#!/usr/bin/env python3
"""
Orchestrateur Principal (GUI Version):
1. Sélection du projet GNS3 via Interface Graphique.
2. Configuration du préfixe ip via Dialogue.
3. Exécution séquentielle de l'automatisation.
"""

import json
import os
import shutil
import tkinter as tk
import ipaddress
from tkinter import filedialog, simpledialog, messagebox, ttk
from pathlib import Path

# Imports des modules
from get_topology.get_topology import get_topology
from gen_config_bgp_ospf.bgp_ospf_gen import generate_bgp_configs as gen_ospf
from injection_cfgs.injection_cfgs import injection_cfg


def run_automation(gns3_file_path, ip_prefix, loopback_format="simple", routing_strategy="grand_reseaux", advanced_options=None):
    """
    Exécute la logique d'automatisation avec les paramètres fournis.
    """

    if advanced_options is None:
        advanced_options = {}

    gns3_file = Path(gns3_file_path)
    project_dir = gns3_file.parent
    
    # Dossiers de travail
    ROOT_DIR = Path(__file__).parent.absolute()
    OUTPUT_CONFIGS_DIR = ROOT_DIR / "configs"
    TOPOLOGY_JSON = ROOT_DIR / "topology.json"
    
    print("\n" + "="*60)
    print(f"      DEMARRAGE AUTOMATISATION")
    print(f"      Projet: {gns3_file.name}")
    print(f"      Préfixe IP: {ip_prefix}")
    print(f"      Format Loopback: {loopback_format}")
    print("="*60)

    # 1. EXTRACTION DE LA TOPOLOGIE
    print(f"\n[1/4] Extraction de la topologie...")
    topo_data = get_topology(
        gns3_file, 
        ip_base=ip_prefix, 
        output_dir=ROOT_DIR, 
        output_name="topology.json",
        loopback_format=loopback_format,
        routing_strategy=routing_strategy,
        as_prefixes=advanced_options.get("as_prefixes", {}),
        role_overrides=advanced_options.get("role_overrides", {}),
        auto_rr_from_name=advanced_options.get("auto_rr_from_name", False)
    )
    
    if topo_data is None:
        if TOPOLOGY_JSON.exists():
            print("  ! Rechargement depuis topology.json existant...")
            with open(TOPOLOGY_JSON, 'r', encoding='utf-8') as f:
                topo_data = json.load(f)
        else:
            return False, "Impossible de charger la topologie."

    # 2. GENERATION DES CONFIGURATIONS
    print("\n[2/4] Génération des configurations...")
    if OUTPUT_CONFIGS_DIR.exists(): shutil.rmtree(OUTPUT_CONFIGS_DIR) 
    OUTPUT_CONFIGS_DIR.mkdir(exist_ok=True)
    
    print("  -> Génération des configurations d'adressage...")
    gen_ospf(TOPOLOGY_JSON, output_dir=OUTPUT_CONFIGS_DIR, options=advanced_options)
    
    # 3. VERIFICATION DU NOMBRE DE CONFIGURATIONS
    print("\n[3/4] Vérification du nombre de configurations...")
    count = len(list(OUTPUT_CONFIGS_DIR.glob("*.cfg")))
    if count != len(topo_data.get("routers", [])):
        print(f"  [AVERTISSEMENT] Nombre de configurations générées ({count}) ne correspond pas au nombre de routeurs dans la topologie ({len(topo_data.get('routers', []))}).")
    else:
        print(f"  Nombre de configurations générées : {count}")

    # 3b. Validation phase 0 OSPF (P/PE uniquement)
    print("\n[3b] Validation OSPF phase 0...")
    missing_ospf = []
    unexpected_ospf = []
    for r in topo_data.get("routers", []):
        role = str(r.get("role", "UNKNOWN")).upper()
        cfg_path = OUTPUT_CONFIGS_DIR / f"{r.get('name')}.cfg"
        if not cfg_path.exists():
            continue
        cfg_text = cfg_path.read_text(encoding="utf-8")
        has_ospf = "router ospf" in cfg_text
        expected_ospf = role in {"P", "PE", "RR"}
        if expected_ospf and not has_ospf:
            missing_ospf.append(r.get("name"))
        if (not expected_ospf) and has_ospf:
            unexpected_ospf.append(r.get("name"))

    if not missing_ospf and not unexpected_ospf:
        print("  Validation OSPF OK: roles et configuration cohérents.")
    else:
        if missing_ospf:
            print(f"  [AVERTISSEMENT] OSPF absent sur: {', '.join(missing_ospf)}")
        if unexpected_ospf:
            print(f"  [AVERTISSEMENT] OSPF non attendu sur: {', '.join(unexpected_ospf)}")

    # 3c. Validation phase 1 MPLS/LDP (P/PE uniquement)
    print("\n[3c] Validation MPLS/LDP phase 1...")
    missing_mpls_global = []
    unexpected_mpls_global = []
    missing_mpls_iface = []

    provider_roles = {"P", "PE", "RR"}
    router_roles = {
        r.get("name"): str(r.get("role", "UNKNOWN")).upper()
        for r in topo_data.get("routers", [])
    }

    expected_mpls_ifaces = {}
    for link in topo_data.get("links", []):
        a = link.get("a")
        b = link.get("b")
        role_a = router_roles.get(a, "UNKNOWN")
        role_b = router_roles.get(b, "UNKNOWN")
        if role_a in provider_roles and role_b in provider_roles:
            expected_mpls_ifaces.setdefault(a, set()).add(link.get("a_iface"))
            expected_mpls_ifaces.setdefault(b, set()).add(link.get("b_iface"))

    for r in topo_data.get("routers", []):
        router_name = r.get("name")
        role = router_roles.get(router_name, "UNKNOWN")
        cfg_path = OUTPUT_CONFIGS_DIR / f"{router_name}.cfg"
        if not cfg_path.exists():
            continue

        cfg_text = cfg_path.read_text(encoding="utf-8")
        has_ldp_global = "mpls ldp router-id Loopback0 force" in cfg_text

        if role in provider_roles and not has_ldp_global:
            missing_mpls_global.append(router_name)
        if role not in provider_roles and has_ldp_global:
            unexpected_mpls_global.append(router_name)

        for iface in expected_mpls_ifaces.get(router_name, set()):
            iface_block = f"interface {iface}\n"
            iface_pos = cfg_text.find(iface_block)
            if iface_pos == -1:
                missing_mpls_iface.append(f"{router_name}:{iface} (interface absente)")
                continue
            next_iface_pos = cfg_text.find("\ninterface ", iface_pos + len(iface_block))
            block = cfg_text[iface_pos:] if next_iface_pos == -1 else cfg_text[iface_pos:next_iface_pos]
            if " mpls ip" not in block:
                missing_mpls_iface.append(f"{router_name}:{iface}")

    if not missing_mpls_global and not unexpected_mpls_global and not missing_mpls_iface:
        print("  Validation MPLS/LDP OK: rôles et interfaces cœur cohérents.")
    else:
        if missing_mpls_global:
            print(f"  [AVERTISSEMENT] LDP global absent sur: {', '.join(missing_mpls_global)}")
        if unexpected_mpls_global:
            print(f"  [AVERTISSEMENT] LDP global non attendu sur: {', '.join(unexpected_mpls_global)}")
        if missing_mpls_iface:
            print(f"  [AVERTISSEMENT] MPLS manquant sur interfaces coeur: {', '.join(missing_mpls_iface)}")

    # 3d. Validation phase 2 BGP vpnv4 (PE/RR uniquement)
    print("\n[3d] Validation BGP vpnv4 phase 2...")
    missing_vpnv4 = []
    unexpected_vpnv4 = []

    for r in topo_data.get("routers", []):
        router_name = r.get("name")
        role = str(r.get("role", "UNKNOWN")).upper()
        cfg_path = OUTPUT_CONFIGS_DIR / f"{router_name}.cfg"
        if not cfg_path.exists():
            continue

        cfg_text = cfg_path.read_text(encoding="utf-8")
        has_vpnv4 = "address-family vpnv4" in cfg_text
        expected_vpnv4 = role in {"PE", "RR"}

        if expected_vpnv4 and not has_vpnv4:
            missing_vpnv4.append(router_name)
        if (not expected_vpnv4) and has_vpnv4:
            unexpected_vpnv4.append(router_name)

    if not missing_vpnv4 and not unexpected_vpnv4:
        print("  Validation BGP vpnv4 OK: présence cohérente sur PE/RR.")
    else:
        if missing_vpnv4:
            print(f"  [AVERTISSEMENT] vpnv4 absent sur: {', '.join(missing_vpnv4)}")
        if unexpected_vpnv4:
            print(f"  [AVERTISSEMENT] vpnv4 non attendu sur: {', '.join(unexpected_vpnv4)}")

    # 3e. Validation phase 3 VRF + PE-CE eBGP
    print("\n[3e] Validation phase 3 (VRF + eBGP PE-CE)...")
    missing_vrf_on_pe = []
    missing_pe_ce_ebgp = []
    missing_ce_bgp = []

    role_map = {
        r.get("name"): str(r.get("role", "UNKNOWN")).upper()
        for r in topo_data.get("routers", [])
    }

    pe_with_ce = set()
    ce_routers = set()
    for link in topo_data.get("links", []):
        a = link.get("a")
        b = link.get("b")
        role_a = role_map.get(a, "UNKNOWN")
        role_b = role_map.get(b, "UNKNOWN")
        if role_a == "PE" and role_b == "CE":
            pe_with_ce.add(a)
            ce_routers.add(b)
        elif role_b == "PE" and role_a == "CE":
            pe_with_ce.add(b)
            ce_routers.add(a)

    for pe_name in sorted(pe_with_ce):
        cfg_path = OUTPUT_CONFIGS_DIR / f"{pe_name}.cfg"
        if not cfg_path.exists():
            continue
        cfg_text = cfg_path.read_text(encoding="utf-8")
        if "ip vrf CUST_" not in cfg_text:
            missing_vrf_on_pe.append(pe_name)
        if "address-family ipv4 vrf" not in cfg_text:
            missing_pe_ce_ebgp.append(pe_name)

    for ce_name in sorted(ce_routers):
        cfg_path = OUTPUT_CONFIGS_DIR / f"{ce_name}.cfg"
        if not cfg_path.exists():
            continue
        cfg_text = cfg_path.read_text(encoding="utf-8")
        if "router bgp" not in cfg_text:
            missing_ce_bgp.append(ce_name)

    if not missing_vrf_on_pe and not missing_pe_ce_ebgp and not missing_ce_bgp:
        print("  Validation phase 3 OK: VRF et eBGP PE-CE présents où attendu.")
    else:
        if missing_vrf_on_pe:
            print(f"  [AVERTISSEMENT] VRF absente sur PE: {', '.join(missing_vrf_on_pe)}")
        if missing_pe_ce_ebgp:
            print(f"  [AVERTISSEMENT] eBGP VRF absent sur PE: {', '.join(missing_pe_ce_ebgp)}")
        if missing_ce_bgp:
            print(f"  [AVERTISSEMENT] BGP CE absent sur: {', '.join(missing_ce_bgp)}")
    
    # 4. INJECTION DANS GNS3
    print("\n[4/4] Injection dans le projet GNS3...")
    injection_cfg(project_dir=str(project_dir), configs_dir=str(OUTPUT_CONFIGS_DIR))
    
    return True, f"Succès ! {count} configurations générées et injectées."


