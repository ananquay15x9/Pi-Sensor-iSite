#!/usr/bin/python3
"""
Listening Node — WiFi Probe Request Scanner
Captures real probe requests via tshark and reports to Brain Pi.

Usage:
    sudo python3 sniffer.py -i mon0
"""

import subprocess
import requests
import time
import signal
import os
import argparse
import threading
from datetime import datetime

# ── Config — edit these per node ─────────────────────────────────────────────
BRAIN_URL  = "http://100.124.55.96:5000/report"
NODE_ID    = "Listener2"
RETRY_DELAY = 5

RSSI_FLOOR = -75 #dBm

# ─────────────────────────────────────────────────────────────────────────────

# skip if see any of these
NON_PHONE_KEYWORDS = [
    # computers / peripherals
    'intel', 'hewlett', 'dell', 'lenovo', 'apple mac',
    # printers
    'canon', 'epson', 'brother', 'xerox', 'lexmark', 'ricoh', 'kyocera',
    # networking gear
    'cisco', 'ubiquiti', 'tp-link', 'netgear', 'asus', 'aruba',
    'ruckus', 'd-link', 'zyxel', 'mikrotik', 'juniper',
    # streaming / smart TV / IoT
    'amazon', 'roku', 'nvidia', 'microsoft', 'belkin',
    'espressif', 'murata', 'texas instru',
]

# Specific MACs to always ignore 
BLACKLIST_MACS: set = set()
# ─────────────────────────────────────────────────────────────────────────────
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
    True if this device is plausibly a phone/tablet.
    Randomized MACs pass automatically.
    Unknown OUIs pass (cheap phone brands often have obscure OUIs).
    """
    if is_randomized_mac(mac):
        return True
    org_lower = org.lower()
    for kw in NON_PHONE_KEYWORDS:
        if kw in org_lower:
            return False
    return True


def report_to_brain(mac: str, rssi: int, org: str, randomized: bool) -> bool:
    try:
        payload = {
            "node":       NODE_ID,
            "mac":        mac,
            "rssi":       rssi,
            "org":        org,
            "randomized": randomized,
        }
        resp = requests.post(BRAIN_URL, json=payload, timeout=1)
        return resp.status_code == 200
    except Exception:
        return False


def run_tshark(iface: str) -> subprocess.Popen:
    """
    Capture only probe request frames and emit two tab-separated fields:
      wlan.sa              — source MAC address
      radiotap.dbm_antsignal — signal strength in dBm
    """
    cmd = [
        "tshark",
        "-i", iface,
        "-f", "type mgt subtype probe-req",
        "-T", "fields",
        "-e", "wlan.sa",
        "-e", "radiotap.dbm_antsignal",
        "-E", "separator=\t",
        "-l",
    ]
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )


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
    """
    Weighted hop: stay on home channel (400ms) so wlan1 stays associated,
    briefly visit the other two main channels (120ms each) to catch phones.
    120ms is well under the ~300ms AP disassociation threshold.
    Pattern: home → ch1 → home → ch11 → repeat
    """
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


def main():
    parser = argparse.ArgumentParser(description="Listener Pi probe request scanner")
    parser.add_argument(
        '-i', '--interface', required=True,
        help="Monitor-mode wireless interface (e.g. mon0)",
    )
    args = parser.parse_args()
    iface = args.interface

    print(f"[{NODE_ID}] Starting on interface {iface}")
    print(f"[{NODE_ID}] Reporting to Brain: {BRAIN_URL}")
    print(f"[{NODE_ID}] RSSI floor: {RSSI_FLOOR}dBm")


    
    # start channel hopper in background
    threading.Thread(target=channel_hopper, args=(iface,), daemon=True).start()

    reported_count = 0

    while True:
        proc = None
        try:
            proc = run_tshark(iface)
            print(f"[{NODE_ID}] tshark active — listening for probe requests...")

            for line in proc.stdout:
                parts = line.strip().split('\t')
                if len(parts) < 2:
                    continue

                mac =      parts[0].strip()
                rssi_str = parts[1].strip()

                # Skip malformed lines
                if not mac or len(mac) != 17:
                    continue

                if mac.lower() in BLACKLIST_MACS:
                    continue

                
                try:
                    # tshark may emit "-27,-27" for radiotap.dbm_antsignal
                    rssi = int(rssi_str.split(",")[0].strip())
                except ValueError:
                    continue

                # drop signals below the floor - outside of the area
                if rssi < RSSI_FLOOR:
                    continue

                randomized = is_randomized_mac(mac)

                org = "RANDOMIZED" if randomized else get_org(mac)

                #phone only filter
                if not randomized and not is_phone_like(mac, org):
                    continue


                ts    = datetime.now().strftime('%H:%M:%S')

                label = "📱~rand" if randomized else f"📱 {org[:22]}"
                print(f"[{ts}] {label} | {mac} | {rssi}dBm")

                ok = report_to_brain(mac, rssi, org, randomized)
                if ok:
                    reported_count += 1
                else:
                    print(
                        f"[{NODE_ID}] ⚠ Brain unreachable "
                        f"(reported so far: {reported_count})"
                    )

        except KeyboardInterrupt:
            print(f"\n[{NODE_ID}] Shutting down. Total reported: {reported_count}")
            break
        except Exception as e:
            print(f"[{NODE_ID}] Error: {e} — restarting tshark in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)
        finally:
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda s, f: os._exit(0))
