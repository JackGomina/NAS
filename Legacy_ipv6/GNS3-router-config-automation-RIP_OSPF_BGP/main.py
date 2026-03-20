#!/usr/bin/env python3
"""
Orchestrateur Principal (GUI Version):
1. Sélection du projet GNS3 via Interface Graphique.
2. Configuration du préfixe IPv6 via Dialogue.
3. Exécution séquentielle de l'automatisation.
"""

import json
import os
import shutil
import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox, ttk
from pathlib import Path

# Imports des modules
from get_topology.get_topology import get_topology
from gen_config_bgp_rip.bgp_rip_gen import generate_bgp_configs as gen_rip
from gen_config_bgp_ospf.bgp_ospf_gen import generate_bgp_configs as gen_ospf
from injection_cfgs.injection_cfgs import injection_cfg


def run_automation(gns3_file_path, ip_prefix, loopback_format="simple", advanced_options={}):
    """
    Exécute la logique d'automatisation avec les paramètres fournis.
    """

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
        loopback_format=loopback_format
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

    print("  -> Génération RIP...")
    gen_rip(TOPOLOGY_JSON, output_dir=OUTPUT_CONFIGS_DIR, options=advanced_options)
    
    print("  -> Génération OSPF...")
    gen_ospf(TOPOLOGY_JSON, output_dir=OUTPUT_CONFIGS_DIR, options=advanced_options)
    
    # 3. VERIFICATION DU NOMBRE DE CONFIGURATIONS
    print("\n[3/4] Vérification du nombre de configurations...")
    count = len(list(OUTPUT_CONFIGS_DIR.glob("*.cfg")))
    if count != len(topo_data.get("routers", [])):
        print(f"  [AVERTISSEMENT] Nombre de configurations générées ({count}) ne correspond pas au nombre de routeurs dans la topologie ({len(topo_data.get('routers', []))}).")
    else:
        print(f"  Nombre de configurations générées : {count}")
    
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
    step1 = ttk.LabelFrame(main_frame, text="2. Définir les Protocoles et AS", padding=5)
    step1.pack(fill=tk.X, pady=5)
    
    tk.Label(step1, justify=tk.LEFT, wraplength=550, text=(
        "Utilisez l'outil 'Draw Rectangle' pour définir les zones.\n"
        "La couleur de BORDURE définit le protocole :"
    )).pack(anchor="w")
    
    f_colors = ttk.Frame(step1)
    f_colors.pack(fill=tk.X, pady=2)
    
    tk.Label(f_colors, text="  ●  ", fg="#FF0000", font=("Arial", 14)).pack(side=tk.LEFT)
    tk.Label(f_colors, text="ROUGE = RIP", font=("Arial", 10, "bold")).pack(side=tk.LEFT)
    
    tk.Label(f_colors, text="      ●  ", fg="#00FF00", font=("Arial", 14)).pack(side=tk.LEFT)
    tk.Label(f_colors, text="VERT = OSPF", font=("Arial", 10, "bold")).pack(side=tk.LEFT)

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
            file_path, ip_base="2000:1::/64", output_dir=ROOT_DIR, output_name="topology.json"
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
    
    def submit_config():
        config_results["ip_base"] = entry_ip.get()
        config_results["loopback_fmt"] = var_loopback.get()
        config_results["enable_policies"] = var_policies.get()
        config_results["enable_metrics"] = var_metrics.get()
        config_results["secure_redist"] = var_redist.get()
        config_results["bgp_policies"] = bgp_relations # On passe le dictionnaire des relations
        config_results["ospf_costs"] = ospf_costs
        config_win.destroy()

    config_win = tk.Toplevel(root)
    config_win.title("Configuration du Réseau")
    config_win.geometry("500x650")
    config_win.grab_set()

    # Section 1: Adressage
    lf_addr = ttk.LabelFrame(config_win, text="1. Adressage IPv6", padding=10)
    lf_addr.pack(fill="x", padx=10, pady=10)
    
    ttk.Label(lf_addr, text="Préfixe des adresses physiques (ex: 2000:1::/64)\n Format prévu intra-AS : 2000:1:<AS>:<ID1>:<ID2>::<ID_local>/80\n Format prévu inter-AS : 2000:1:0:<AS1>:<AS2>:<ID1>:<ID2>::<ID_local>/112").pack(anchor="w")
    entry_ip = ttk.Entry(lf_addr)
    entry_ip.insert(0, "2000:1::/64")
    entry_ip.pack(fill="x", pady=5)
    
    ttk.Label(lf_addr, text="Format des adresses Loopback :").pack(anchor="w", pady=(10, 0))
    var_loopback = tk.StringVar(value="with_as")
    ttk.Radiobutton(lf_addr, text="Avec AS (2000:2:<AS>::<ID_routeur>)", variable=var_loopback, value="with_as").pack(anchor="w")
    ttk.Radiobutton(lf_addr, text="Simple (2000::<ID_routeur>)", variable=var_loopback, value="simple").pack(anchor="w")

    # Section 2: Options Avancées (Policies)
    lf_advanced = ttk.LabelFrame(config_win, text="2. Options Avancées", padding=10)
    lf_advanced.pack(fill="x", padx=10, pady=10)

    # 2a. Redistribution Sécurisée
    var_redist = tk.BooleanVar(value=True)
    check_redist = ttk.Checkbutton(lf_advanced, text="Activer Redistribution Sécurisée (Route-Maps)", variable=var_redist)
    check_redist.pack(anchor="w", pady=5)
    ttk.Label(lf_advanced, text="   (Filtre les routes redistribuées pour éviter les boucles)", font=("Arial", 8, "italic"), foreground="gray").pack(anchor="w")

    # 2b. BGP Policies (Gao-Rexford)
    var_policies = tk.BooleanVar(value=False)
    # Store relations: "100-200": "peer", "100-300": "customer" (from 100 pov)
    bgp_relations = {} 

    def open_relations_window():
        """
        Fenêtre pour définir les relations entre AS (Provider/Customer/Peer)
        """
        # Si une fenêtre existe déjà, on ne fait rien (ou on la met au premier plan)
        # Ici on utilise grab_set (modal) donc le bouton sera de toute façon inactif tant que la fenêtre est ouverte.
        
        rel_win = tk.Toplevel(config_win)
        rel_win.title("Relations BGP")
        rel_win.geometry("600x450")
        
        # Rendre la fenêtre modale (bloque la fenêtre parent) et au premier plan
        rel_win.transient(config_win)
        rel_win.grab_set()
        rel_win.focus_set()
        
        # Info Panel : AS Detected
        lf_info = ttk.LabelFrame(rel_win, text="AS Détectés", padding=5)
        lf_info.pack(fill="x", padx=10, pady=5)
        lbl_as_list = tk.Label(lf_info, text="AS trouvés : " + ", ".join(sorted_as_list), fg="blue")
        lbl_as_list.pack(anchor="w")
        
        # Details Routers
        frame_list = ttk.Frame(lf_info)
        frame_list.pack(fill="x", pady=2)

        # Regroupement par AS pour affichage clair
        as_groups = {}
        if 'topo_preview' in locals() and topo_preview:
             for r in topo_preview.get("routers", []):
                asn = str(r.get("as_number", ""))
                if asn:
                    as_groups.setdefault(asn, []).append(r["name"])
        
        display_lines = []
        for asn in sorted(as_groups.keys(), key=lambda x: int(x) if x.isdigit() else 999999):
            routers_str = ", ".join(sorted(as_groups[asn]))
            display_lines.append(f"- AS {asn} : {routers_str}")

        full_text = "\n".join(display_lines) if display_lines else "Aucun routeur détecté."

        lbl_r_map = tk.Label(frame_list, text=full_text, font=("Consolas", 9), justify="left", fg="#333333")
        lbl_r_map.pack(anchor="w", padx=5)

        ttk.Label(rel_win, text="Définissez les relations (A vers B)", font=("Arial", 10, "bold")).pack(pady=10)
        
        frame_input = ttk.Frame(rel_win)
        frame_input.pack(pady=5)
        
        ttk.Label(frame_input, text="AS A :").grid(row=0, column=0)
        # Use Combobox populated with detected AS
        cb_asa = ttk.Combobox(frame_input, values=sorted_as_list, width=8)
        cb_asa.grid(row=0, column=1, padx=5)
        
        ttk.Label(frame_input, text=" est le ").grid(row=0, column=2)
        cb_rel = ttk.Combobox(frame_input, values=["peer", "provider", "customer"], state="readonly", width=10)
        cb_rel.set("peer")
        cb_rel.grid(row=0, column=3, padx=5)
        
        ttk.Label(frame_input, text=" de AS B :").grid(row=0, column=4)
        cb_asb = ttk.Combobox(frame_input, values=sorted_as_list, width=8)
        cb_asb.grid(row=0, column=5, padx=5)
        
        list_box = tk.Listbox(rel_win, width=70, height=8)
        list_box.pack(pady=10)
        
        def update_listbox():
            list_box.delete(0, tk.END)
            for key, rel in bgp_relations.items():
                as1, as2 = key.split("-")
                if rel == "provider":
                    desc = f"AS {as1} est le FOURNISSEUR de AS {as2}"
                elif rel == "customer":
                    desc = f"AS {as1} est le CLIENT de AS {as2}"
                else:
                    desc = f"AS {as1} est le PAIR (Peer) de AS {as2}"
                list_box.insert(tk.END, desc)

        def add_rel():
            as_a = cb_asa.get().strip()
            as_b = cb_asb.get().strip()
            rel = cb_rel.get()
            
            if not as_a or not as_b:
                messagebox.showerror("Erreur", "Veuillez sélectionner des AS.")
                return
            
            if as_a == as_b:
                messagebox.showerror("Erreur", "Impossible de définir une relation sur le même AS.")
                return

            # Nettoyage des relations existantes pour cette paire (sens direct ou inverse)
            key_direct = f"{as_a}-{as_b}"
            key_reverse = f"{as_b}-{as_a}"
            
            if key_direct in bgp_relations:
                del bgp_relations[key_direct]
            if key_reverse in bgp_relations:
                del bgp_relations[key_reverse]

            bgp_relations[key_direct] = rel
            update_listbox()

        def delete_rel():
            selection = list_box.curselection()
            if not selection:
                return
            
            item_text = list_box.get(selection[0])
            # Retrouver la clé via le texte (un peu hacky mais simple ici) or rebuild key logic
            # On parcourt le dictionnaire pour trouver la correspondance
            key_to_del = None
            for key, rel in bgp_relations.items():
                as1, as2 = key.split("-")
                # On recrée la string pour comparer
                if rel == "provider":
                    desc = f"AS {as1} est le FOURNISSEUR de AS {as2}"
                elif rel == "customer":
                    desc = f"AS {as1} est le CLIENT de AS {as2}"
                else:
                    desc = f"AS {as1} est le PAIR (Peer) de AS {as2}"
                
                if desc == item_text:
                    key_to_del = key
                    break
            
            if key_to_del:
                del bgp_relations[key_to_del]
                update_listbox()

        # Frame pour les boutons
        frame_btns = ttk.Frame(rel_win)
        frame_btns.pack(pady=5)
        
        ttk.Button(frame_input, text="Ajouter / Mettre à jour", command=add_rel).grid(row=0, column=6, padx=10)
        ttk.Button(rel_win, text="Supprimer sélection", command=delete_rel).pack(pady=2)

        # Initialiser la liste si des relations existent déjà
        update_listbox()

        ttk.Button(rel_win, text="Valider & Fermer", command=rel_win.destroy).pack(side=tk.BOTTOM, pady=10)


    def toggle_policies_options():
        if var_policies.get():
            btn_config_rels.config(state="normal")
            lbl_pol_info.config(text="Cliquez sur 'Configurer Relations' pour définir qui est client/fournisseur.", foreground="blue")
        else:
            btn_config_rels.config(state="disabled")
            lbl_pol_info.config(text="Mode simple: Tout le monde est 'Peer' (eBGP standard).", foreground="gray")

    check_policies = ttk.Checkbutton(lf_advanced, text="Activer Politiques BGP (Gao-Rexford)", variable=var_policies, command=toggle_policies_options)
    check_policies.pack(anchor="w", pady=(15, 5))
    
    lbl_pol_info = ttk.Label(lf_advanced, text="Mode simple: Tout le monde est 'Peer' (eBGP standard).", font=("Arial", 8, "italic"), foreground="gray")
    lbl_pol_info.pack(anchor="w", padx=20)

    btn_config_rels = ttk.Button(lf_advanced, text="Configurer Relations...", state="disabled", command=open_relations_window) 
    btn_config_rels.pack(anchor="w", padx=20, pady=5)

    # 2c. OSPF Metrics
    var_metrics = tk.BooleanVar(value=False)
    # { "R1": { "Gi1/0": 10 } }
    ospf_costs = {} 

    def open_metrics_window():
        met_win = tk.Toplevel(config_win)
        met_win.title("Métriques OSPF - Tableau des Coûts")
        met_win.geometry("700x500")
        met_win.transient(config_win)
        met_win.grab_set()

        ttk.Label(met_win, text="Tableau des Coûts OSPF (Défaut: 10)", font=("Arial", 10, "bold")).pack(pady=10)

        # Container for the canvas and scrollbar
        container = ttk.Frame(met_win)
        container.pack(fill="both", expand=True, padx=10, pady=5)
        
        canvas = tk.Canvas(container)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Pour activer le scroll avec la molette souris
        def on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", on_mousewheel)

        # Headers
        ttk.Label(scrollable_frame, text="Lien (Connexion)", font=("Arial", 9, "bold")).grid(row=0, column=0, padx=5, pady=5, sticky="w")
        ttk.Label(scrollable_frame, text="Coût", font=("Arial", 9, "bold")).grid(row=0, column=1, padx=5, pady=5, sticky="w")

        # Data
        links_data = topo_preview.get("links", []) if topo_preview else []
        link_entries = [] # To store (link_obj, entry_widget)

        for i, link in enumerate(links_data):
            rA, ifA = link["a"], link["a_iface"]
            rB, ifB = link["b"], link["b_iface"]
            
            label_text = f"{rA} <--> {rB} ({ifA}) ({ifB})"
            
            # Check if there is already a cost defined in ospf_costs for rA side
            current_cost = 10
            if rA in ospf_costs and ifA in ospf_costs[rA]:
                current_cost = ospf_costs[rA][ifA]
            
            lbl = ttk.Label(scrollable_frame, text=label_text)
            lbl.grid(row=i+1, column=0, padx=5, pady=2, sticky="w")
            
            ent = ttk.Entry(scrollable_frame, width=10)
            ent.insert(0, str(current_cost))
            ent.grid(row=i+1, column=1, padx=5, pady=2)
            
            link_entries.append((link, ent))
            
        def save_metrics():
            # Reset
            ospf_costs.clear()
            
            error_flag = False
            for link, ent in link_entries:
                try:
                    val = int(ent.get())
                except ValueError:
                    error_flag = True
                    break
                
                # Apply symmetry
                rA, ifA = link["a"], link["a_iface"]
                rB, ifB = link["b"], link["b_iface"]
                
                if rA not in ospf_costs: ospf_costs[rA] = {}
                if rB not in ospf_costs: ospf_costs[rB] = {}
                
                # Default is 10, only store if different? Or store everything to be explicit.
                # Storing everything is safer for the generator logic.
                ospf_costs[rA][ifA] = val
                ospf_costs[rB][ifB] = val
            
            if error_flag:
                messagebox.showerror("Erreur", "Tous les coûts doivent être des entiers valides.")
                return

            # Cleanup bindings
            canvas.unbind_all("<MouseWheel>")
            met_win.destroy()

        ttk.Button(met_win, text="Enregistrer & Fermer", command=save_metrics).pack(pady=10)

    def toggle_metrics_options():
        if var_metrics.get():
            btn_config_met.config(state="normal")
            lbl_met_info.config(text="Cliquez sur 'Configurer Coûts OSPF' pour définir les métriques manuellement.", foreground="blue")
        else:
            btn_config_met.config(state="disabled")
            lbl_met_info.config(text="Mode automatique : Coûts par défaut (10).", foreground="gray")

    check_metrics = ttk.Checkbutton(lf_advanced, text="Activer Optimisation Métriques OSPF", variable=var_metrics, command=toggle_metrics_options)
    check_metrics.pack(anchor="w", pady=(15, 5))
    
    lbl_met_info = ttk.Label(lf_advanced, text="Mode automatique : Coûts par défaut (10).", font=("Arial", 8, "italic"), foreground="gray")
    lbl_met_info.pack(anchor="w", padx=20)

    btn_config_met = ttk.Button(lf_advanced, text="Configurer Coûts OSPF...", state="disabled", command=open_metrics_window)
    btn_config_met.pack(anchor="w", padx=20, pady=5)

    # Bouton Valider
    ttk.Button(config_win, text="Valider & Lancer", command=submit_config).pack(side="bottom", pady=20)
    
    root.wait_window(config_win)
    
    if not config_results:
        print("Annulé par l'utilisateur.")
        return

    # Extraction des valeurs
    ip_base = config_results.get("ip_base", "2000:1::/64")
    loopback_choice = config_results.get("loopback_fmt", "simple")

    # 3. Lancer le traitement
    print(f"Options choisies : Policies={config_results['enable_policies']}, Metrics={config_results['enable_metrics']}, Redist={config_results['secure_redist']}")
    
    # Construction du dictionnaire d'options
    advanced_options = {
        "secure_redist": config_results["secure_redist"],
        "policies_enabled": config_results["enable_policies"],
        "bgp_relations": config_results.get("bgp_policies", {}),
        "ospf_costs": config_results.get("ospf_costs", {})
    }
    
    success, message = run_automation(file_path, ip_base, loopback_choice, advanced_options)
    
    if success:
        messagebox.showinfo("Terminé", message)
    else:
        messagebox.showerror("Erreur", message)

if __name__ == "__main__":
    main_gui()