def show_tutorial(root):
    """
    Affiche une fenêtre d'aide expliquant comment préparer le projet GNS3.
    """
    tuto = tk.Toplevel(root)
    tuto.title("Guide de préparation GNS3")
    tuto.geometry("600x650") 
    
    tuto.grab_set()
    tuto.focus_force() 

    # Conteneur principal
    main_frame = ttk.Frame(tuto, padding="20")
    main_frame.pack(fill=tk.BOTH, expand=True)

    ttk.Label(main_frame, text="Pré-requis : Structure GNS3", font=("Helvetica", 16, "bold")).pack(pady=(0, 15))

    # --- ETAPE 1 : CABLAGE ---
    step0 = ttk.LabelFrame(main_frame, text="1. Positionnement & Câblage", padding=5)
    step0.pack(fill=tk.X, pady=5)
    tk.Label(step0, justify=tk.LEFT, wraplength=550, text=(
        "Placez vos routeurs dans l'espace de travail et reliez-les entre eux (câblez les interfaces)."
    )).pack(anchor="w")

    # --- ETAPE 2 : LES RECTANGLES ---
    step1 = ttk.LabelFrame(main_frame, text="2. Définir les Zones AS", padding=5)
    step1.pack(fill=tk.X, pady=5)
    
    tk.Label(step1, justify=tk.LEFT, wraplength=550, text=(
        "Utilisez l'outil 'Draw Rectangle' pour définir les zones.\n"
        "Noir strict (#000000) = domaine provider.\n"
        "Toute autre couleur = client/VRF.\n"
        "Deux rectangles de même couleur = même client."
    )).pack(anchor="w")
    
    f_colors = ttk.Frame(step1)
    f_colors.pack(fill=tk.X, pady=2)
    
    tk.Label(f_colors, text="      ●  ", fg="#4fa3d1", font=("Arial", 14)).pack(side=tk.LEFT)
    tk.Label(f_colors, text="COULEUR = Zone Client", font=("Arial", 10, "bold")).pack(side=tk.LEFT)
    tk.Label(f_colors, text="      ●  ", fg="#000000", font=("Arial", 14)).pack(side=tk.LEFT)
    tk.Label(f_colors, text="NOIR STRICT = Domaine Provider", font=("Arial", 10, "bold")).pack(side=tk.LEFT)

    # --- ETAPE 3 : ARRIERE PLAN ---
    step2 = ttk.LabelFrame(main_frame, text="3. IMPORTANT : Arrière-plan", padding=5)
    step2.pack(fill=tk.X, pady=5)
    
    tk.Label(step2, justify=tk.LEFT, wraplength=550, text=(
        "Clic-Droit sur chaque rectangle > 'Lower one layer'.\n"
        "Sinon, les routeurs seront cachés par le rectangle."
    )).pack(anchor="w")

    # --- ETAPE 4 : EXTINCTION ---
    step3 = ttk.LabelFrame(main_frame, text="4. CRITIQUE : Eteindre les routeurs", padding=5)
    step3.pack(fill=tk.X, pady=5)
    
    tk.Label(step3, justify=tk.LEFT, wraplength=550, fg="red", font=("Arial", 10, "bold"), text=(
        "Avant de continuer :\n"
        "Assurez-vous que TOUS les routeurs sont ETEINTS (Stop).\n"
        "L'injection de configuration ne fonctionne que si les routeurs sont à l'arrêt."
    )).pack(anchor="w")

    # --- BOUTON IMAGE (ASSETS) ---
    assets_dir = Path(__file__).parent / "assets"
    tuto_img_path = assets_dir / "tuto_gns3.png"
    
    def open_image():
        if tuto_img_path.exists():
            # Ouvre l'image avec le visualiseur par défaut du système 
            os.startfile(tuto_img_path)
        else:
            messagebox.showinfo("Image manquante", f"L'image d'aide n'a pas été trouvée dans :\n{assets_dir}")

    if tuto_img_path.exists():
        btn = ttk.Button(main_frame, text="📷 Voir l'exemple en Image (Ouvrir)", command=open_image)
        btn.pack(pady=15, ipady=5)
    else:
        ttk.Label(main_frame, text="(Image d'aide non trouvée)", fg="gray").pack(pady=10)

    # Bouton OK
    ttk.Button(main_frame, text="Tout est prêt -> Sélectionner le projet", command=tuto.destroy).pack(side=tk.BOTTOM, pady=10)
    
    # Attendre la fermeture
    root.wait_window(tuto)


