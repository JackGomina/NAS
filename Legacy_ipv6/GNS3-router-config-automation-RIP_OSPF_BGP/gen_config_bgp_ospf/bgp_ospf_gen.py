#!/usr/bin/env python3
"""
IPv6 BGP Config Generator (Unified iBGP/eBGP)
"""
import json
import os
import sys
from pathlib import Path
from jinja2 import Template
import sys

# Add root directory to sys.path to allow importing utils
sys.path.append(str(Path(__file__).parent.parent))
from utils import get_router_id, get_loopback_ip

def generate_bgp_configs(topology_file, output_dir="configs", options=None):
    if options is None:
        options = {}
    print(f"Loading topology from {topology_file}...")
    with open(topology_file, 'r') as f:
        topo = json.load(f)

    # Prepare data structures
    routers = {r["name"]: r for r in topo["routers"]}
    links = topo.get("links", [])
    loopback_fmt = topo.get("loopback_format", "simple")
    
    # Enrich router data with deduced fields
    for name, r in routers.items():
        r["router_id"] = get_router_id(name)
        r["loopback_ip"] = get_loopback_ip(name, fmt=loopback_fmt, as_number=r.get("as_number"))
        # Default ASN if missing (fallback for safety)
        if "as_number" not in r or r["as_number"] is None:
            r["as_number"] = 65000 
        r["bgp_neighbors"] = []
        # Initialize OSPF enabled on all interfaces by default (will be disabled for eBGP links)
        for iface in r.get("interfaces", []):
            iface["ospf_enabled"] = True
            # Apply OSPF Costs if defined in options
            cost_map = options.get("ospf_costs", {}).get(name, {})
            if iface["name"] in cost_map:
                iface["ospf_cost"] = cost_map[iface["name"]]

    # Infer neighbors
    # 1. Process Links for eBGP (Direct Physical Peering) and OSPF disabling
    for link in links:
        a_name = link["a"]
        b_name = link["b"]
        
        if a_name not in routers or b_name not in routers:
            continue

        rA = routers[a_name]
        rB = routers[b_name]
        
        asA = rA["as_number"]
        asB = rB["as_number"]
        
        # Get interface IPs for the link
        def get_ip(router_data, iface_name):
            for i in router_data["interfaces"]:
                if i["name"] == iface_name:
                    return i["ip"]
            return None

        ipA = get_ip(rA, link["a_iface"])
        ipB = get_ip(rB, link["b_iface"])

        if not ipA or not ipB:
            print(f"Warning: Could not find IP for link {a_name}<->{b_name}")
            continue

        # eBGP Logic: Different AS -> Peer physically
        if asA != asB:
            # We only care about configuring the OSPF router side
            
            # Setup side A if it is OSPF
            if rA.get("protocol") == "OSPF":
                rA["bgp_neighbors"].append({
                    "name": b_name,
                    "ip": ipB,
                    "asn": asB,
                    "is_ibgp": False
                })
                # Disable OSPF on this interface (eBGP link)
                for iface in rA["interfaces"]:
                    if iface["name"] == link["a_iface"]:
                        iface["ospf_enabled"] = False
            
            # Setup side B if it is OSPF
            if rB.get("protocol") == "OSPF":
                rB["bgp_neighbors"].append({
                    "name": a_name,
                    "ip": ipA,
                    "asn": asA,
                    "is_ibgp": False
                })
                # Disable OSPF on this interface (eBGP link)
                for iface in rB["interfaces"]:
                    if iface["name"] == link["b_iface"]:
                        iface["ospf_enabled"] = False

    # 2. Process Full Mesh for iBGP (Loopback Peering) within same AS for OSPF routers
    ospf_router_names = [n for n, r in routers.items() if r.get("protocol") == "OSPF"]
    
    for i in range(len(ospf_router_names)):
        for j in range(i + 1, len(ospf_router_names)):
            nameA = ospf_router_names[i]
            nameB = ospf_router_names[j]
            
            rA = routers[nameA]
            rB = routers[nameB]
            
            # Only connect if same AS (iBGP)
            if rA["as_number"] == rB["as_number"]:
                # iBGP Peering A -> B
                rA["bgp_neighbors"].append({
                    "name": nameB,
                    "ip": rB["loopback_ip"],
                    "asn": rB["as_number"],
                    "is_ibgp": True
                })
                # iBGP Peering B -> A
                rB["bgp_neighbors"].append({
                    "name": nameA,
                    "ip": rA["loopback_ip"],
                    "asn": rA["as_number"],
                    "is_ibgp": True
                })

    # Generate Configs
    out_path = Path(output_dir)
    os.makedirs(out_path, exist_ok=True)
    
    # Use template from local directory
    template_path = Path(__file__).parent / "router_bgp_ospf.j2"
        
    with open(template_path) as f:
        template = Template(f.read())
        
    print(f"Generating BGP+OSPF configs in {out_path}...")
    
    relations = options.get("bgp_relations", {})

    for name in ospf_router_names:
        r = routers[name]
        
        # Enrich neighbors with relationship data
        # Default relationship is 'peer'
        neighbors_list = []
        # Unique-ification logic first
        seen_ips = set()
        for n in r["bgp_neighbors"]:
             if n["ip"] in seen_ips:
                 continue
             seen_ips.add(n["ip"])
             
             # Determine relationship
             # Key format in relations dict: "AS1-AS2" (sorted) or check both directions
             local_as = str(r["as_number"])
             remote_as = str(n["asn"])
             
             rel_type = "peer" # Default
             
             if not n["is_ibgp"]:
                 # Check explicit configuration
                 # User might have stored "100-200" = "customer" (meaning 100 is customer of 200 ??? No, usually context dependent)
                 # Let's assume input was "AS A is RELATION of AS B"
                 # We need to find the entry matching our pair
                 
                 # Check direct key "local-remote"
                 key1 = f"{local_as}-{remote_as}"
                 if key1 in relations:
                     # If config says "100-200": "customer" -> 100 IS CUSTOMER OF 200
                     # So for us (100), the remote (200) is our PROVIDER
                     val = relations[key1]
                     if val == "customer": rel_type = "provider"
                     elif val == "provider": rel_type = "customer"
                     else: rel_type = "peer"
                 
                 # Check reverse key "remote-local"
                 key2 = f"{remote_as}-{local_as}"
                 if key2 in relations:
                     # If config says "200-100": "customer" -> 200 IS CUSTOMER OF 100
                     # So for us (100), the remote (200) is our CUSTOMER
                     val = relations[key2]
                     rel_type = val # Direct mapping in this direction
             
             n["relationship"] = rel_type
             neighbors_list.append(n)
        
        config = template.render(
            router_name=name,
            router_id=r["router_id"],
            loopback_ip=r["loopback_ip"],
            asn=r["as_number"],
            interfaces=r["interfaces"],
            neighbors=neighbors_list,
            networks=r.get("networks", []),
            options=options
        )
        
        with open(out_path / f"{name}.cfg", "w") as f:
            f.write(config)
        print(f"  Saved {name}.cfg ({'iBGP' if any(n['is_ibgp'] for n in neighbors_list) else ''}{' eBGP' if any(not n['is_ibgp'] for n in neighbors_list) else ''})")

if __name__ == "__main__":
    topo_file = Path(__file__).parent / "topology.json"
    if not topo_file.exists():
        print("Error: topology.json not found in requested directory.")
        sys.exit(1)
        
    generate_bgp_configs(topo_file)
