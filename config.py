"""
config.py — Load, validate, and save config.json.
 
All other modules import from here. No logic lives here.
"""

import json
import os
from typing import Any

CONFIG_PATH = os.environ.get("PROBE_CONFIG", "config.json")


_DEFAULTS: dict = {
    "device": {
        "id": "sensor-1",
        "role": "brain",
        "log_file": "visitors.log",
        "daily_report_file": "daily_reports.log",
    },
    "network": {
        "nodes": ["MainBrain", "Listener1", "Listener2"],
        "edge_devices": [],
        "blacklisted": [],
    },
    "thresholds": {
        "rssi_floor":               -75,
        "inside_avg_rssi":          -68,
        "inside_min_best_rssi":     -68,
        "inside_min_sightings":      3,
        "inside_min_nodes":          1,
        "inside_confirm_window":     3,
        "inside_confirm_min_passes": 2,
        "visitor_timeout_sec":       60,
        "zone_close":               -40,
        "zone_nearby":              -60,
    },
    "merge": {
        "window_sec":     4,
        "rssi_tolerance": 4,
    },
    "occupancy": {
        "window_minutes":              10,
        "sample_sec":                   5,
        "history_max_points":       10000,
        "impression_retention_minutes": 1440,
    },
    "console": {
        "refresh_sec": 5,
        "color":       True,
        "debug":       False,
    },
    "http": {
        "host": "0.0.0.0",
        "port": 5000,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load(path: str = CONFIG_PATH) -> dict:
    """
    Load config from path, filling in any missing keys from defaults.
    Never raises on missing keys — always returns a complete config dict.
    """
    if not os.path.exists(path):
        return _deep_merge({}, _DEFAULTS)
    try:
        with open(path) as f:
            raw = json.load(f)
        return _deep_merge(_DEFAULTS, raw)
    except Exception as e:
        print(f"[config] Warning: could not load {path}: {e} — using defaults")
        return _deep_merge({}, _DEFAULTS)
 
 
def save(cfg: dict, path: str = CONFIG_PATH) -> None:
    """Write cfg to path as pretty JSON."""
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"[config] Saved → {path}")



def get_nodes(cfg: dict) -> list:
    return cfg["network"]["nodes"]
 
def get_edge_device_macs(cfg: dict) -> dict:
    """Returns {mac: label} for all configured edge devices."""
    return {
        d["mac"].lower(): d["id"]
        for d in cfg["network"].get("edge_devices", [])
        if "mac" in d and "id" in d
    }
 
def get_blacklisted_macs(cfg: dict) -> set:
    return {m.lower() for m in cfg["network"].get("blacklisted", [])}
 
def get_threshold(cfg: dict, key: str) -> Any:
    return cfg["thresholds"][key]
 
def get_raspberry_pi_ouis() -> frozenset:
    return frozenset({
        "b8:27:eb", "dc:a6:32", "28:cd:c1",
        "2c:cf:67", "e4:5f:01", "d8:3a:dd",
    })
