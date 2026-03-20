import json
import ipaddress
from pathlib import Path
from collections import defaultdict
from utils import get_router_number
import re
import sys
import os

# Add parent directory to path to allow importing utils
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


# --- MAPPING COULEUR -> PROTOCOLE/AS ---
COLOR_TO_PROTOCOL = {
    "00ff00": "OSPF"     # Vert
}

# --- FONCTION UTILITAIRE : Traduction GNS3 -> Cisco ---
def get_interface_name(adapter, port):
    """
    Traduit les numéros de port GNS3 en noms d'interfaces Cisco IOS.
    Valable ici pour routeurs c7200.
    """
    # Sur un c7200, l'adaptateur 0 est le FastEthernet intégré
    if adapter == 0:
        return f"FastEthernet{adapter}/{port}"
    # Les adaptateurs suivants (1, 2...) sont des modules GigabitEthernet
    else:
        return f"GigabitEthernet{adapter}/{port}"


def extract_drawings(gns3_data):
    """
    Extrait les rectangles de dessins du projet GNS3.
    Retourne une liste de dictionnaires avec: x, y, width, height, color, protocol, as_number
    Assigne les numéros d'AS croissants: 1, 2, 3...
    """
    drawings = gns3_data.get("topology", {}).get("drawings", [])
    rectangles = []
    as_counter = 1
    
    for drawing in drawings:
        svg = drawing.get("svg", "")
        
        # Extraire les dimensions du SVG
        width_match = re.search(r'width="(\d+)"', svg)
        height_match = re.search(r'height="(\d+)"', svg)
        
        # Extraire la couleur (stroke ou fill)
        # Supporte minuscules et majuscules pour le code hexadécimal
        color_match = re.search(r'stroke="#([0-9a-fA-F]+)"', svg)
        if not color_match:
            # Si l'utilisateur a rempli le rectangle au lieu de la bordure
            color_match = re.search(r'fill="#([0-9a-fA-F]+)"', svg)
        
        if width_match and height_match:
            width = int(width_match.group(1))
            height = int(height_match.group(1))
            color = color_match.group(1) if color_match else "000000"
            protocol = COLOR_TO_PROTOCOL.get(color.lower(), "UNKNOWN")
            
            rect = {
                "x": drawing["x"],
                "y": drawing["y"],
                "width": width,
                "height": height,
                "color": color,
                "protocol": protocol
            }
            
            rect["as_number"] = as_counter
            as_counter += 1

            rectangles.append(rect)
    
    return rectangles


def is_point_in_rectangle(px, py, rect):
    """
    Vérifie si un point (px, py) est dans un rectangle.
    Les routeurs sont considérés comme des points.
    """
    x_min = rect["x"]
    x_max = rect["x"] + rect["width"]
    y_min = rect["y"]
    y_max = rect["y"] + rect["height"]
    
    return x_min <= px <= x_max and y_min <= py <= y_max


def assign_routers_to_as(nodes_data, rectangles):
    """
    Associe chaque routeur à un AS et un protocole selon les zones dessinées dans GNS3.
    Les rectangles colorés (Vert=OSPF) définissent le protocole IGP et l'AS.
    """
    router_to_as = {}
    
    for node in nodes_data:
        # On ne traite que les routeurs (type "dynamips")
        if node.get("node_type") != "dynamips":
            continue

        name = node["name"]
        x, y = node["x"], node["y"]
        
        # Trouver les rectangles contenant ce routeur
        containing_rects = [r for r in rectangles if is_point_in_rectangle(x, y, r)]
        
        # Valeurs par défaut
        protocol = "UNKNOWN"
        as_number = None
        has_ebgp = False
        
        # Analyse des rectangles
        for rect in containing_rects:
            r_proto = rect.get("protocol")
            
            if r_proto in ["OSPF"]:
                # On privilégie le premier protocole trouvé si aucun n'est encore défini
                if protocol == "UNKNOWN":
                    protocol = r_proto
                    as_number = rect.get("as_number")
        
        router_to_as[name] = {
            "protocol": protocol,
            "as_number": as_number,
            "ebgp": has_ebgp
        }
    
    return router_to_as


