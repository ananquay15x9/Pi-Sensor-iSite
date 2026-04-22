
"""
utils.py — Shared pure helpers.

Safe to import from anywhere.
"""

def friendly_device_name(device) -> str:
    """Human-readable label for a DeviceRecord."""
    org       = (device.org or "").strip()
    org_lower = org.lower()
    unknown   = {"", "unknown", "private", "randomized"}
 
    if org_lower not in unknown:
        if "anchor:" in org_lower or "listener" in org_lower or "raspberry pi" in org_lower:
            kind = "Pi"
        elif "apple" in org_lower or "samsung" in org_lower or "google" in org_lower:
            kind = "Phone"
        elif "intel" in org_lower or "dell" in org_lower or "lenovo" in org_lower:
            kind = "Laptop"
        else:
            kind = "Device"
        return f"{org} {kind}".strip()
 
    if device.classification() == "likely_phone" or device.randomized:
        return "Phone"
    if device.node_count >= 2 and device.sightings >= 4:
        return "Nearby device"
    return "Unknown device"
 
 
def human_event_label(event_type: str) -> str:
    return {"NEW": "Detected", "INSIDE": "Inside"}.get(event_type, event_type.title())
 
 
def is_randomized_mac(mac: str) -> bool:
    """True if MAC is locally-administered (randomized by phone OS)."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except Exception:
        return False
 
 
def masked_mac(mac: str) -> str:
    parts = mac.split(":")
    if len(parts) != 6:
        return mac
    return f"{parts[0]}:{parts[1]}:XX:XX:{parts[4]}:{parts[5]}"
 