def main_gui():
    root = tk.Tk()
    root.withdraw() # Cacher la fenêtre principale vide
    
    # 0. Afficher le tutoriel
    show_tutorial(root)

    # 1. Sélectionner le fichier GNS3
    print("En attente de sélection du fichier .gns3...")
    file_path = filedialog.askopenfilename(
        title="Sélectionnez votre fichier de projet GNS3 (.gns3)",
        filetypes=[("GNS3 Project", "*.gns3"), ("All Files", "*.*")]
    )

    if not file_path:
        print("Annulé par l'utilisateur.")
        return

    # --- ANALYSE PRELIMINAIRE DE LA STRUCTURE (AS/Routeurs) ---
    print("Analyse de la topologie pour détection des AS...")
    # On utilise le répertoire racine du script
    ROOT_DIR = Path(__file__).parent.absolute()
    
    # On lance une extraction pour récupérer la liste des AS et on sauvegarde
    # directement dans topology.json à la racine
    try:
        topo_preview = get_topology(
            file_path,
            ip_base="10.0.0.0/8",
            output_dir=ROOT_DIR,
            output_name="topology.json",
            auto_rr_from_name=False
        )
        detected_as = set()
        router_as_map = []
        for r in topo_preview.get("routers", []):
            if r.get("as_number"):
                as_str = str(r["as_number"])
                detected_as.add(as_str)
                router_as_map.append(f"{r['name']} -> AS {as_str}")
        sorted_as_list = sorted(list(detected_as), key=int)
    except Exception as e:
        print(f"Erreur lors de l'analyse préliminaire : {e}")
        sorted_as_list = []
        router_as_map = []

    # --- NOUVELLE INTERFACE DE CONFIGURATION AVANCEE ---
    # On remplace les simpledialog successifs par une seule fenêtre de config
    
    config_results = {}

    preview_routers = sorted(topo_preview.get("routers", []), key=lambda r: r.get("name", "")) if sorted_as_list else []
    detected_role_map = {r.get("name"): r.get("role", "UNKNOWN") for r in preview_routers}

    config_win = tk.Toplevel(root)
    config_win.title("Configuration du Réseau")
    config_win.geometry("760x760")
    config_win.grab_set()

    # Section 0: Validation des roles detectes
    lf_roles = ttk.LabelFrame(config_win, text="0. Rôles routeurs (auto depuis .gns3)", padding=10)
    lf_roles.pack(fill="x", padx=10, pady=10)

    ttk.Label(
        lf_roles,
        text=(
            "Règle auto: bordure + AS FAI => PE, coeur FAI => P, bordure client => CE.\n"
            "Vous pouvez corriger manuellement si nécessaire."
        )
    ).pack(anchor="w")

    var_auto_rr = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        lf_roles,
        text="Activer auto-RR par nom (contient 'RR')",
        variable=var_auto_rr
    ).pack(anchor="w", pady=(6, 8))

    role_vars = {}
    if preview_routers:
        for r in preview_routers:
            router_name = r.get("name", "?")
            asn = r.get("as_number", "?")
            as_type = r.get("as_type", "unknown")

            row = ttk.Frame(lf_roles)
            row.pack(fill="x", pady=2)

            ttk.Label(row, text=f"{router_name} (AS {asn}, {as_type})", width=38).pack(side="left")
            role_var = tk.StringVar(value=r.get("role", "UNKNOWN"))
            role_vars[router_name] = role_var
            ttk.Combobox(
                row,
                textvariable=role_var,
                values=["PE", "P", "RR", "CE", "C", "UNKNOWN"],
                state="readonly",
                width=12
            ).pack(side="left")
    else:
        ttk.Label(lf_roles, text="Aucun routeur détecté.", foreground="red").pack(anchor="w")

    # Section 1: Adressage
    lf_addr = ttk.LabelFrame(config_win, text="1. Adressage ip", padding=10)
    lf_addr.pack(fill="x", padx=10, pady=10)

    ttk.Label(
        lf_addr,
        text=(
            "Saisissez 2 pools IPv4 par AS (tous en /24) :\n"
            "- Pool Liens: utilisé pour allouer des /30 sur les liens intra-AS\n"
            "- Pool Loopbacks: utilisé pour allouer les Loopback0"
        )
    ).pack(anchor="w")

    as_link_pool_vars = {}
    as_loop_pool_vars = {}

    if sorted_as_list:
        for idx, asn in enumerate(sorted_as_list):
            default_link_pool = f"172.16.{idx}.0"
            default_loop_pool = f"10.255.{idx}.0"
            row = ttk.Frame(lf_addr)
            row.pack(fill="x", pady=4)

            ttk.Label(row, text=f"AS {asn}", width=8).pack(side="left")

            ttk.Label(row, text="Liens").pack(side="left", padx=(0, 4))
            link_pool_var = tk.StringVar(value=default_link_pool)
            as_link_pool_vars[asn] = link_pool_var
            ttk.Entry(row, textvariable=link_pool_var, width=16).pack(side="left", padx=(0, 3))
            ttk.Label(row, text="/24").pack(side="left", padx=(0, 8))

            ttk.Label(row, text="Loopbacks").pack(side="left", padx=(0, 4))
            loop_pool_var = tk.StringVar(value=default_loop_pool)
            as_loop_pool_vars[asn] = loop_pool_var
            ttk.Entry(row, textvariable=loop_pool_var, width=16).pack(side="left", padx=(0, 3))
            ttk.Label(row, text="/24").pack(side="left")
    else:
        ttk.Label(lf_addr, text="Aucun AS détecté dans la topologie.", foreground="red").pack(anchor="w", pady=5)

    def submit_config():
        if not sorted_as_list:
            messagebox.showerror("Erreur", "Aucun AS détecté. Vérifiez les rectangles/AS dans GNS3 puis relancez.")
            return

        as_prefixes_cfg = {}
        for asn in sorted_as_list:
            raw_link_pool = as_link_pool_vars[asn].get().strip()
            raw_loop_pool = as_loop_pool_vars[asn].get().strip()

            if not raw_link_pool:
                messagebox.showerror("Erreur", f"Pool liens vide pour AS {asn}.")
                return
            if not raw_loop_pool:
                messagebox.showerror("Erreur", f"Pool loopbacks vide pour AS {asn}.")
                return

            if "/" in raw_link_pool:
                messagebox.showerror("Erreur", f"Entrez le pool liens de AS {asn} sans '/'. Le masque est fixé à /24.")
                return
            if "/" in raw_loop_pool:
                messagebox.showerror("Erreur", f"Entrez le pool loopbacks de AS {asn} sans '/'. Le masque est fixé à /24.")
                return

            try:
                ipaddress.IPv4Address(raw_link_pool)
                normalized_link_net = ipaddress.IPv4Network(f"{raw_link_pool}/24", strict=False)
                ipaddress.IPv4Address(raw_loop_pool)
                normalized_loop_net = ipaddress.IPv4Network(f"{raw_loop_pool}/24", strict=False)
            except ValueError:
                messagebox.showerror(
                    "Erreur",
                    f"Pool IPv4 invalide pour AS {asn}: liens={raw_link_pool}/24 loopbacks={raw_loop_pool}/24"
                )
                return

            as_prefixes_cfg[asn] = {
                "links": {
                    "prefix": str(normalized_link_net.network_address),
                    "prefix_len": 24
                },
                "loopbacks": {
                    "prefix": str(normalized_loop_net.network_address),
                    "prefix_len": 24
                }
            }

        config_results["as_prefixes"] = as_prefixes_cfg
        config_results["role_overrides"] = {
            name: var.get()
            for name, var in role_vars.items()
            if var.get() != detected_role_map.get(name, "UNKNOWN")
        }
        config_results["auto_rr_from_name"] = bool(var_auto_rr.get())
        config_win.destroy()

    # Bouton Valider
    ttk.Button(config_win, text="Valider & Lancer", command=submit_config).pack(side="bottom", pady=20)
    
    root.wait_window(config_win)
    
    if not config_results:
        print("Annulé par l'utilisateur.")
        return

    # Extraction des valeurs
    ip_base = "10.0.0.0/8"
    loopback_choice = "with_as"
    routing_strategy = "grand_reseaux"

    # 3. Lancer le traitement
    print("Options choisies : adressage par AS + validation des rôles")
    
    # Construction du dictionnaire d'options
    advanced_options = {
        "as_prefixes": config_results.get("as_prefixes", {}),
        "role_overrides": config_results.get("role_overrides", {}),
        "auto_rr_from_name": config_results.get("auto_rr_from_name", False)
    }
    
    success, message = run_automation(file_path, ip_base, loopback_choice, routing_strategy, advanced_options)
    
    if success:
        messagebox.showinfo("Terminé", message)
    else:
        messagebox.showerror("Erreur", message)

if __name__ == "__main__":
    main_gui()