# --- FONCTION PRINCIPALE ---
def get_topology(gns3_file, ip_base="10.0.0.0/8", output_dir=None, output_name="topology.json", loopback_format="simple", routing_strategy="grand_reseaux"):
    """
    Extrait la topologie d'un fichier .gns3 et génère un fichier topology.json
    
    Args:
        gns3_file (str): Chemin vers le fichier .gns3
        ip_base (str): Base pour l'adressage ip (défaut: "10.0.0.0/8")
        output_dir (str): Répertoire de sortie (défaut: répertoire du script)
        output_name (str): Nom du fichier de sortie (défaut: "topology.json")
        loopback_format (str): Format des loopbacks
        routing_strategy (str): Stratégie d'adressage IP ("grand_reseaux" ou "simple")
    
    Returns:
        dict: Les données de topologie extraites
    """
    
    # Configuration des chemins
    if output_dir is None:
        output_dir = Path(__file__).parent.absolute()
    else:
        output_dir = Path(output_dir)
    
    gns3_path = Path(gns3_file)
    
    print(f"Chemin GNS3 utilisé : {gns3_path}")
    print(f"Fichier existe ? {gns3_path.exists()}")

    # --- 1. CHARGEMENT DE LA TOPOLOGIE DEPUIS GNS3 ---
    try:
        with open(gns3_path, "r") as f:
            gns3_data = json.load(f)
    except FileNotFoundError:
        print(f"Erreur : Le fichier '{gns3_path}' est introuvable.")
        exit(1)

    # Création d'un dictionnaire pour retrouver le nom d'un routeur via son ID unique
    id_to_name = {}
    routers_list = []

    # GNS3 stocke les nœuds directement sous la racine ou sous "topology"
    nodes_data = gns3_data.get("topology", {}).get("nodes", gns3_data.get("nodes", []))
    links_data = gns3_data.get("topology", {}).get("links", gns3_data.get("links", []))

    print(f"Nombre de nœuds trouvés : {len(nodes_data)}")
    print(f"Nombre de liens trouvés : {len(links_data)}")

    # Extraire les rectangles (AS/groupes)
    rectangles = extract_drawings(gns3_data)
    print(f"Nombre de rectangles détectés : {len(rectangles)}")
    for i, rect in enumerate(rectangles):
        print(f"  AS{rect['as_number']}: {rect['protocol']} ({rect['color']}) à ({rect['x']}, {rect['y']}), taille {rect['width']}x{rect['height']}")
    
    # Assigner les routeurs aux AS
    router_to_as = assign_routers_to_as(nodes_data, rectangles)
    print(f"Attribution routeurs -> AS:")
    for router, as_info in router_to_as.items():
        as_num = as_info.get("as_number", "?")
        protocol = as_info.get("protocol", "?")
        print(f"  {router}: AS{as_num} ({protocol})")

    for node in nodes_data:
        name = node["name"]
        node_id = node["node_id"]
        id_to_name[node_id] = name
        routers_list.append(name)

    print(f"Topologie détectée : {len(routers_list)} routeurs ({', '.join(routers_list)})")

    # --- 2. EXTRACTION DES LIENS ET CONVERSION ---
    links = []

    for link in links_data:
        node_a_data = link["nodes"][0]
        node_b_data = link["nodes"][1]
        
        id_a = node_a_data["node_id"]
        id_b = node_b_data["node_id"]

        # On vérifie que les deux bouts sont bien des routeurs connus
        if id_a in id_to_name and id_b in id_to_name:
            links.append({
                "a": id_to_name[id_a],
                "a_iface": get_interface_name(node_a_data["adapter_number"], node_a_data["port_number"]),
                "b": id_to_name[id_b],
                "b_iface": get_interface_name(node_b_data["adapter_number"], node_b_data["port_number"])
            })

    print(f"Liens détectés : {len(links)} liens actifs.")

    # --- 2b. DETECTION eBGP PAR LIENS INTER-AS ---
    # Si deux routeurs liés n'appartiennent pas au même AS, ils font de l'eBGP
    for link in links:
        a = link["a"]
        b = link["b"]
        a_info = router_to_as.get(a, {"as_number": None, "ebgp": False})
        b_info = router_to_as.get(b, {"as_number": None, "ebgp": False})

        if (
            a_info.get("as_number") is not None
            and b_info.get("as_number") is not None
            and a_info.get("as_number") != b_info.get("as_number")
        ):
            a_info["ebgp"] = True
            b_info["ebgp"] = True
            router_to_as[a] = a_info
            router_to_as[b] = b_info

    # --- 3. LOGIQUE D'ADRESSAGE MNÉMOTECHNIQUE AVEC AS ---
    # IPv4 addressing: 
    # Intra-AS: base.{AS}.{link_id}.{ID_LOCAL}/24
    # Inter-AS: 192.168.{AS_MIN}.{AS_MAX}/24 (last octet is router ID)
    
    # Optional parsing of base IP, but we primarily use it for the first octet of Intra-AS
    try:
        base_net_obj = ipaddress.IPv4Network(ip_base, strict=False)
        base_first_octet = str(base_net_obj.network_address).split('.')[0]
    except Exception:
        base_first_octet = "10"
    
    interfaces_cfg = defaultdict(list)
    networks = defaultdict(set)

    # Counters to generate unique subnets
    intra_as_link_counters = defaultdict(int)
    inter_as_link_counters = defaultdict(int)

    def parse_iface_id(iface_name):
        nums = re.findall(r'\d+', iface_name)
        return int(nums[0]) if nums else 1

    # 3a. IDs
    node_to_id = {}
    for name in routers_list:
        node_to_id[name] = get_router_number(name)

    # 3b. Links
    for link in links:
        a, a_iface_name = link["a"], link["a_iface"]
        b, b_iface_name = link["b"], link["b_iface"]

        id_a_int = node_to_id[a]
        id_b_int = node_to_id[b]
        
        info_a = router_to_as.get(a)
        info_b = router_to_as.get(b)
        
        as_a = int(info_a['as_number']) if info_a and info_a.get('as_number') else 0
        as_b = int(info_b['as_number']) if info_b and info_b.get('as_number') else 0

        low_id, high_id = sorted((id_a_int, id_b_int))

        if as_a == as_b and as_a != 0:
            # Cas 1 : Intra-AS (Même AS et AS != 0)
            
            as_octet = as_a % 255 if as_a > 255 else as_a
            
            if routing_strategy == "grand_reseaux":
                intra_as_link_counters[as_octet] += 1
                # On commence à 0 pour le premier lien
                link_id_0 = intra_as_link_counters[as_octet] - 1
                
                # 1 lien = 1 sous réseau /30 (4 IP, donc intervalle de 4)
                subnet_int = link_id_0 * 4
                octet3 = subnet_int // 256
                octet4 = subnet_int % 256
                
                ip_a_val = subnet_int + 1
                ip_b_val = subnet_int + 2
                
                subnet_prefix = f"10.{as_octet}.{octet3}.{octet4}"
                subnet_cidr = f"{subnet_prefix}/30"
                
                ip_a_str = f"10.{as_octet}.{ip_a_val // 256}.{ip_a_val % 256}"
                ip_b_str = f"10.{as_octet}.{ip_b_val // 256}.{ip_b_val % 256}"
                prefix_len = 30
                mask_str = "255.255.255.252"
            elif routing_strategy == "simple":
                iface_a_id = parse_iface_id(a_iface_name)
                iface_b_id = parse_iface_id(b_iface_name)
                
                ip_a_str = f"10.{as_octet}.{id_a_int}.{iface_a_id}"
                ip_b_str = f"10.{as_octet}.{id_b_int}.{iface_b_id}"
                
                # Le vrai masque est /24 mais on assigne selon l'interface pour le 'simple'
                subnet_prefix = f"10.{as_octet}.0.0"
                subnet_cidr = f"{subnet_prefix}/24"

            prefix_len = 24 if routing_strategy == "simple" else 30
            if routing_strategy == "simple":
                mask_str = "255.255.255.0"

        else:
            # Cas 2 : Inter-AS (eBGP ou lien non assigné)
            # Format attendu : 192.168.LINK_COUNTER.X/24
            
            low_as, high_as = sorted((as_a, as_b))
            
            # Generate unique Inter-AS subnet
            pair_key = f"{low_as}_{high_as}"
            inter_as_link_counters[pair_key] += 1
            idx = inter_as_link_counters[pair_key]
            
            # Let's cleanly assign 192.168.X.Y where X is a unique inter-AS link id
            # Since we could have multiple links between same pairs, we hash AS pair into 3rd octet
            as_oct1 = low_as % 255 if low_as > 255 else low_as
            as_oct2 = high_as % 255 if high_as > 255 else high_as
            
            # A unique 3rd octet combining AS and link index
            third_octet = (as_oct1 + as_oct2 + idx) % 254 + 1
            
            subnet_prefix = f"192.168.{third_octet}.0"
            subnet_cidr = f"{subnet_prefix}/24"
            
            ip_a_str = f"192.168.{third_octet}.{id_a_int}"
            ip_b_str = f"192.168.{third_octet}.{id_b_int}"
            prefix_len = 24
            mask_str = "255.255.255.0"

        # Configuration pour le routeur A
        interfaces_cfg[a].append({
            "name": a_iface_name,
            "ip": ip_a_str,
            "prefix": prefix_len,
            "mask": mask_str
        })
        networks[a].add(subnet_cidr)

        # Configuration pour le routeur B
        interfaces_cfg[b].append({
            "name": b_iface_name,
            "ip": ip_b_str,
            "prefix": prefix_len,
            "mask": mask_str
        })
        networks[b].add(subnet_cidr)

    # --- 3b. EXPORT TOPOLOGY.JSON ---
    topology_data = {
        "ip_base": ip_base,
        "loopback_format": loopback_format,
        "routers": [],
        "links": []
    }

    # Ajouter les routeurs avec leur protocole et AS assignés
    for router_name in routers_list:
        as_info = router_to_as.get(router_name, {"protocol": "UNKNOWN", "as_number": None, "ebgp": False})
        router_entry = {
            "name": router_name,
            "protocol": as_info.get("protocol"),
            "as_number": as_info.get("as_number"),
            "ebgp": as_info.get("ebgp", False),
            "interfaces": interfaces_cfg.get(router_name, []),
            "networks": sorted(networks.get(router_name, []))
        }
        topology_data["routers"].append(router_entry)

    # Ajouter les liens
    for link in links:
        topology_data["links"].append({
            "a": link["a"],
            "a_iface": link["a_iface"],
            "b": link["b"],
            "b_iface": link["b_iface"]
        })

    # Sauvegarder topology.json
    topology_file = output_dir / output_name
    with open(topology_file, "w", encoding="utf-8") as f:
        json.dump(topology_data, f, indent=2, ensure_ascii=False)
    print(f"Topologie exportée : {topology_file}")

    print(f"\nTerminé ! La topologie a été extraite depuis {gns3_path}")
    return topology_data


