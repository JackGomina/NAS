# BGP Configs

- Fonction qui prend comme paramètre le fichier topology.json, l'output dir et les options.

- On fait un dictionnaire ROUTERS avec comme clé le nom du routeur et value l'objet du routeur.

- On fait une liste LINK avec les objets de liens.

- On enrichit avec bgp_neighbors, router_id, loopback_ip, as_number.

- Pour chacun des liens il va déterminer si les routeurs sont dans le même AS ou pas.
    - SI NON et que le protocole est RIP, il ajoute l'autre routeur du lien en BGP neighbor, et désactive RIP.

- le script construit rip_router_names: pour chaque paire de router(A,B) si l'AS number est identique il ajoute IBGP dans les deux sens sur les loopback.

- ensuite va voir si un objet options existe {"100-200": "provider/customer"} sinon peer

- Ensuite configure le template à l'aide de Jinja2.

---

# Détail du template `router_bgp_rip.j2` (ligne par ligne)

Le fichier `gen_config_bgp_rip/router_bgp_rip.j2` est un template Jinja2 qui produit une configuration Cisco IOS.

Rappel Jinja2 :
- `{{ ... }}` : insère une valeur calculée par Python.
- `{% for ... %}` / `{% if ... %}` : boucle/condition (logique de génération).
- `!` : séparateur/commentaire IOS.

Variables principales injectées par le générateur :
- `router_name` : nom du routeur (ex: R6)
- `router_id` : BGP router-id (format IPv4), ex `6.6.6.6`
- `loopback_ip` : IPv6 loopback (souvent `/128`), ex `2000::6`
- `asn` : ASN du routeur
- `interfaces[]` : interfaces avec `name`, `ip`, `prefix` et `rip_enabled`
- `neighbors[]` : voisins BGP avec `ip`, `asn`, `name`, `is_ibgp`, `relationship`
- `networks[]` : (optionnel) préfixes à annoncer via `network ...`
- `is_border` : vrai si le routeur a au moins un voisin eBGP
- `options` : options (`policies_enabled`, `secure_redist`, `bgp_relations`)

## 1) Identité et activation IPv6
```text
hostname {{ router_name }}
ipv6 unicast-routing
ipv6 cef
```
- `hostname ...` : facilite l’identification du routeur.
- `ipv6 unicast-routing` : active le routage IPv6 globalement (sinon pas de forwarding IPv6).
- `ipv6 cef` : active CEF (forwarding plus performant/stable en lab).

## 2) Loopback0 (identité stable + support iBGP)
```text
interface Loopback0
 ipv6 address {{ loopback_ip }}/128
 ipv6 rip RIPNG enable
```
- `Loopback0` : interface logique (reste UP tant que le routeur est UP).
- `ipv6 address .../128` : adresse “identité” du routeur (host route).
- `ipv6 rip RIPNG enable` : annonce la loopback via RIPng → indispensable pour que les loopbacks soient joignables (utile pour l’iBGP par loopback).

## 3) Interfaces physiques (boucle Jinja2)
```text
{% for iface in interfaces %}
interface {{ iface.name }}
 ipv6 address {{ iface.ip }}/{{ iface.prefix }}
 {% if iface.rip_enabled %}
 ipv6 rip RIPNG enable
 {% endif %}
 no shutdown
{% endfor %}
```
- La boucle `for` génère un bloc par interface décrite dans le JSON.
- `ipv6 address ...` : configure l’IPv6 sur le lien.
- `iface.rip_enabled` : contrôlé par Python.
    - `True` : RIPng est activé sur l’interface.
    - `False` : RIPng n’est pas activé (typiquement sur un lien eBGP inter-AS pour éviter RIP entre AS).
- `no shutdown` : met l’interface en état UP.

## 4) RIPng global + injection de route par défaut si bordure
```text
ipv6 router rip RIPNG
 {% if is_border %}
 ipv6 rip RIPNG default-information originate
 {% endif %}
```
- `ipv6 router rip RIPNG` : crée/active le processus RIPng.
- Si `is_border` : le routeur injecte `::/0` dans RIPng.
    - But : les routeurs internes n’apprennent pas toute la table externe; ils envoient l’externe vers la bordure via une route par défaut.

## 5) BGP (déclaration globale)
```text
router bgp {{ asn }}
 bgp router-id {{ router_id }}
 no bgp default ipv4-unicast
```
- `router bgp <asn>` : démarre BGP dans l’AS.
- `bgp router-id <router_id>` : identifiant BGP (format IPv4), même si on fait IPv6.
- `no bgp default ipv4-unicast` : évite de gérer IPv4; on utilisera uniquement l’address-family IPv6.

## 6) Voisins BGP
```text
{% for neighbor in neighbors %}
 neighbor {{ neighbor.ip }} remote-as {{ neighbor.asn }}
 neighbor {{ neighbor.ip }} description to_{{ neighbor.name }}_{{ neighbor.relationship|default('peer') }}
 {% if neighbor.is_ibgp %}
 neighbor {{ neighbor.ip }} update-source Loopback0
 neighbor {{ neighbor.ip }} next-hop-self
 {% endif %}
{% endfor %}
```
- `remote-as` : ASN du voisin.
- `description ...` : debug/lisibilité; inclut `relationship` (customer/peer/provider).
- iBGP uniquement (`neighbor.is_ibgp`) :
    - `update-source Loopback0` : force la source TCP/BGP à être la loopback locale. Sans ça, iBGP via loopback échoue souvent (source par défaut = interface de sortie).
    - `next-hop-self` : évite des next-hop injoignables côté autres routeurs iBGP.

