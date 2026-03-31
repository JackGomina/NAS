#!/usr/bin/env python3
"""
Addressing config generator.
Generate Cisco configs with only hostname, Loopback0 and physical interfaces.
"""
import json
import os
import sys
from pathlib import Path
from jinja2 import Template

# Add root directory to sys.path to allow importing utils
sys.path.append(str(Path(__file__).parent.parent))
from utils import get_loopback_ip


def _iface_ip(router, iface_name):
    for iface in router.get("interfaces", []):
        if iface.get("name") == iface_name:
            return iface.get("ip")
    return None


def _vrf_name_from_color(customer_color):
    color = str(customer_color or "#cccccc").lower().lstrip("#")
    return f"CUST_{color}"


def generate_bgp_configs(topology_file, output_dir="configs", options=None):
    if options is None:
        options = {}

    print(f"Loading topology from {topology_file}...")
    with open(topology_file, "r", encoding="utf-8") as f:
        topo = json.load(f)

    routers = topo.get("routers", [])
    links = topo.get("links", [])

    provider_roles = {"P", "PE", "RR"}
    router_by_name = {r.get("name"): r for r in routers}

    # Build iBGP vpnv4 topology per provider AS with RR design.
    rr_peering = {r.get("name"): [] for r in routers}
    provider_as_groups = {}
    for r in routers:
        role = str(r.get("role", "UNKNOWN")).upper()
        as_type = str(r.get("as_type", "unknown")).lower()
        asn = r.get("as_number")
        if as_type != "provider" or asn is None:
            continue
        provider_as_groups.setdefault(int(asn), []).append(r)

    for asn, members in provider_as_groups.items():
        rrs = [r for r in members if str(r.get("role", "UNKNOWN")).upper() == "RR"]
        pes = [r for r in members if str(r.get("role", "UNKNOWN")).upper() == "PE"]

        # RR <-> RR mesh
        for i in range(len(rrs)):
            for j in range(i + 1, len(rrs)):
                rr1 = rrs[i]
                rr2 = rrs[j]
                rr_peering[rr1.get("name")].append({
                    "neighbor_name": rr2.get("name"),
                    "neighbor_ip": rr2.get("loopback_ip") or get_loopback_ip(rr2.get("name"), as_number=rr2.get("as_number")),
                    "remote_as": asn,
                    "is_rr_client": False
                })
                rr_peering[rr2.get("name")].append({
                    "neighbor_name": rr1.get("name"),
                    "neighbor_ip": rr1.get("loopback_ip") or get_loopback_ip(rr1.get("name"), as_number=rr1.get("as_number")),
                    "remote_as": asn,
                    "is_rr_client": False
                })

        # PE clients -> all RRs, and RRs -> PE clients
        for pe in pes:
            for rr in rrs:
                rr_peering[pe.get("name")].append({
                    "neighbor_name": rr.get("name"),
                    "neighbor_ip": rr.get("loopback_ip") or get_loopback_ip(rr.get("name"), as_number=rr.get("as_number")),
                    "remote_as": asn,
                    "is_rr_client": False
                })
                rr_peering[rr.get("name")].append({
                    "neighbor_name": pe.get("name"),
                    "neighbor_ip": pe.get("loopback_ip") or get_loopback_ip(pe.get("name"), as_number=pe.get("as_number")),
                    "remote_as": asn,
                    "is_rr_client": True
                })

        # Fallback sans RR: full-mesh PE<->PE
        if not rrs:
            for i in range(len(pes)):
                for j in range(i + 1, len(pes)):
                    pe1 = pes[i]
                    pe2 = pes[j]
                    rr_peering[pe1.get("name")].append({
                        "neighbor_name": pe2.get("name"),
                        "neighbor_ip": pe2.get("loopback_ip") or get_loopback_ip(pe2.get("name"), as_number=pe2.get("as_number")),
                        "remote_as": asn,
                        "is_rr_client": False
                    })
                    rr_peering[pe2.get("name")].append({
                        "neighbor_name": pe1.get("name"),
                        "neighbor_ip": pe1.get("loopback_ip") or get_loopback_ip(pe1.get("name"), as_number=pe1.get("as_number")),
                        "remote_as": asn,
                        "is_rr_client": False
                    })

    # Build per-router set of interfaces that belong to provider-provider links.
    core_ifaces = {}
    for link in links:
        a = link.get("a")
        b = link.get("b")
        a_iface = link.get("a_iface")
        b_iface = link.get("b_iface")

        role_a = str(router_by_name.get(a, {}).get("role", "UNKNOWN")).upper()
        role_b = str(router_by_name.get(b, {}).get("role", "UNKNOWN")).upper()

        if role_a in provider_roles and role_b in provider_roles:
            core_ifaces.setdefault(a, set()).add(a_iface)
            core_ifaces.setdefault(b, set()).add(b_iface)

    # Build customer/VRF catalog.
    customer_catalog = {}
    for r in routers:
        if str(r.get("as_type", "unknown")).lower() != "customer":
            continue
        customer_id = r.get("customer_id")
        if customer_id is None:
            continue
        if customer_id not in customer_catalog:
            customer_catalog[customer_id] = {
                "customer_id": customer_id,
                "customer_as": r.get("as_number"),
                "customer_color": r.get("customer_color") or "#cccccc",
                "vrf_name": _vrf_name_from_color(r.get("customer_color")),
            }

    # Build PE-CE relationships and per-PE VRF definitions.
    pe_ce_neighbors = {}
    pe_vrf_defs = {}
    pe_iface_vrf = {}
    ce_bgp_neighbors = {}

    for link in links:
        a = link.get("a")
        b = link.get("b")
        role_a = str(router_by_name.get(a, {}).get("role", "UNKNOWN")).upper()
        role_b = str(router_by_name.get(b, {}).get("role", "UNKNOWN")).upper()

        if role_a == "PE" and role_b == "CE":
            pe_name, pe_iface = a, link.get("a_iface")
            ce_name, ce_iface = b, link.get("b_iface")
        elif role_b == "PE" and role_a == "CE":
            pe_name, pe_iface = b, link.get("b_iface")
            ce_name, ce_iface = a, link.get("a_iface")
        else:
            continue

        pe_router = router_by_name.get(pe_name, {})
        ce_router = router_by_name.get(ce_name, {})
        customer_id = ce_router.get("customer_id")
        cust = customer_catalog.get(customer_id)
        if cust is None:
            continue

        vrf_name = cust["vrf_name"]
        pe_asn = pe_router.get("as_number")
        ce_asn = ce_router.get("as_number")
        ce_ip = _iface_ip(ce_router, ce_iface)
        pe_ip = _iface_ip(pe_router, pe_iface)

        pe_iface_vrf[(pe_name, pe_iface)] = vrf_name

        pe_vrf_defs.setdefault(pe_name, {})
        pe_vrf_defs[pe_name][vrf_name] = {
            "name": vrf_name,
            "rd": f"{pe_asn}:{customer_id}",
            "rt": f"{pe_asn}:{customer_id}",
        }

        if ce_ip:
            pe_ce_neighbors.setdefault(pe_name, []).append({
                "vrf_name": vrf_name,
                "ce_name": ce_name,
                "ce_ip": ce_ip,
                "ce_asn": ce_asn,
            })

        if pe_ip:
            ce_bgp_neighbors.setdefault(ce_name, []).append({
                "pe_name": pe_name,
                "pe_ip": pe_ip,
                "pe_asn": pe_asn,
            })

    out_path = Path(output_dir)
    os.makedirs(out_path, exist_ok=True)

    template_path = Path(__file__).parent / "router_bgp_ospf.j2"
    with open(template_path, "r", encoding="utf-8") as f:
        template = Template(f.read())

    print(f"Generating addressing configs in {out_path}...")

    for router in routers:
        name = router["name"]
        loopback_ip = router.get("loopback_ip") or get_loopback_ip(name, as_number=router.get("as_number"))
        role = str(router.get("role", "UNKNOWN")).upper()
        enable_ospf = role in {"P", "PE", "RR"}
        enable_mpls = role in {"P", "PE", "RR"}

        interfaces = []
        for iface in router.get("interfaces", []):
            iface_copy = dict(iface)
            iface_name = iface.get("name")
            iface_copy["ospf_enabled"] = iface_name in core_ifaces.get(name, set())
            iface_copy["mpls_enabled"] = iface_name in core_ifaces.get(name, set())
            iface_copy["vrf_name"] = pe_iface_vrf.get((name, iface_name))
            interfaces.append(iface_copy)

        bgp_ctx = {
            "enabled": False,
            "mode": "none",
            "asn": router.get("as_number"),
            "router_id": loopback_ip,
            "neighbors": [],
            "vrf_neighbors": [],
            "ce_neighbors": [],
            "ce_networks": [],
        }

        if role in {"PE", "RR"}:
            bgp_ctx.update({
                "enabled": len(rr_peering.get(name, [])) > 0 or len(pe_ce_neighbors.get(name, [])) > 0,
                "mode": "provider",
                "neighbors": rr_peering.get(name, []),
                "vrf_neighbors": pe_ce_neighbors.get(name, []),
            })
        elif role == "CE":
            ce_networks = []
            if loopback_ip:
                ce_networks.append({"prefix": loopback_ip, "mask": "255.255.255.255"})

            bgp_ctx.update({
                "enabled": len(ce_bgp_neighbors.get(name, [])) > 0,
                "mode": "ce",
                "neighbors": ce_bgp_neighbors.get(name, []),
                "ce_neighbors": ce_bgp_neighbors.get(name, []),
                "ce_networks": ce_networks,
            })

        vrfs = sorted(pe_vrf_defs.get(name, {}).values(), key=lambda v: v["name"])

        config = template.render(
            router_name=name,
            loopback_ip=loopback_ip,
            interfaces=interfaces,
            router_id=loopback_ip,
            enable_ospf=enable_ospf,
            ospf_process_id=1,
            enable_mpls=enable_mpls,
            vrfs=vrfs,
            bgp=bgp_ctx
        )

        with open(out_path / f"{name}.cfg", "w", encoding="utf-8") as f:
            f.write(config)
        print(f"  Saved {name}.cfg")


if __name__ == "__main__":
    topo_file = Path(__file__).parent / "topology.json"
    if not topo_file.exists():
        print("Error: topology.json not found in requested directory.")
        sys.exit(1)

    generate_bgp_configs(topo_file)
