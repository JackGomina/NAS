import re

def get_router_number(router_name):
    """
    Extracts the router index from its name.
    Supports formats like "R1", "AS100_R2", "Router-5".
    Logic: Uses the Last sequence of digits found in the name.
    Returns 1 if no digits are found.
    """
    # Find all digit sequences
    numbers = re.findall(r'\d+', router_name)
    
    if not numbers:
        return 1
    
    # Return the last number found (assuming "AS100_R2" -> we want 2, not 100)
    return int(numbers[-1])

def get_router_id(router_name):
    """
    Generates a standard BGP Router ID (IPv4 format) based on the router name.
    Example: R1 -> 1.1.1.1, R15 -> 15.15.15.15
    """
    num = get_router_number(router_name)
    # Cap at 255 for octet validty if needed, but usually router-id is just a 32bit int.
    # For simplicity in GNS3 labs, N.N.N.N is standard convention.
    if num > 255:
        # Fallback logic for high numbers to avoid invalid IP format if strictly checked,
        # though router-id can be any 32-bit value. 
        # But let's keep it simple:
        b = num % 255
        return f"{b}.{b}.{b}.{b}"
        
    return f"{num}.{num}.{num}.{num}"

def get_loopback_ip(router_name, fmt="simple", as_number=None):
    """
    Generates an IPv6 Loopback address based on the selected format.
    Formats:
      - 'simple': 2000::{ID}
      - 'with_as': 2000:2:{AS}::{ID}
    """
    num = get_router_number(router_name)
    
    if fmt == "simple":
        return f"2000::{num}"
    
    elif fmt == "with_as":
        # Format: 2000:2:AS::ID
        # On utilise le bloc '2' pour distinguer les Loopbacks des Liens physiques (souvent en bloc '1')
        if as_number:
            try:
                # On utilise directement la string pour l'AS et l'ID (Decimal-in-Hex) pour la lisibilité
                return f"2000:2:{as_number}::{num}"
            except ValueError:
                return f"2000::2:{num}" # Fallback
        else:
            return f"2000::2:{num}"
    
    return f"2000::{num}"
