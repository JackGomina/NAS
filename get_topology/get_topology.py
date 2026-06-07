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


# --- REGLES DE ZONING ---
# Noir strict: uniquement #000000
PROVIDER_COLOR_STRICT = "000000"
PROVIDER_ASN_DEFAULT = 65000


def normalize_hex_color(raw):
    """
    Normalize un code couleur hexadécimal (sans '#') en minuscule sur 6 caractères.
    """
    val = str(raw or "").strip().lower().lstrip("#")
    if len(val) == 3 and all(c in "0123456789abcdef" for c in val):
        val = "".join(c * 2 for c in val)
    if len(val) != 6 or any(c not in "0123456789abcdef" for c in val):
        return "000000"
    return val

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
    - Noir strict (#000000): zone provider
    - Toute autre couleur: zone client
    Même couleur => même client (même customer_id/as_number)
    """
    drawings = gns3_data.get("topology", {}).get("drawings", [])
    rectangles = []
    provider_rectangles = []
    customer_color_to_asn = {}
    customer_color_to_id = {}
    next_customer_asn = 65100
    next_customer_id = 1
    
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
            color = normalize_hex_color(color_match.group(1) if color_match else "000000")
            color_lower = color.lower()

            rect = {
                "x": drawing["x"],
                "y": drawing["y"],
                "width": width,
                "height": height,
                "color": color_lower
            }

            if color_lower == PROVIDER_COLOR_STRICT:
                # Zone provider
                rect["protocol"] = "OSPF"
                rect["zone_type"] = "provider"
                rect["as_number"] = PROVIDER_ASN_DEFAULT
                rect["customer_id"] = None
                rect["customer_color"] = None
                rect["is_provider_zone"] = True
                provider_rectangles.append(rect)
                rectangles.append(rect)
            else:
                # Zone client
                if color_lower not in customer_color_to_asn:
                    customer_color_to_asn[color_lower] = next_customer_asn
                    next_customer_asn += 1
                if color_lower not in customer_color_to_id:
                    customer_color_to_id[color_lower] = next_customer_id
                    next_customer_id += 1

                rect["protocol"] = "BGP"
                rect["zone_type"] = "customer"
                rect["as_number"] = customer_color_to_asn[color_lower]
                rect["customer_id"] = customer_color_to_id[color_lower]
                rect["customer_color"] = f"#{color_lower}"
                rect["is_provider_zone"] = False
                rectangles.append(rect)

    return {
        "as_rectangles": rectangles,
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
    Associe chaque routeur à une zone provider/customer selon les rectangles.
    Priorité: provider > customer > none.
    Signale les incohérences en cas de chevauchement provider+customer.
    """
    router_to_as = {}
    warnings = []
    
    for node in nodes_data:
        # On ne traite que les routeurs (type "dynamips")
        if node.get("node_type") != "dynamips":
            continue

        name = node["name"]
        x, y = node["x"], node["y"]
        
        containing_rects = [r for r in rectangles if is_point_in_rectangle(x, y, r)]

        provider_containing = [r for r in containing_rects if r.get("zone_type") == "provider"]
        customer_containing = [r for r in containing_rects if r.get("zone_type") == "customer"]

        if provider_containing and customer_containing:
            warnings.append(
                f"Routeur {name}: chevauchement incohérent (zone provider + zone client). "
                f"Priorité appliquée à la zone provider."
            )

        protocol = "UNKNOWN"
        as_number = None
        has_ebgp = False
        as_type = "unknown"
        customer_id = None
        customer_color = None

        if provider_containing:
            chosen = provider_containing[0]
            protocol = chosen.get("protocol", "OSPF")
            as_number = chosen.get("as_number")
            as_type = "provider"
        elif customer_containing:
            chosen = customer_containing[0]
            protocol = chosen.get("protocol", "BGP")
            as_number = chosen.get("as_number")
            as_type = "customer"
            customer_id = chosen.get("customer_id")
            customer_color = chosen.get("customer_color")
        
        router_to_as[name] = {
            "protocol": protocol,
            "as_number": as_number,
            "ebgp": has_ebgp,
            "as_type": as_type,
            "customer_id": customer_id,
            "customer_color": customer_color
        }

    return router_to_as, warnings


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
    role_overrides=None,
    auto_rr_from_name=False
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
    router_to_as, assignment_warnings = assign_routers_to_as(nodes_data, as_rectangles)
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

    # --- 2c. DETECTION DES ROLES (PE/P/RR/CE/C) ---
    border_routers = set()
    for link in links:
        a = link["a"]
        b = link["b"]
        a_type = router_to_as.get(a, {}).get("as_type", "unknown")
        b_type = router_to_as.get(b, {}).get("as_type", "unknown")
        if {a_type, b_type} == {"provider", "customer"}:
            border_routers.add(a)
            border_routers.add(b)

    for router_name, as_info in router_to_as.items():
        asn = as_info.get("as_number")
        if asn is None:
            as_info["role"] = "UNKNOWN"
            router_to_as[router_name] = as_info
            continue

        as_type = as_info.get("as_type", "unknown")
        is_border = router_name in border_routers

        if is_border and as_type == "provider":
            role = "PE"
        elif not is_border and as_type == "provider":
            role = "P"
        elif is_border and as_type == "customer":
            role = "CE"
        else:
            role = "C"

        # Option GUI: activer l'auto-RR par convention de nom.
        if auto_rr_from_name and as_type == "provider" and "RR" in str(router_name).upper():
            role = "RR"

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

    for warn in assignment_warnings:
        print(f"  [AVERTISSEMENT] {warn}")

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
            "customer_id": as_info.get("customer_id"),
            "customer_color": as_info.get("customer_color"),
            "role": as_info.get("role", "UNKNOWN"),
            "ebgp": as_info.get("ebgp", False),
            "loopback_ip": loopback_ip,
            "interfaces": interfaces_cfg.get(router_name, []),
            "networks": sorted(networks.get(router_name, []))
        }
        topology_data["routers"].append(router_entry)

    topology_data["warnings"] = assignment_warnings

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


