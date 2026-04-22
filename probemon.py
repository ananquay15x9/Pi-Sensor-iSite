#!/usr/bin/python3

"""
probemon.py - local ping collector
reads all settings from config.json 

usage:
    sudo python3 probemon.py -i mon0
"""

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
import logging
import requests
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from scapy.all import sniff, Dot11
 
import config as cfg_mod
 
#  ── setup ─────────────────────────────────────────────────────────────────────
def build_settings(cfg: dict) -> dict:
    """ 
    Pull all probemon settings from config.json into one flat dict
    """
    t = cfg["thresholds"]
    m = cfg["merge"]
    return {
        "node_id":          cfg["device"]["id"],
        "aggregator_url":   "http://127.0.0.1:5000/report",
        "rssi_floor":       t["rssi_floor"],
        "merge_window_sec": m["window_sec"],
        "rssi_tolerance":   m["rssi_tolerance"],
        "per_mac_cooldown": 2,       # seconds — rate limit per MAC
        "device_ttl_min":   2,       # minutes — rolling memory window
        "calibration_sec":  10,      # warm-up period
        "non_phone_keywords": [
            "intel", "hewlett", "dell", "lenovo", "apple mac",
            "canon", "epson", "brother", "xerox", "lexmark", "ricoh", "kyocera",
            "cisco", "ubiquiti", "tp-link", "netgear", "asus", "aruba",
            "ruckus", "d-link", "zyxel", "mikrotik", "juniper",
            "amazon", "roku", "nvidia", "microsoft", "belkin",
            "espressif", "murata", "texas instru", "hon hai",
        ],
        "blacklisted_macs": cfg_mod.get_blacklisted_macs(cfg),
        "edge_macs":        cfg_mod.get_edge_device_macs(cfg),  # mac -> label
        "pi_ouis":          cfg_mod.get_raspberry_pi_ouis(),
        "hopping": {
            "home_interface":   "wlan1",
            "home_dwell_sec":   cfg["hopping"]["home_dwell_sec"]
                                if "hopping" in cfg else 0.40,
            "visit_dwell_sec":  cfg["hopping"]["visit_dwell_sec"]
                                if "hopping" in cfg else 0.12,
        },
    }
    

# ── helpers ───────────────────────────────────────────────────────────────────
def is_randomized_mac(mac: str) -> bool:
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except Exception:
        return False

def get_org(mac: str) -> str:
    try:
        import netaddr
        return netaddr.EUI(mac).oui.registration().org
    except Exception:
        return "UNKNOWN"

def is_phone_like(mac: str, org: str, settings: dict) -> bool:
    if is_randomized_mac(mac):
        return True
    ol = org.lower()
    for kw in settings["non_phone_keywords"]:
        if kw in ol:
            return False
    return True

def should_ignore(mac: str, org: str, settings: dict) -> bool:
    """Filter out junk but not edge devices.
        Edge devices are for calibration so allow them through as ANCHOR reports
        1. edge devices -> pass through (reported as ANCHOR, not as people)
        2. blaclisted -> drop completely
    """
    # edge devices are always allowed
    if mac in settings["edge_macs"]:
        return False
    if mac in settings["blacklisted_macs"]:
        return True
    prefix = ":".join(mac.split(":")[:3]).lower()
    if prefix in settings["pi_ouis"]:
        return True
    if "raspberry" in org.lower():
        return True
    return False



def report(mac: str, rssi: int, org: str, randomized: bool, settings: dict):
    try:
        requests.post(
            settings["aggregator_url"],
            json={
                "node":       settings["node_id"],
                "mac":        mac,
                "rssi":       rssi,
                "org":        org,
                "randomized": randomized,
                "timestamp":  datetime.now().isoformat(),
            },
            timeout=0.5,
        )
    except Exception:
        pass
    
# ── channel hopping ───────────────────────────────────────────────────
def get_home_channel(home_iface: str) -> int:
    try:
        out = subprocess.check_output(["iw", "dev", home_iface, "info"], text=True)
        for line in out.splitlines():
            if "channel" in line.lower():
                return int(line.strip().split()[1])
    except Exception:
        pass
    return 6

