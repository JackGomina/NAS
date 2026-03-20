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

def get_loopback_ip(router_name, as_number=None, **kwargs):
    """
    Generates an IP Loopback address.
    Format: 10.255.AS.ID
    """
    num = get_router_number(router_name)
    
    if as_number:
        try:
            numeric_as = int(as_number)
            if numeric_as > 255:
                numeric_as = numeric_as % 255
            return f"10.255.{numeric_as}.{num}"
        except ValueError:
            return f"10.255.255.{num}" # Fallback
    else:
        return f"10.255.255.{num}"
