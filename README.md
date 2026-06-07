# GNS3 MPLS L3VPN Automation

Portfolio project developed by **Leno Renaud, Hector Ernoult, Théodore Bonnier, Jules Gruffaz**.

This project automates Cisco c7200 router configuration inside a GNS3 topology using Python scripts and Jinja2 templates.  
It provides a GUI workflow that helps generate and inject routing configurations (OSPF / iBGP / eBGP / MPLS / VRF) into a complete lab architecture.

---

## Technical Architecture & Automation Logic

This tool transcends standard IP routing generation by automatically inferring an entire **MPLS L3VPN** topology from GNS3 geometrical drawings:

1. **Zone Detection & Customer Mapping:**
   - A black rectangle (`#000000`) defines the **Provider network**.
   - Rectangles of any other color define **Customer sites**. Two sites sharing the exact same color are automatically assigned to the same Customer VRF.
2. **Dynamic Role Inference:**
   - **P (Provider Core):** Inside the black rectangle, only connected to other Provider routers.
   - **PE (Provider Edge):** Inside the black rectangle, connected to at least one Customer router.
   - **CE (Customer Edge):** Inside a colored rectangle.
3. **Automated Configuration Stack:**
   - **Underlay (P/PE/RR):** OSPF routing and MPLS LDP are automatically configured on core interfaces.
   - **Overlay (PE/RR):** iBGP VPNv4 sessions are established (via a Route Reflector) to transport customer routes.
   - **Customer Isolation (PE):** VRFs (`CUST_<color>`) are generated on the fly. Route Distinguishers (RD) and Route Targets (RT) are auto-calculated dynamically using the format `<Provider_ASN>:<Customer_ID>`.
   - **Edge Access (PE-CE):** VRF interfaces and eBGP sessions are directly configured for route exchange between provider and clients.

---

## Project Overview

### Goal

Build a reliable automation pipeline for multi-AS GNS3 labs, reducing manual router configuration and making network experiments reproducible.

### Key Features

- GUI-based workflow with usage guidance.
- Automatic topology processing and configuration generation.
- Templated router configs with Jinja2.
- Automated config injection into router files.
- Support for MPLS L3VPN designs (OSPF core, LDP, vpnv4, VRF, PE-CE eBGP).
- Automatic router role detection (PE, P, CE, RR) based on GNS3 rectangles.

### Tech Stack

- **Python**
- **Tkinter** (GUI)
- **Jinja2** (templating)
- **GNS3** project parsing and config injection

---

## Screenshots

### GUI Interface

![Tkinter interface](assets/software_interface_tkinter.png)

### Configuration Result in Router CLI

![Router configuration](assets/router_config_gns.png)

### GNS3 Usage Tutorial View

![GNS3 tutorial](assets/tuto_gns3.png)

---

## Repository Structure

```text
main.py                         # Entry point (GUI launcher)
utils.py                        # Utility helpers
topology.json                   # Topology data source
configs/                        # Generated router configurations
get_topology/                   # Topology extraction logic
gen_config_bgp_ospf/            # BGP + OSPF + MPLS generation module
injection_cfgs/                 # Config injection module
architecture_finale/            # GNS3 project files
assets/                         # README screenshots
```

---

## Setup & Configuration

### 1) Prerequisites

- Python 3.10+ recommended
- GNS3 installed and working
- Cisco c7200 images already available in GNS3 (e.g., `adventerprise` or `advipservices` versions to properly support MPLS LDP, VRF, and VPNv4 capabilities).

### 2) Clone / import the full project

The scripts rely on the repository structure, so keep the complete folder tree unchanged.

```bash
git clone <your-repo-url>
cd <my-project-folder>
```

### 3) Install dependencies

```bash
pip install jinja2
```

`tkinter` is usually bundled with Python on many systems. If missing, install it from your OS package manager.

### 4) Prepare your GNS3 topology

- Open your GNS3 project.
- Draw background rectangles around each AS:
	- **Black strict (#000000)** rectangle for the Provider domain
	- **Any other color** rectangle for Customer VPNs/VRFs. Two rectangles of the same color = same customer.

### 5) Run the application

```bash
python main.py
```

Then:
- Select your GNS3 project folder from the GUI.
- Follow the guided steps.
- Wait for the success message confirming configuration generation/injection.

### 6) Important runtime conditions

- All routers must be **powered off** before injection.
- Existing startup configurations can be overwritten by the process.

---

## Known Limitations

- All routers must be correctly placed inside their specific rectangles for the automatic role assignment (PE, P, CE, RR) to work properly.
- The GUI assumes specific router names to identify the Route Reflector if automatic assignment is checked.

---

## Portfolio Notes

This project demonstrates:

- Network automation design
- Routing protocol orchestration (/OSPF/BGP)
- Template-driven infrastructure generation
- Practical tooling for reproducible lab deployments
- Team collaboration in an academic engineering context
