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
    "ff0000": "RIP",      # Rouge
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
    Assigne les numéros d'AS croissants: 100, 200, 300...
    """
    drawings = gns3_data.get("topology", {}).get("drawings", [])
    rectangles = []
    as_counter = 100
    
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
            as_counter += 100

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
    Les rectangles colorés (Rouge=RIP, Vert=OSPF) définissent le protocole IGP et l'AS.
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
            
            if r_proto in ["RIP", "OSPF"]:
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
def get_topology(gns3_file, ip_base="2000:1::/64", output_dir=None, output_name="topology.json", loopback_format="simple"):
    """
    Extrait la topologie d'un fichier .gns3 et génère un fichier topology.json
    
    Args:
        gns3_file (str): Chemin vers le fichier .gns3
        ip_base (str): Base pour l'adressage IPv6 (défaut: "2000:1::/64")
        output_dir (str): Répertoire de sortie (défaut: répertoire du script)
        output_name (str): Nom du fichier de sortie (défaut: "topology.json")
    
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
    # format: 2000:1:<AS>:<ID1>:<ID2>::<ID_LOCAL>
    
    base_net_obj = ipaddress.ip_network(ip_base, strict=False)
    base_parts = str(base_net_obj.network_address).split('::')[0]
    
    interfaces_cfg = defaultdict(list)
    networks = defaultdict(set)

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

        if as_a == as_b and as_a != 0:
            # Cas 1 : Intra-AS (Même AS et AS != 0)
            # Format attendu : 2000:1:AS:ID1:ID2::X/80
            # 2000:1::/64 -> base_parts = "2000:1"
            # base_parts (2 blocs) + AS (1 bloc) + ID1 (1 bloc) + ID2 (1 bloc) = 5 blocs
            # Reste 3 blocs pour l'hôte.
            low_id, high_id = sorted((id_a_int, id_b_int))
            # Construction propre sans double "::"
            subnet_prefix_str = f"{base_parts}:{as_a}:{low_id}:{high_id}::"
            subnet_prefix_no_colons = f"{base_parts}:{as_a}:{low_id}:{high_id}:0:0:0"
            subnet_cidr = f"{subnet_prefix_str}/80"
            
            # On utilise ipaddress pour faire l'addition propre
            net = ipaddress.IPv6Network(f"{base_parts}:{as_a}:{low_id}:{high_id}::/80", strict=False)
            ip_a_str = str(net.network_address + id_a_int)
            ip_b_str = str(net.network_address + id_b_int)
            prefix_len = 80

        else:
            # Cas 2 : Inter-AS
            # Format attendu : 2000:1:0:AS1:AS2:ID1:ID2:X/112
            # base_parts (2 blocs) + 0 (1 bloc) + AS1 (1 bloc) + AS2 (1 bloc) + ID1 (1 bloc) + ID2 (1 bloc) = 7 blocs 
            # Il ne reste que 1 bloc pour l'hôte. (Total 8 blocs)
            # DANGER: Si base_parts a déjà "::", il faut faire attention.
            # Supposons base_parts = "2000:1".
            
            low_as, high_as = sorted((as_a, as_b))
            low_id, high_id = sorted((id_a_int, id_b_int))
            
            # On construit explicitement les 7 premiers blocs
            # 2000:1:0:AS1:AS2:ID1:ID2:0
            subnet_str = f"{base_parts}:0:{low_as}:{high_as}:{low_id}:{high_id}:0"
            
            try:
                # On valide que c'est une IP correcte
                base_ip_int = int(ipaddress.IPv6Address(subnet_str))
                ip_a_str = str(ipaddress.IPv6Address(base_ip_int + id_a_int))
                ip_b_str = str(ipaddress.IPv6Address(base_ip_int + id_b_int))
                
                # Pour le CIDR d'affichage qui finit par :: (optionnel mais propre)
                subnet_cidr = f"{base_parts}:0:{low_as}:{high_as}:{low_id}:{high_id}::/112"
            except Exception as e:
                print(f"Erreur génération IP Inter-AS: {e}")
                # Fallback simple
                ip_a_str = f"2001:FFFF:{low_as}:{high_as}::{id_a_int}"
                ip_b_str = f"2001:FFFF:{low_as}:{high_as}::{id_b_int}"
                subnet_cidr = f"2001:FFFF:{low_as}:{high_as}::/64"
            
            prefix_len = 112

        # Configuration pour le routeur A
        interfaces_cfg[a].append({
            "name": a_iface_name,
            "ip": ip_a_str,
            "prefix": prefix_len
        })
        networks[a].add(subnet_cidr)

        # Configuration pour le routeur B
        interfaces_cfg[b].append({
            "name": b_iface_name,
            "ip": ip_b_str,
            "prefix": prefix_len
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