### Exemple R1–R2–R3 (iBGP + eBGP)

Topologie: R1 — R2 — R3, avec R1 et R2 dans le même AS (ex: AS100) et R3 dans un autre (ex: AS200).

- Sessions BGP:
    - R1 ↔ R2: iBGP via les adresses de `Loopback0` (grâce à `neighbor ... update-source Loopback0`).
    - R2 ↔ R3: eBGP sur l’interface physique du lien R2–R3 (pas d’`update-source` ni d’`ebgp-multihop` ici).

- Routage interne (R1–R2): RIPng transporte les préfixes internes et les loopbacks. Comme R2 est « bordure » (`is_border=True`), il injecte `::/0` dans RIPng (`default-information originate`) pour que R1 envoie le trafic externe vers R2.

- Flux d’annonces de routes:
    - R3 → R2 (eBGP): R2 apprend des préfixes externes avec `next-hop = adresse R3` (interface du lien). Quand R2 réannonce ces routes à R1 (iBGP), le `next-hop-self` fait que R2 **remplace** le next-hop par **sa Loopback0**. Résultat: R1 sait joindre ce next-hop via IGP/RIPng et peut réellement forwarder le trafic vers R2.
    - R1 → R2 (iBGP): R1 annonce ses préfixes internes (loopback, connected via `redistribute rip`) à R2. Vers R3 (eBGP), ce que R2 annonce dépend des politiques: si `options.policies_enabled=True` et que R3 est `peer`/`provider`, les route-maps appliquent les règles valley-free (on n’annonce typiquement que les routes « customer »). Sans politiques, R2 pourrait annoncer davantage.

- Ce qui casserait sans `next-hop-self` sur iBGP: R1 recevrait depuis R2 une route dont le next-hop reste **l’adresse de R3**. Comme cette adresse n’est pas dans l’IGP interne, R1 ne sait pas y aller: la route BGP devient inopérante (non installée/invalidée), et le trafic vers ce préfixe échoue.

- Pourquoi `update-source Loopback0` sur iBGP: la session TCP BGP utilise la loopback comme source, ce qui rend la **session indépendante** des états des interfaces physiques. Il faut en contrepartie que l’IGP (ici RIPng) rende les loopbacks **joignables** entre R1 et R2.

En résumé: R2, en tant que routeur de bordure, apprend l’externe depuis R3, **réécrit le next-hop** vers lui-même pour les pairs iBGP, et fournit une route par défaut aux internes. R1 parle iBGP avec R2 via loopback, apprend l’externe avec un next-hop atteignable (Loopback de R2) et envoie son trafic externe vers R2.

## 7) Address-family IPv6 unicast (activation + policies optionnelles)
```text
address-family ipv6 unicast
 {% for neighbor in neighbors %}
    neighbor {{ neighbor.ip }} activate
    {% if options.policies_enabled %}
     neighbor {{ neighbor.ip }} send-community
     {% if not neighbor.is_ibgp %}
     neighbor {{ neighbor.ip }} route-map MAP_FROM_{{ neighbor.relationship|upper }} in
     neighbor {{ neighbor.ip }} route-map MAP_TO_{{ neighbor.relationship|upper }} out
     {% endif %}
    {% endif %}
 {% endfor %}
```
- `neighbor ... activate` : active ce voisin pour l’IPv6.
- `send-community` : autorise l’envoi des “tags” BGP (communities) au voisin.
    - Ces tags sont utilisés par les politiques (ex: marquer une route comme venant d’un customer).
- Route-maps **seulement en eBGP** (`not neighbor.is_ibgp`) : on évite d’appliquer ces politiques sur iBGP.

## 8) Réseaux annoncés + redistribution
```text
{% if networks %}
 network <prefix>
{% endif %}
network {{ loopback_ip }}/128
redistribute connected
{% if options.secure_redist %}
 redistribute rip RIPNG route-map RIP_TO_BGP
{% else %}
 redistribute rip RIPNG
{% endif %}
```
- `network <prefix>` : annonce BGP explicite (pour les préfixes listés dans `networks[]`).
- `network <loopback>/128` : annonce toujours la loopback.
- `redistribute connected` : injecte les routes connected dans BGP (pratique mais peut annoncer “trop” si tu as des interfaces non prévues).
- `redistribute rip RIPNG` : injecte RIPng dans BGP.
    - Si `secure_redist=True`, passe par une route-map pour pouvoir filtrer/contrôler.

## 9) Route-map de redistribution (option `secure_redist`)
```text
route-map RIP_TO_BGP permit 10
```
- Point d’accroche pour filtrer la redistribution RIP → BGP.
- Tel quel (permit sans match), ça laisse tout passer, mais c’est prêt à être renforcé.

## 10) Politiques Gao-Rexford (option `policies_enabled`)
Ce bloc :
- définit des communities (`asn:10`, `asn:20`, `asn:30`) pour marquer l’origine des routes (customer/peer/provider)
- applique des préférences en entrée (local-preference : customer > peer > provider)
- applique des règles “valley-free” en sortie (vers peer/provider on annonce seulement les routes customer)

## 11) Fin
```text
end
write memory
```
- `write memory` : sauvegarde la config (utile en lab).