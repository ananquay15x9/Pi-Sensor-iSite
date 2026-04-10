#!/usr/bin/python3

"""
Brain Pi — Local Probe Sniffer (MainBrain node)
Scans probe requests on this Pi and reports to the local aggregator (sensor.py).
 
Usage:
    sudo python3 probemon.py -i wlan0mon
"""

import time
import subprocess
import threading
import signal
import os
import argparse
import requests
import logging
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from scapy.all import sniff, Dot11


# ── Config ────────────────────────────────────────────────────────────────────
AGGREGATOR_URL = "http://127.0.0.1:5000/report"
NODE_ID        = "MainBrain"

CALIBRATION_TIME = timedelta(seconds=10)

#  clear per-device memory
DEVICE_TTL     = timedelta(minutes=2)

#MAC rotation grouping
GROUP_WINDOW_SEC = 4
RSSI_TOLERANCE   = 3 #dbm

#devices weaker than this means they're outside the area
RSSI_FLOOR       = -75  # dBm

# don't count the same device more than once
PER_MAC_COOLDOWN = 2    # seconds

# channel hopping
#HOP_CHANNELS  = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
#HOP_INTERVAL  = 0.3   # seconds per channel


# ── Listener Pi anchor MACs ───────────────────────────────────────────────────
LISTENER_PI_MACS = {
    "9c:ef:d5:f8:9b:c1": "Listener1",
    "b8:27:eb:21:ff:f2": "Listener2",

}
# ───────────────────Non-Phone Filter──────────────────────────────────────────────────────────
 
NON_PHONE_KEYWORDS = [
    # computers / peripherals
    'intel', 'hewlett', 'dell', 'lenovo', 'apple mac',
    # printers / scanners
    'canon', 'epson', 'brother', 'xerox', 'lexmark', 'ricoh', 'kyocera',
    # networking gear
    'cisco', 'ubiquiti', 'tp-link', 'netgear', 'asus', 'aruba',
    'ruckus', 'd-link', 'zyxel', 'mikrotik', 'juniper',
    # streaming / smart TV / IoT
    'amazon', 'roku', 'nvidia', 'microsoft', 'belkin',
    'espressif',   # ESP8266/ESP32 IoT modules
    'murata',      # IoT modules
    'texas instru',
    'Hon Hai',     # Foxconn — often laptops
]

# MACs to always ignore regardless of OUI
BLACKLIST_MACS = {
    # '00:11:22:33:44:55',
}

# ── State ─────────────────────────────────────────────────────────────────────
devices       = {}
last_seen     = {}
active_groups = []
start_time    = datetime.now()
in_calibration = True
segment_start  = None
# ── State ─────────────────────────────────────────────────────────────────────


def is_randomized_mac(mac: str) -> bool:
    try:
        first_byte = int(mac.split(':')[0], 16)
        return bool(first_byte & 0x02)
    except Exception:
        return False
 
 
def get_org(mac: str) -> str:
    try:
        import netaddr
        return netaddr.EUI(mac).oui.registration().org
    except Exception:
        return "UNKNOWN"

def is_phone_like(mac: str, org: str) -> bool:
    """
    Return True if this device is plausibly a phone/tablet.
    Randomized MACs pass automatically (phones randomize; laptops generally don't).
    Non-phone org keywords cause rejection.
    """
    if is_randomized_mac(mac):
        return True
 
    org_lower = org.lower()
    for kw in NON_PHONE_KEYWORDS:
        if kw in org_lower:
            return False
 
    # If OUI is completely unknown, give it the benefit of the doubt —
    # many cheap phone brands have obscure OUIs.
    return True

    
def report_to_aggregator(mac: str, rssi: int, org: str, randomized: bool):
    try:
        payload = {
            "node":       NODE_ID,
            "mac":        mac,
            "rssi":       rssi,
            "org":        org,
            "randomized": randomized,
        }
        requests.post(AGGREGATOR_URL, json=payload, timeout=0.5)
    except Exception:
        pass

def get_home_channel() -> int:
    """Detect which channel wlan1 (WiFi) is on — stay there most of the time."""
    try:
        out = subprocess.check_output(["iw", "dev", "wlan1", "info"], text=True)
        for line in out.splitlines():
            if "channel" in line.lower():
                return int(line.strip().split()[1])
    except Exception:
        pass
    return 6  # safe fallback



