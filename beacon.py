#!/usr/bin/python3
"""
beacon.py — Calibration Pi Beacon

Run this on each temporary edge-device Pi during 10 minutes
calibration session.
Its only job: make this Pi consistently visible on the 2.4 GHz
band so the sensor Pi can measure its signal strength (RSSI)
and compute thresholds.

Why I create this small script..
- A Pi is powerd on and connected to WiFi is still a little bit weak
- I checked and saw that once connected and idle, a Pi can sit for
minutes without transmitting a single 2.4 GHz frame that a monitor-mode
would capture.
- The Sensor Pi's Panda dongle in monitor mode captures signal request
frames so -> edge device should actively sending request for networks

Core of this script:
- It sends signal every 3 seconds, so we guarantee the sensor receives a
steady probe frames from this Pi - enough to build a reliable RSSI median
over the 10-minute window (~200 samples).
- We use 2.4 GHz specifically because the Panda dongle operates on 2.4 GHz only
even though a Pi can scans on 5 GHz.

PLACEMENT
-------------
Just don't place it next to the sensor Pi.

The sensor scans to the WEAKEST edge device.
Closer to the sensor: ~-10 to -35 dBm (close)
Further to the sensor: ~ -40 to -70 dBm (weak)

Usage:
    sudo python3 beacon.py

No config file needed — just run it and leave it running for the
full calibration period. Stop it when calibration is done.
"""

import subprocess
import time
import signal
import os
import sys
from datetime import datetime

#how often to send signal (seconds)
# 3 seconds gives ~200 samples over a 10-minute calibration
BEACON_INTERVAL = 3

# use the built-in chip (wlan0)
PROBE_INTERFACE = "wlan0"

def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# 2.4Ghz to match Panda dongle hopping channels
FREQ_2_4GHZ = ["2412", "2417", "2422", "2427", "2432", "2437",
                "2442", "2447", "2452", "2457", "2462"]

def emit_probe(iface: str):
    """
    trigger wifi requests by doing a quick scan. The sensor Pi's panda dongle will pick this up.
    """
    try:
        subprocess.run(
            ["iw", "dev", iface, "scan", "freq"] + FREQ_2_4GHZ,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
    except Exception:
        try:
            # Fallback: iwlist (older driver compat)
            subprocess.run(
                ["iwlist", iface, "scan"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
            )
        except Exception:
            pass


def get_mac(iface: str) -> str:
    """
    printed on startup so the sensor can confirm it matches the entry
    in config.json. If the MACs don't match, probe frames arrive at the
    sensor but are not recognized as calibration beacons. It will be
    treated as unknown devices and excluded from threshold computation.
    """
    try:
        out = subprocess.check_output(["ip", "link", "show", iface], text=True)
        for line in out.splitlines():
            if "link/ether" in line:
                return line.strip().split()[1]
    except Exception:
        pass
    return "unknown"

def main():
    mac = get_mac(PROBE_INTERFACE)
    log(f"Calibration beacon starting on {PROBE_INTERFACE}")
    log(f"This Pi's MAC: {mac}")
    log(f"Make sure this MAC is listed in config.json → network.edge_devices on the sensor Pi")
    log(f"Emitting probe burst every {BEACON_INTERVAL}s — keep running until calibration finishes")
    log("─" * 50)

    count = 0
    while True:
        emit_probe(PROBE_INTERFACE)
        count += 1
        log(f"Probe burst #{count} emitted")
        time.sleep(BEACON_INTERVAL)

def handle_exit(sig, frame):
    log("Beacon stopped.")
    sys.exit(0)


signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Run with sudo: sudo python3 beacon.py")
        sys.exit(1)
    main()
