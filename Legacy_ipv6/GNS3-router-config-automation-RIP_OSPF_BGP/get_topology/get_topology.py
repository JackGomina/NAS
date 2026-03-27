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
PROVIDER_RECT_COLORS = {"000000"}

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
    - Rectangles verts: zones AS (numérotées)
    - Rectangles noirs: zones AS provider (auto-suffisantes)
    """
    drawings = gns3_data.get("topology", {}).get("drawings", [])
    as_rectangles = []
    provider_rectangles = []
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
            color_lower = color.lower()

            rect = {
                "x": drawing["x"],
                "y": drawing["y"],
                "width": width,
                "height": height,
                "color": color
            }

            if color_lower in COLOR_TO_PROTOCOL:
                rect["protocol"] = COLOR_TO_PROTOCOL[color_lower]
                rect["as_number"] = as_counter
                rect["is_provider_zone"] = False
                as_counter += 1
                as_rectangles.append(rect)
            elif color_lower in PROVIDER_RECT_COLORS:
                # Un rectangle noir est aussi une zone AS OSPF utilisable seule.
                rect["protocol"] = "OSPF"
                rect["as_number"] = as_counter
                rect["is_provider_zone"] = True
                as_counter += 1
                as_rectangles.append(rect)
                provider_rectangles.append(rect)

    return {
        "as_rectangles": as_rectangles,
        "provider_rectangles": provider_rectangles
    }


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

        # Priorité aux zones provider (rectangles noirs) si chevauchement.
        provider_containing = [r for r in containing_rects if r.get("is_provider_zone")]
        if provider_containing:
            containing_rects = provider_containing
        
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


def find_routers_in_rectangles(nodes_data, rectangles):
    routers = set()
    for node in nodes_data:
        if node.get("node_type") != "dynamips":
            continue

        x, y = node["x"], node["y"]
        if any(is_point_in_rectangle(x, y, rect) for rect in rectangles):
            routers.add(node["name"])
    return routers


# --- FONCTION PRINCIPALE ---
def get_topology(
    gns3_file,
    ip_base="10.0.0.0/8",
    output_dir=None,
    output_name="topology.json",
    loopback_format="simple",
    routing_strategy="grand_reseaux",
    as_prefixes=None,
    role_overrides=None
):
    """
    Extrait la topologie d'un fichier .gns3 et génère un fichier topology.json
    
    Args:
        gns3_file (str): Chemin vers le fichier .gns3
        ip_base (str): Base pour l'adressage ip (défaut: "10.0.0.0/8")
        output_dir (str): Répertoire de sortie (défaut: répertoire du script)
        output_name (str): Nom du fichier de sortie (défaut: "topology.json")
        loopback_format (str): Format des loopbacks
        routing_strategy (str): Paramètre conservé pour compatibilité (non utilisé)
                as_prefixes (dict): Mapping des pools AS, ex:
                        {
                            "1": {
                                "links": {"prefix": "172.16.0.0", "prefix_len": 24},
                                "loopbacks": {"prefix": "10.255.0.0", "prefix_len": 24}
                            }
                        }
    
    Returns:
        dict: Les données de topologie extraites
    """
    
    # Valeurs par défaut
    if as_prefixes is None:
        as_prefixes = {}
    if role_overrides is None:
        role_overrides = {}

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
    drawings_info = extract_drawings(gns3_data)
    as_rectangles = drawings_info["as_rectangles"]
    provider_rectangles = drawings_info["provider_rectangles"]

    print(f"Nombre de rectangles AS détectés : {len(as_rectangles)}")
    for i, rect in enumerate(as_rectangles):
        print(f"  AS{rect['as_number']}: {rect['protocol']} ({rect['color']}) à ({rect['x']}, {rect['y']}), taille {rect['width']}x{rect['height']}")
    print(f"Nombre de rectangles provider détectés : {len(provider_rectangles)}")

    # Assigner les routeurs aux AS
    router_to_as = assign_routers_to_as(nodes_data, as_rectangles)
    provider_routers = find_routers_in_rectangles(nodes_data, provider_rectangles)

    for node in nodes_data:
        if node.get("node_type") != "dynamips":
            continue

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

    # --- 2c. DETECTION DES TYPES D'AS ET ROLES (PE/P/CE/C) ---
    as_type_by_number = {}
    for router_name, as_info in router_to_as.items():
        asn = as_info.get("as_number")
        if asn is None:
            continue
        as_key = str(asn)
        if as_key not in as_type_by_number:
            as_type_by_number[as_key] = "customer"
        if router_name in provider_routers:
            as_type_by_number[as_key] = "provider"

    border_routers = set()
    for link in links:
        a = link["a"]
        b = link["b"]
        a_as = router_to_as.get(a, {}).get("as_number")
        b_as = router_to_as.get(b, {}).get("as_number")
        if a_as is not None and b_as is not None and a_as != b_as:
            border_routers.add(a)
            border_routers.add(b)

    for router_name, as_info in router_to_as.items():
        asn = as_info.get("as_number")
        if asn is None:
            as_info["as_type"] = "unknown"
            as_info["role"] = "UNKNOWN"
            router_to_as[router_name] = as_info
            continue

        as_type = as_type_by_number.get(str(asn), "customer")
        is_border = router_name in border_routers

        if is_border and as_type == "provider":
            role = "PE"
        elif not is_border and as_type == "provider":
            role = "P"
        elif is_border and as_type == "customer":
            role = "CE"
        else:
            role = "C"

        # Critère auto RR: routeur provider dont le nom contient "RR".
        # Exemples: RR1, CORE-RR2, RRX.
        if as_type == "provider" and "RR" in str(router_name).upper():
            role = "RR"

        as_info["as_type"] = as_type
        as_info["role"] = role
        router_to_as[router_name] = as_info

    allowed_roles = {"PE", "P", "RR", "CE", "C", "UNKNOWN"}
    for router_name, forced_role in role_overrides.items():
        if router_name not in router_to_as:
            continue
        forced = str(forced_role).upper()
        if forced in allowed_roles:
            router_to_as[router_name]["role"] = forced

    print(f"Attribution routeurs -> AS:")
    for router, as_info in router_to_as.items():
        as_num = as_info.get("as_number", "?")
        protocol = as_info.get("protocol", "?")
        as_type = as_info.get("as_type", "unknown")
        role = as_info.get("role", "UNKNOWN")
        print(f"  {router}: AS{as_num} ({protocol}) type={as_type} role={role}")

    # --- 3. LOGIQUE D'ADRESSAGE ---
    # Tous les liens sont alloués en /30.
    # - Lien intra-AS: /30 dans le pool du préfixe de l'AS.
    # - Lien inter-AS: /30 dans un pool inter-AS dédié.

    interfaces_cfg = defaultdict(list)
    networks = defaultdict(set)

    def parse_as_pool(as_number, pool_kind):
        as_key = str(as_number)
        cfg = as_prefixes.get(as_key, {})

        # Backward compatibility: ancien format plat {prefix, prefix_len}
        if "prefix" in cfg:
            raw_prefix = cfg.get("prefix")
            raw_len = cfg.get("prefix_len", 24)
        else:
            pool_cfg = cfg.get(pool_kind, {})
            raw_prefix = pool_cfg.get("prefix")
            raw_len = pool_cfg.get("prefix_len", 24)

        try:
            plen = int(raw_len)
        except Exception:
            plen = 24

        if plen != 24:
            raise ValueError(f"Masque invalide pour AS{as_key} ({pool_kind}): /{plen} (attendu: /24)")

        if raw_prefix:
            try:
                ipaddress.IPv4Address(str(raw_prefix))
                return ipaddress.IPv4Network(f"{raw_prefix}/{plen}", strict=False)
            except Exception as e:
                raise ValueError(f"Préfixe invalide pour AS{as_key} ({pool_kind}): {raw_prefix}/{plen}") from e

        # Fallback explicite si un AS n'a pas de config fournie
        as_int = int(as_number) if str(as_number).isdigit() else 0
        if pool_kind == "links":
            default_net = ipaddress.IPv4Network(f"172.16.{as_int % 256}.0/24", strict=False)
        else:
            default_net = ipaddress.IPv4Network(f"10.255.{as_int % 256}.0/24", strict=False)
        return default_net

    as_subnet_iter = {}

    def next_intra_subnet(as_number):
        as_key = str(as_number)
        if as_key not in as_subnet_iter:
            as_net = parse_as_pool(as_number, "links")
            if as_net.prefixlen > 30:
                raise ValueError(f"Le préfixe de AS{as_key} doit être <= /30 pour allouer des liens /30")
            as_subnet_iter[as_key] = as_net.subnets(new_prefix=30)
        try:
            return next(as_subnet_iter[as_key])
        except StopIteration as e:
            raise ValueError(f"Plus assez de sous-réseaux /30 disponibles dans le préfixe de AS{as_key}") from e

    inter_pool = ipaddress.IPv4Network("172.31.0.0/16", strict=False)
    inter_subnet_iter = inter_pool.subnets(new_prefix=30)

    loopback_iter = {}

    def next_loopback_ip(as_number):
        as_key = str(as_number)
        if as_key not in loopback_iter:
            loop_net = parse_as_pool(as_number, "loopbacks")
            loopback_iter[as_key] = loop_net.hosts()
        try:
            return str(next(loopback_iter[as_key]))
        except StopIteration as e:
            raise ValueError(f"Plus assez d'adresses loopback disponibles dans le pool /24 de AS{as_key}") from e

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
            # Cas 1 : Intra-AS (Même AS)
            link_subnet = next_intra_subnet(as_a)
            hosts = list(link_subnet.hosts())

            subnet_cidr = str(link_subnet)
            ip_a_str = str(hosts[0])
            ip_b_str = str(hosts[1])
            prefix_len = 30
            mask_str = "255.255.255.252"

        else:
            # Cas 2 : Inter-AS (ou lien non assigné)
            try:
                link_subnet = next(inter_subnet_iter)
            except StopIteration as e:
                raise ValueError("Plus assez de sous-réseaux /30 disponibles pour les liens inter-AS") from e

            hosts = list(link_subnet.hosts())
            subnet_cidr = str(link_subnet)

            ip_a_str = str(hosts[0])
            ip_b_str = str(hosts[1])
            prefix_len = 30
            mask_str = "255.255.255.252"

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

        asn = as_info.get("as_number")
        loopback_ip = None
        if asn is not None:
            loopback_ip = next_loopback_ip(asn)

        router_entry = {
            "name": router_name,
            "protocol": as_info.get("protocol"),
            "as_number": as_info.get("as_number"),
            "as_type": as_info.get("as_type", "unknown"),
            "role": as_info.get("role", "UNKNOWN"),
            "ebgp": as_info.get("ebgp", False),
            "loopback_ip": loopback_ip,
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