def channel_hopper(iface: str):
    """Pattern: home -> ch1 -> home -> ch11 -> repeat"""
    home = get_home_channel()
    visit = [ch for ch in [1, 6, 11] if ch != home]
    print(f"[HOP] Home channel: {home} | Visiting: {visit}", flush=True)

    def set_ch(ch):
        subprocess.run(
            ["iw", "dev", iface, "set", "channel", str(ch)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    while True:
        set_ch(home);      time.sleep(0.40)
        set_ch(visit[0]);  time.sleep(0.12)
        set_ch(home);      time.sleep(0.40)
        if len(visit) > 1:
            set_ch(visit[1]); time.sleep(0.12)


def found_device(logger, mac: str, rssi: int, org: str, randomized: bool):
    global segment_start, in_calibration
 
    now = datetime.now()
 
    if segment_start is None:
        segment_start = now

    #came back later gets counted again
    if now - segment_start > DEVICE_TTL:
        print(
            f"\n── Segment reset. Unique this window: {len(devices)} ──",
            flush=True,
        )  
        devices.clear()
        active_groups.clear()
        segment_start = now

    #per mac rate limiting
    if mac in last_seen and (now - last_seen[mac]).total_seconds() < PER_MAC_COOLDOWN:
        return

    #mac rotation detection:
    for group in active_groups:
        time_diff = (now - group['time']).total_seconds()
        rssi_diff = abs(rssi - group['rssi'])
        if time_diff < GROUP_WINDOW_SEC and rssi_diff <= RSSI_TOLERANCE:
            group['time'] = now   # refresh the group window
            return

    # hard floor: ignore devices far away
    if rssi < RSSI_FLOOR:
        return

    #record this device
    devices[mac] = True
    last_seen[mac] = now
    active_groups.append({'rssi': rssi, 'time': now})

    # prune expired groups
    active_groups[:] = [
        g for g in active_groups
        if (now - g['time']).total_seconds() < GROUP_WINDOW_SEC * 2
    ]

    # forward to local sensor pi
    report_to_aggregator(mac, rssi, org, randomized)
     
    ts         = now.strftime('%H:%M:%S')
    zone_label = "CLOSE " if rssi >= -40 else "NEARBY"
    label      = "~rand" if randomized else org[:24]
    output     = f"[{ts}] {zone_label} | {rssi:>4}dBm | {mac} | {label}"
    print(output, flush=True)
    logger.info(output)


def build_packet_callback(logger):
    def packet_callback(packet):
        global in_calibration
        try:
            if not packet.haslayer(Dot11):
                return
            if packet.type != 0 or packet.subtype != 0x04:
                return

            mac = packet.addr2
            rssi = packet.dBm_AntSignal

            if not mac:
                return

            mac_lower = mac.lower()

            #hard blacklist
            if mac_lower in BLACKLIST_MACS:
                return

            randomized = is_randomized_mac(mac)

            #check if this is a known listener Pi
            if mac_lower in LISTENER_PI_MACS:
                listener_name = LISTENER_PI_MACS[mac_lower]
                # Report it as a special org so sensor.py can track it as an anchor
                report_to_aggregator(mac, rssi, f"ANCHOR:{listener_name}", randomized=False)
                return

            org = "RANDOMIZED" if randomized else get_org(mac)

            # phone filter
            if not randomized and not is_phone_like(mac, org):
                return

            # calibration period
            if in_calibration:
                print(".", end="", flush=True)
                if (datetime.now() - start_time) > CALIBRATION_TIME:
                    in_calibration = False
                    print(
                        f"\n── MainBrain ACTIVE | Floor: {RSSI_FLOOR}dBm | "
                        f"Rotation window: {GROUP_WINDOW_SEC}s / ±{RSSI_TOLERANCE}dBm ──\n",
                        flush=True,
                    )
                return

            found_device(logger, mac, rssi, org, randomized)

        except Exception:
            pass

    return packet_callback

def signal_handler(sig, frame):
    print(f"\n[{NODE_ID}] Shutting down. Unique devices this session: {len(devices)}")
    os._exit(0)
 
 
signal.signal(signal.SIGINT, signal_handler)

 
 
def main():
    parser = argparse.ArgumentParser(description="Brain Pi local probe scanner")
    parser.add_argument(
        '-i', '--interface', required=True,
        help="Monitor-mode wireless interface (e.g. wlan0mon)",
    )
    args = parser.parse_args()
 
    logger = logging.getLogger("newprobemon")
    logger.setLevel(logging.INFO)
    logger.addHandler(
        RotatingFileHandler('newprobemon.log', maxBytes=5_000_000, backupCount=3)
    )

    print(f"── [{NODE_ID}] Starting {CALIBRATION_TIME.seconds}s calibration... ──", flush=True)
    print(f"── Weighted channel hopping on {args.interface} ──", flush=True)
    print(f"── RSSI floor: {RSSI_FLOOR}dBm | MAC rotation window: {GROUP_WINDOW_SEC}s ──", flush=True)
    if LISTENER_PI_MACS:
        print(f"── Listener anchor MACs registered: {list(LISTENER_PI_MACS.values())} ──", flush=True)
    else:
        print("── No Listener Pi anchor MACs configured (optional) ──", flush=True)

    # start channel hopper as well
    threading.Thread(target=channel_hopper, args=(args.interface,), daemon=True).start()
     
    callback = build_packet_callback(logger)

    # main sniff loop
    while True:
        try:
            sniff(iface=args.interface, prn=callback, store=0, timeout=5)
        except Exception:
            continue

if __name__ == '__main__':
    main()