def channel_hopper(iface: str, settings: dict):
    hop      = settings["hopping"]
    home_if  = hop["home_interface"]
    home_dwell  = hop["home_dwell_sec"]
    visit_dwell = hop["visit_dwell_sec"]
 
    home  = get_home_channel(home_if)
    visit = [ch for ch in [1, 6, 11] if ch != home]
    print(f"[HOP] Home ch:{home} | Visiting:{visit}", flush=True)
 
    def set_ch(ch):
        subprocess.run(
            ["iw", "dev", iface, "set", "channel", str(ch)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
 
    while True:
        set_ch(home);      time.sleep(home_dwell)
        set_ch(visit[0]);  time.sleep(visit_dwell)
        set_ch(home);      time.sleep(home_dwell)
        if len(visit) > 1:
            set_ch(visit[1]); time.sleep(visit_dwell)

            
# ───────────────────core detection──────────────────────────────────────────────────────────
 
class ProbeState:
    def __init__(self, settings: dict):
        self.s            = settings
        self.devices      = {}       # mac -> True (seen this segment)
        self.last_seen    = {}       # mac -> datetime
        self.active_groups = []      # [{rssi, time}] for MAC rotation detection
        self.segment_start = None

    def reset_segment(self):
        print(
            f"\n── Segment reset. Unique this window: {len(self.devices)} ──",
            flush=True,
        )
        self.devices.clear()
        self.active_groups.clear()
        self.segment_start = datetime.now()

    def process(self, mac: str, rssi: int, org: str, randomized: bool, logger) -> bool:
        """
        Returns True if this probe should be forwarded to the aggregator.
        Handles rate limiting and MAC rotation grouping internally.
        """
        now      = datetime.now()
        s        = self.s
        is_known = mac in self.last_seen

        if self.segment_start is None:
            self.segment_start = now

        # rolling window reset
        if now - self.segment_start > timedelta(minutes=s["device_ttl_min"]):
            self.reset_segment()

        if is_known:
            #same mac returning - do not touch rotation groups
            if (now - self.last_seen[mac]).total_seconds() < s["per_mac_cooldown"]:
                return False
        else:
        # brand new mac - check if it looks like a rotation of a known device
        # MAC rotation detection — same physical device, rotating random MAC
            for group in self.active_groups:
                time_diff = (now - group["time"]).total_seconds()
                rssi_diff = abs(rssi - group["rssi"])
                if time_diff < s["merge_window_sec"] and rssi_diff <= s["rssi_tolerance"]:
                    group["time"] = now  # refresh group
                    return False         # same device, skip

        # hard RSSI floor
        if rssi < s["rssi_floor"]:
            return False

        # record
        self.devices[mac] = True
        self.last_seen[mac] = now
        if not is_known:
            #only create a new group entry for first sighting of this mac
            self.active_groups.append({"rssi": rssi, "time": now})
   

        #prune expired groups
        self.active_groups[:] = [
            g for g in self.active_groups
            if (now - g["time"]).total_seconds() < s["merge_window_sec"] * 2
        ]

        ts    = now.strftime("%H:%M:%S")
        zone  = "CLOSE " if rssi >= -40 else "NEARBY"
        label = "~rand" if randomized else org[:24]
        line  = f"[{ts}] {zone} | {rssi:>4}dBm | {mac} | {label}"
        print(line, flush=True)
        logger.info(line)
 
        return True
# ── packet callback ─────────────────────────────────────────────────────────────────────
def build_callback(logger, state: ProbeState, settings: dict):
    start_time     = datetime.now()
    calibration    = timedelta(seconds=settings["calibration_sec"])
    in_calibration = [True] 


    def callback(packet):
        try:
            if not packet.haslayer(Dot11):
                return
            if packet.type != 0 or packet.subtype != 0x04:
                return

            mac  = packet.addr2
            rssi = packet.dBm_AntSignal

            if not mac:
                return

            mac = mac.lower()

            if should_ignore(mac, "", settings):
                return

            # edge device Pi - report as ANCHOR beacon for calibration reference
            if mac in settings["edge_macs"]:
                label = settings["edge_macs"][mac]
                report(mac, rssi, f"ANCHOR:{label}", False, settings)
                return

            randomized = is_randomized_mac(mac)
            org        = "RANDOMIZED" if randomized else get_org(mac)

            if not randomized and not is_phone_like(mac, org, settings):
                return

            # calibration warm up
            if in_calibration[0]:
                print(".", end="", flush=True)
                if (datetime.now() - start_time) > calibration:
                    in_calibration[0] = False
                    print(
                        f"\n── {settings['node_id']} ACTIVE | "
                        f"Floor:{settings['rssi_floor']}dBm | "
                        f"Merge window:{settings['merge_window_sec']}s ──\n",
                        flush=True,
                    )
                return

            if state.process(mac, rssi, org, randomized, logger):
                report(mac, rssi, org, randomized, settings)

        except Exception:
            pass

    return callback
    
# ── main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Probe Mesh — Local Collector")
    parser.add_argument(
        "-i", "--interface", required=True,
        help="Monitor-mode wireless interface (e.g. mon0)"
    )
    parser.add_argument(
        "--config", default=cfg_mod.CONFIG_PATH, metavar="PATH",
        help=f"Path to config.json (default: {cfg_mod.CONFIG_PATH})"
    )
    args = parser.parse_args()
 
    cfg      = cfg_mod.load(args.config)
    settings = build_settings(cfg)
    iface    = args.interface
 
    # logger
    logger = logging.getLogger("probemon")
    logger.setLevel(logging.INFO)
    logger.addHandler(
        RotatingFileHandler("probemon.log", maxBytes=5_000_000, backupCount=3)
    )

    print(f"── [{settings['node_id']}] Starting {settings['calibration_sec']}s calibration ──", flush=True)
    print(f"── Interface: {iface} | Aggregator: {settings['aggregator_url']} ──", flush=True)
    print(f"── RSSI floor: {settings['rssi_floor']}dBm | "
          f"Merge: {settings['merge_window_sec']}s ±{settings['rssi_tolerance']}dBm ──", flush=True)
    print(f"── Blacklisted MACs: {len(settings['blacklisted_macs'])} ──", flush=True)
 
    # channel hopper
    threading.Thread(
        target=channel_hopper, args=(iface, settings), daemon=True
    ).start()

    state    = ProbeState(settings)
    callback = build_callback(logger, state, settings)

    def handle_exit(sig, frame):
        print(f"\n[{settings['node_id']}] Shutting down. "
              f"Unique devices this session: {len(state.devices)}")
        os._exit(0)

    signal.signal(signal.SIGINT, handle_exit)

    while True:
        try:
            sniff(iface=iface, prn=callback, store=0, timeout=5)
        except Exception:
            continue


if __name__ == "__main__":
    main()
