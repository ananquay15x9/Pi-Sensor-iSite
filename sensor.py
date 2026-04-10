#!/usr/bin/python3
"""
Brain Pi —  Live Detector UI

Receives probe reports from all nodes, tracks active devices
Run:
    python3 sensor.py
"""

from flask import Flask, request, jsonify
from datetime import datetime, timedelta
from collections import defaultdict, deque
from dataclasses import dataclass, field
import threading
import time
import json
import os
from typing import Dict, List, Any, Optional

app = Flask(__name__)

# ── Env helpers ───────────────────────────────────────────────────────────────
def env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        return default

def env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None:
        return default
    try:
        return float(v)
    except ValueError:
        return default


# ── Config ────────────────────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 5000

LOG_FILE = "visitors.log"

# add/remove node IDs here if decide to add more Listener Pis
NODES = [
    "MainBrain",
    "Listener1",
    "Listener2",
]


# ── detection / lifecycle ─────────────────────────────────────────────────────
VISITOR_TIMEOUT  = timedelta(seconds=20)   # unseen for this long → exit
NODE_STALE_AFTER = timedelta(seconds=15)   # node health warning if quiet
EVENT_MEMORY     = 100                     # keep last N events in memory
                   
# ── Zone thresholds ───────────────────────────────────────────────────────────
ZONE_CLOSE  = env_int("ZONE_CLOSE", -40)  # dBm — "right in front"
ZONE_NEARBY = env_int("ZONE_NEARBY", -60)   # dBm — "in the room"

# ── RSSI-based area gating ───────────────────────────────────────────────────
# a device must pass BOTH of these to be counted as "inside":
INSIDE_AVG_RSSI      = env_int("INSIDE_AVG_RSSI", -60)   # dBm — averaged across all reporting nodes
INSIDE_MIN_BEST_RSSI = env_int("INSIDE_MIN_BEST_RSSI", -55)   # dBm — at least one node must see this strongly
INSIDE_MIN_SIGHTINGS = env_int("INSIDE_MIN_SIGHTINGS", 4)     # must be seen at least this many times total
INSIDE_MIN_NODES     = env_int("INSIDE_MIN_NODES", 1)     # minimum nodes that need to have seen it
# weak hits from far corners (common when one phone sits next to one listener) are excluded so they do not pull "inside" down
INSIDE_ROOM_NODE_PEAK_MIN = env_int("INSIDE_ROOM_NODE_PEAK_MIN", -72) #dBm

# confirm inside using a short vote window to tolerate noisy samples
INSIDE_CONFIRM_WINDOW = env_int("INSIDE_CONFIRM_WINDOW", 3)
INSIDE_CONFIRM_MIN_PASSES = env_int("INSIDE_CONFIRM_MIN_PASSES", 2)

# rolling occupancy + impression window stats
OCCUPANCY_WINDOW_MINUTES = env_int("OCCUPANCY_WINDOW_MINUTES", 10)
OCCUPANCY_SAMPLE_SEC = env_int("OCCUPANCY_SAMPLE_SEC", 5)
OCCUPANCY_HISTORY_MAX_POINTS = env_int("OCCUPANCY_HISTORY_MAX_POINTS", 10000)
IMPRESSION_RETENTION_MINUTES = env_int("IMPRESSION_RETENTION_MINUTES", 24 * 60)


# ── randomized MAC merging ─────────────────────────────────────────────────────
RANDOMIZED_MERGE_WINDOW_SEC = env_int("RANDOMIZED_MERGE_WINDOW_SEC", 15)
RANDOMIZED_RSSI_TOLERANCE   = env_int("RANDOMIZED_RSSI_TOLERANCE", 6)

# common Raspberry Pi
RASPBERRY_PI_OUIS = frozenset({
    "b8:27:eb", "dc:a6:32", "28:cd:c1", "2c:cf:67", "e4:5f:01", "d8:3a:dd",
})

# reduce spam for repeated logs
EVENT_COOLDOWN = timedelta(seconds=8)

# print live summary 
CONSOLE_REFRESH_SEC = 5

# ─────────────────────────────────────────────────────────────────────────────
lock = threading.Lock()

# global stats
total_ever_seen = 0
total_entries   = 0
total_exits     = 0
total_people_inside_events = 0

# node health
node_last_seen:    Dict[str, datetime] = {}
node_report_count: Dict[str, int]     = defaultdict(int)

# recent events
recent_events = deque(maxlen=EVENT_MEMORY)
occupancy_history = deque(maxlen=OCCUPANCY_HISTORY_MAX_POINTS)  # [{ts, inside}]
inside_entries_history = deque()  # [(ts, device_id)] for rolling impressions


#known Pi - tracked separately, never counted as visitors
anchor_devices: Dict[str, dict] = {}


@dataclass
class DeviceRecord:
    mac:                str
    device_id:          str
    org:                str
    randomized:         bool
    first_seen:         datetime
    last_seen:          datetime
    state:              str = "new"
    state_since:        Optional[datetime] = None
    last_event_at:      Optional[datetime] = None
    sightings:          int = 0
    last_report_node:   str = ""
    inside_since:       Optional[datetime] = None #when device first confirmed inside
    consecutive_inside: int = 0
    area_gate_votes:    List[int] = field(default_factory=list)
    rssi_by_node:       Dict[str, List[int]] = field(
        default_factory=lambda: defaultdict(list)
    )


    def __post_init__(self):
        if self.state_since is None:
            self.state_since = self.first_seen

    def add_sighting(self, node: str, rssi: int, now: datetime):
        self.last_seen =        now
        self.last_report_node = node
        self.sightings       += 1
        self.rssi_by_node[node].append(rssi)
        self.rssi_by_node[node] = self.rssi_by_node[node][-10:]

    @property
    def best_rssi(self) -> int:
        vals = [r for readings in self.rssi_by_node.values() for r in readings]
        return max(vals) if vals else -100

    @property
    def avg_rssi(self) -> float:
        """Mean of every stored RSSI sample across nodes (biased toward whoever reports most)."""
        vals = [r for readings in self.rssi_by_node.values() for r in readings]
        return round(sum(vals) / len(vals), 1) if vals else -100.0

    @property
    def avg_rssi_per_node(self) -> Dict[str, float]:
        """Per-node average of recent RSSI readings."""
        return {
            node: round(sum(readings) / len(readings), 1)
            for node, readings in self.rssi_by_node.items()
            if readings
        }

    @property
    def avg_rssi_room(self) -> float:
        """
        use median per-node - equal weight per node regardless of how many samples each reported
        this prevents one nearby Pi from dominating when a phone stands right next to it, giving a fairer
        room-wide picture
        """
        import statistics
        node_medians = []
        for node, readings in self.rssi_by_node.items():
            if readings:
                node_medians.append(statistics.median(readings))
        if not node_medians:
            return -100.0
        if len(node_medians) == 1:
            return round(node_medians[0], 1)
        return round(statistics.median(node_medians), 1)

    @property
    def strongest_node_avg_rssi(self) -> float:
        """Mean RSSI at whichever node currently has the strongest peak reading."""
        sn = self.strongest_node
        if sn == "Unknown":
            return -100.0
        return self.avg_rssi_per_node.get(sn, -100.0)

    @property
    def nodes_seen(self) -> List[str]:
        return sorted(self.rssi_by_node.keys())
    
    @property
    def node_count(self) -> int:
        return len(self.rssi_by_node)

    @property
    def dwell_seconds(self) -> int:
        return int((datetime.now() - self.first_seen).total_seconds())

    @property
    def strongest_node(self) -> str:
        best_node = None
        best_val =  -999
        for node, readings in self.rssi_by_node.items():
            if readings:
                node_best = max(readings)
                if node_best > best_val:
                    best_val = node_best
                    best_node = node
        return best_node or "Unknown"
    
    def zone(self) -> str:
        if self.best_rssi >= ZONE_CLOSE:
            return "close"
        if self.best_rssi >= ZONE_NEARBY:
            return "nearby"
        return "fringe"
    
    def confidence(self) -> float:
        score = 0.0
    
        score += min(self.node_count / 3.0, 1.0) * 0.40

        score += min(self.sightings / 6.0, 1.0) * 0.30

        # signal strength
        if self.best_rssi >= -45:
            score += 0.30
        elif self.best_rssi >= -55:
            score += 0.24
        elif self.best_rssi >= -65:
            score += 0.16
        elif self.best_rssi >= -75:
            score += 0.08

        if self.randomized:
            score += 0.08

        return round(min(score, 0.99),2)

    def classification(self) -> str:
        conf = self.confidence()
        if conf >= 0.75:
            return "likely_phone"
        if conf >= 0.45:
            return "visitor_candidate"
        return "uncertain"

    def masked_mac(self) -> str:
        parts = self.mac.split(":")
        if len(parts) != 6:
            return self.mac
        return f"{parts[0]}:{parts[1]}:XX:XX:{parts[4]}:{parts[5]}"

    def compact_nodes(self) -> str:
        return ", ".join(self.nodes_seen)


    def best_rssi_by_node(self) -> Dict[str, int]:
        return {
            node: max(readings)
            for node, readings in self.rssi_by_node.items()
            if readings
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "device_id":      self.device_id,
            "mac":            self.mac,
            "masked_mac":     self.masked_mac(),
            "org":            self.org,
            "randomized":     self.randomized,
            "classification": self.classification(),
            "confidence":     self.confidence(),
            "state":          self.state,
            "zone":           self.zone(),
            "first_seen":     self.first_seen.strftime("%Y-%m-%d %H:%M:%S"),
            "last_seen":      self.last_seen.strftime("%Y-%m-%d %H:%M:%S"),
            "dwell_seconds":  self.dwell_seconds,
            "sightings":      self.sightings,
            "nodes_seen":     self.nodes_seen,
            "node_count":     self.node_count,
            "strongest_node": self.strongest_node,
            "best_rssi":      self.best_rssi,
            "avg_rssi":       self.avg_rssi_room,
            "avg_rssi_all_samples": self.avg_rssi,
            "avg_rssi_by_node": {k: v for k, v in self.avg_rssi_per_node.items()},
            "rssi_by_node":   self.best_rssi_by_node(),
        }
        
        
# mac -> DeviceRecord
visitors: Dict[str, DeviceRecord] = {}

def mac_three_octets(mac: str) -> str:
    parts = mac.split(":")
    if len(parts) >= 3:
        return ":".join(parts[:3]).lower()
    return ""

def is_raspberry_pi_oui(mac: str) -> bool:
    return mac_three_octets(mac) in RASPBERRY_PI_OUIS

def should_ignore_probe_for_visitors(mac: str, org: str) -> bool:
    """
    Listener Pis are often heard as normal Wi-Fi probes (b8:27:…, etc.).
    Skip them for visitor state so they do not inflate people counts.
    """
    if mac in anchor_devices:
        return True
    if is_raspberry_pi_oui(mac):
        return True
    org_l = (org or "").lower()
    if "raspberry" in org_l and "phone" not in org_l:
        return True
    return False


def device_is_probable_phone(d: DeviceRecord) -> bool:
    """Same rules as dashboard 'People Detected' table."""
    if d.sightings < 2:
        return False
    if d.randomized:
        return True
    org_lower = (d.org or "").lower()
    phone_brands = ["apple", "samsung", "google", "oneplus", "xiaomi",
                    "huawei", "motorola", "sony", "lg electron", "oppo",
                    "realme", "vivo", "nothing tech"]
    if any(b in org_lower for b in phone_brands):
        return True
    if d.classification() == "likely_phone":
        return True
    return False


def should_count_toward_people_inside(d: DeviceRecord) -> bool:
    if d.state != "inside":
        return False
    if should_ignore_probe_for_visitors(d.mac, d.org):
        return False
    return device_is_probable_phone(d)

    

# ── Helpers ───────────────────────────────────────────────────────────────────
def now_ts() -> datetime:
    return datetime.now()

def short_device_id(mac: str) -> str:
    cleaned = mac.replace(":", "").lower()
    return f"dev_{cleaned[-4:]}"

def friendly_device_name(device: "DeviceRecord") -> str:
    """
    Easier to understand UI
    """
    org = (device.org or "").strip()
    org_lower = org.lower()

    # remove obvious unknown placeholders
    unknown_org = {"", "unknown", "private", "randomized"}
    is_unknown_org = org_lower in unknown_org

    if not is_unknown_org:
        if "anchor:" in org_lower or "listener" in org_lower or "raspberry pi" in org_lower:
            kind = "Pi"
        elif "apple" in org_lower or "samsung" in org_lower or "google" in org_lower:
            kind = "Phone"
        elif "intel" in org_lower or "dell" in org_lower or "lenovo" in org_lower:
            kind = "Laptop"
        else:
            kind = "Device"
        return f"{org} {kind}".strip()

    # fallback for unknown orgs
    if device.classification() == "likely_phone" or device.randomized:
        return "Phone"
    if device.node_count >= 2 and device.sightings >= 4:
        return "Nearby device"
    return "Unknown device"


def human_event_label(event_type: str) -> str:
    return {
        "NEW": "Detected",
        "INSIDE": "Inside",
        "FRINGE": "Weak signal",
        "EXIT": "Exited",
    }.get(event_type, event_type.title())


def log_line(msg: str):
    ts   = now_ts().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def push_event(event_type: str, device: DeviceRecord, extra: Dict[str, Any] = None):
    payload = {
        "ts":             now_ts().strftime("%Y-%m-%d %H:%M:%S"),
        "event":          event_type,
        "device_id":      device.device_id,
        "device_label":   friendly_device_name(device),
        "masked_mac":     device.masked_mac(),
        "org":            device.org,
        "randomized":     device.randomized,
        "classification": device.classification(),
        "confidence":     device.confidence(),
        "state":          device.state,
        "zone":           device.zone(),
        "best_rssi":      device.best_rssi,
        "avg_rssi":       device.avg_rssi_room,
        "avg_rssi_all_samples": device.avg_rssi,
        "nodes_seen":     device.nodes_seen,
        "dwell_seconds":  device.dwell_seconds,
    }
    if extra:
        payload.update(extra)

    recent_events.appendleft(payload)

    nodes = ",".join(device.nodes_seen) if device.nodes_seen else "none"
    log_line(
        f"{event_type:<6} | {device.device_id} | {device.masked_mac()} | "
        f"{device.classification()} | state={device.state} | zone={device.zone()} | "
        f"nodes={nodes} | best={device.best_rssi}dBm | avg_room={device.avg_rssi_room}dBm | "
        f"dwell={device.dwell_seconds}s"
    )


def node_health_snapshot() -> List[Dict[str, Any]]:
    now  = now_ts()
    rows = []
    for node in NODES:
        last = node_last_seen.get(node)
        if last is None:
            status = "offline"
            age    = None
        else:
            age_delta = now - last
            age    = round(age_delta.total_seconds(), 1)
            status = "online" if age_delta <= NODE_STALE_AFTER else "stale"

        rows.append({
            "node":                      node,
            "status":                    status,
            "last_seen":                 last.strftime("%Y-%m-%d %H:%M:%S") if last else None,
            "seconds_since_last_report": age,
            "reports_received":          node_report_count.get(node, 0),
        })
    return rows

# RSSI averaged area
def passes_area_gate(device: DeviceRecord) -> bool:
    """
    True if the device is convincingly inside the mesh area.
    falling back to best single-node RSSI for single-node observations.
    With 2+ nodes: require strong best RSSI and sightings, then either the filtered
    room average OR a strong average at the loudest listener (covers "phone parked
    next to one corner" without weak remote anchors blocking inside).
    """
    if device.sightings < INSIDE_MIN_SIGHTINGS:
        return False
    if device.node_count < INSIDE_MIN_NODES:
        return False
    if device.best_rssi < INSIDE_MIN_BEST_RSSI:
        return False
    if device.node_count >= 2:
        room_ok = device.avg_rssi_room >= INSIDE_AVG_RSSI
        loud_ok = device.strongest_node_avg_rssi >= INSIDE_AVG_RSSI
        return room_ok or loud_ok

    return device.best_rssi >= INSIDE_MIN_BEST_RSSI
    

def should_merge_randomized(
    incoming_node: str,
    incoming_rssi: int,
    now: datetime,
    candidate: DeviceRecord,
) -> bool:
    # only merge recent randomized MACs
    if not candidate.randomized:
        return False
    if (now - candidate.last_seen).total_seconds() > RANDOMIZED_MERGE_WINDOW_SEC:
        return False
    if incoming_node not in candidate.nodes_seen and candidate.strongest_node != incoming_node:
        return False
    return abs(candidate.best_rssi - incoming_rssi) <= RANDOMIZED_RSSI_TOLERANCE

def maybe_transition_state(device: DeviceRecord):
    """
    - Getting IN requires passing the area gate (RSSI + sightings).
    - Getting OUT requires VISITOR_TIMEOUT of silence — signal alone never evicts.
    - A streak counter (consecutive_inside) requires 2 consecutive passing checks
      before declare "inside", filtering out single-burst probe events.
    """
    global total_entries, total_people_inside_events
    old_state = device.state
 
    # ── once inside, only timeout can remove — not signal fluctuation ─────────
    if old_state == "inside":
        # still accumulate the streak for logging but do not change state
        if passes_area_gate(device):
            device.consecutive_inside += 1
        return
 
    # ── define where this device should be ─────────────────────────────────
    passed_area_gate = passes_area_gate(device)
    if passed_area_gate:
        device.consecutive_inside += 1
    else:
        device.consecutive_inside = 0  # reset streak on any fail
 
    # require 2 consecutive passing checks to confirm "inside"
    # yhis filters out single WiFi-toggle probe bursts
    device.area_gate_votes.append(1 if passed_area_gate else 0)
    if len(device.area_gate_votes) > INSIDE_CONFIRM_WINDOW:
        device.area_gate_votes = device.area_gate_votes[-INSIDE_CONFIRM_WINDOW:]

    # require enough passes in recent checks (2-of-3 by default).
    if sum(device.area_gate_votes) >= INSIDE_CONFIRM_MIN_PASSES:
        new_state = "inside"
    else:
        new_state = "fringe"
 
    if old_state == "new" and new_state == "fringe":
        return  # stay "new" until we know more
 
    if new_state != old_state:
        device.state       = new_state
        device.state_since = now_ts()
 
        if new_state == "inside":
            device.inside_since = now_ts()
            total_entries += 1
            if should_count_toward_people_inside(device):
                total_people_inside_events += 1
                inside_entries_history.append((now_ts(), device.device_id))
            push_event("INSIDE", device)
        elif new_state == "fringe":
            if (
                device.last_event_at is None
                or (now_ts() - device.last_event_at) >= EVENT_COOLDOWN
            ):
                push_event("FRINGE", device)
        device.last_event_at = now_ts()

def purge_stale_locked():
    global total_exits

    now     = now_ts()
    expired = []

    for mac, device in list(visitors.items()):
        if (now - device.last_seen) > VISITOR_TIMEOUT:
            expired.append(mac)

    for mac in expired:
        device = visitors[mac]
        push_event("EXIT", device, extra={"exit_reason": "timeout"})
        total_exits += 1
        del visitors[mac]

    return len(expired)


def record_occupancy_sample_locked():
    inside_all = sum(1 for d in visitors.values() if d.state == "inside")
    inside_phones = sum(
        1 for d in visitors.values() if should_count_toward_people_inside(d)
    )
    occupancy_history.append({
        "ts": now_ts(),
        "inside": inside_all,
        "inside_phones": inside_phones,
    })


def prune_inside_entries_locked(now: datetime):
    cutoff = now - timedelta(minutes=IMPRESSION_RETENTION_MINUTES)
    while inside_entries_history and inside_entries_history[0][0] < cutoff:
        inside_entries_history.popleft()


def occupancy_window_stats_locked(window_minutes: int) -> Dict[str, Any]:
    now = now_ts()
    cutoff = now - timedelta(minutes=window_minutes)
    samples = [s for s in occupancy_history if s["ts"] >= cutoff]
    if not samples:
        return {
            "window_minutes": window_minutes,
            "sample_count": 0,
            "inside_now": 0,
            "inside_avg": 0.0,
            "inside_peak": 0,
            "inside_min": 0,
            "all_inside_now": 0,
            "all_inside_avg": 0.0,
            "all_inside_peak": 0,
            "all_inside_min": 0,
            "impressions_window": 0,
            "unique_devices_window": 0,
        }

    inside_vals = [s["inside"] for s in samples]
    phone_vals = [s.get("inside_phones", s["inside"]) for s in samples]
    prune_inside_entries_locked(now)
    entries_window = [(ts, dev_id) for ts, dev_id in inside_entries_history if ts >= cutoff]
    unique_devices = len({dev_id for _, dev_id in entries_window})
    return {
        "window_minutes": window_minutes,
        "sample_count": len(samples),
        # only count people, exclude listener and Pis
        "inside_now": phone_vals[-1],
        "inside_avg": round(sum(phone_vals) / len(phone_vals), 2),
        "inside_peak": max(phone_vals),
        "inside_min": min(phone_vals),
        "all_inside_now": inside_vals[-1],
        "all_inside_avg": round(sum(inside_vals) / len(inside_vals), 2),
        "all_inside_peak": max(inside_vals),
        "all_inside_min": min(inside_vals),
        "impressions_window": len(entries_window),
        "unique_devices_window": unique_devices,
    }



def active_devices_sorted() -> List[DeviceRecord]:
    return sorted(
        visitors.values(),
        key=lambda d: (d.state != "inside", -d.best_rssi, -d.sightings),
    )

def summary_dict() -> Dict[str, Any]:
    active  = list(visitors.values())
    inside  = sum(1 for d in active if d.state == "inside")
    inside_phones = sum(1 for d in active if should_count_toward_people_inside(d))
    outside = sum(1 for d in active if d.state != "inside")

    win = occupancy_window_stats_locked(OCCUPANCY_WINDOW_MINUTES)
    return {
        "timestamp":       now_ts().strftime("%Y-%m-%d %H:%M:%S"),
        "active":          len(active),
        "inside":          inside,
        "inside_phones":   inside_phones,
        "outside_or_nearby": outside,
        "total_ever_seen": total_ever_seen,
        "total_entries":   total_entries,
        "total_exits":     total_exits,
        "total_people_inside_events": total_people_inside_events,
        "counter_notes": {
            "total_ever_seen": (
                "Number of new visitor records created since the process started "
                "(each new MAC key after merge checks; not unique people)."
            ),
            "total_exits": (
                "Visitor records removed after silence (VISITOR_TIMEOUT). "
                "Randomized Wi-Fi MACs rotate often, so seen and exits grow quickly."
            ),
            "total_entries": "Times any visitor transitioned to state inside (includes non-phones).",
            "total_people_inside_events": "Inside transitions counting only probable phones (excludes Pi/listener hardware).",
        },
        "window_stats":    win,
        "thresholds": {
            "inside_avg_rssi": INSIDE_AVG_RSSI,
            "inside_min_best_rssi": INSIDE_MIN_BEST_RSSI,
            "inside_min_sightings": INSIDE_MIN_SIGHTINGS,
            "inside_min_nodes": INSIDE_MIN_NODES,
            "inside_confirm_window": INSIDE_CONFIRM_WINDOW,
            "inside_confirm_min_passes": INSIDE_CONFIRM_MIN_PASSES,
            "occupancy_window_minutes": OCCUPANCY_WINDOW_MINUTES,
        },
        "nodes":           node_health_snapshot(),
        "anchors":         list(anchor_devices.values()),
    }


# ── Console renderer ──────────────────────────────────────────────────────────
def render_console():
    with lock:
        purge_stale_locked()
        summary = summary_dict()
        devices = active_devices_sorted()[:10]
        events  = list(recent_events)[:8]

    print("\n" + "=" * 90, flush=True)
    print(" WIFI PROBE MESH | LIVE STATUS", flush=True)
    print("=" * 90, flush=True)
    print(
        f" Time: {summary['timestamp']} | Active: {summary['active']} | "
        f"Inside phones: {summary['inside_phones']} | Inside all: {summary['inside']} | "
        f"Nearby/Outside: {summary['outside_or_nearby']}",
        flush=True,
    )
    print(
        f" Totals: seen={summary['total_ever_seen']} | "
        f"entries={summary['total_entries']} | people_inside_events={summary['total_people_inside_events']} | "
        f"exits={summary['total_exits']}",
        flush=True,
    )
    print("-" * 90, flush=True)
    print(" Node Health", flush=True)
    for row in summary["nodes"]:
        age     = row["seconds_since_last_report"]
        age_str = "never" if age is None else f"{age:.1f}s ago"
        print(
            f"  {row['node']:<12} status={row['status']:<7} "
            f"last={age_str:<12} reports={row['reports_received']}",
            flush=True,
        )

    if summary["anchors"]:
        print("-" * 90, flush=True)
        print(" Listener Pi Anchors", flush=True)
        for a in summary["anchors"]:
            print(
                f"  {a['label']:<14} mac={a['mac']}  "
                f"last_rssi={a.get('last_rssi','?')}dBm  "
                f"last_seen={a.get('last_seen','?')}",
                flush=True,
            )
    
    print("-" * 90, flush=True)
    print(" Recent Events", flush=True)
    for ev in events:
        print(
            f"  {ev['ts'][11:19]}  {human_event_label(ev['event']):<11}  {ev['device_id']:<8}  "
            f"{ev['masked_mac']:<20}  {ev['state']:<9}  {ev['zone']:<7}  "
            f"{ev['best_rssi']:>4}dBm  avg_room={ev['avg_rssi']:>5}dBm  "
            f"conf={ev['confidence']:.2f}",
            flush=True,
        )

    print("-" * 90, flush=True)
    print(" Active Devices", flush=True)
    if not devices:
        print("  (none)", flush=True)
    else:
        print(
            " ID        MAC                  TYPE               STATE      "
            "NODES  BEST   AVG_RM DWELL",
            flush=True,
        )
        for d in devices:
            print(
                f"  {d.device_id:<8} {d.masked_mac():<20}  {d.classification():<17}  "
                f"{d.state:<9}  {d.compact_nodes():<6} {d.best_rssi:>4}  "
                f"{d.avg_rssi_room:>6}  {d.dwell_seconds:>4}s",
                flush=True,
            )
    print("=" * 90, flush=True)


def cleanup_loop():
    while True:
        time.sleep(2)
        with lock:
            purged = purge_stale_locked()
        if purged:
            log_line(
                f"CLEANUP | removed {purged} stale device(s) | active={len(visitors)}"
            )


def occupancy_loop():
    while True:
        time.sleep(max(1, OCCUPANCY_SAMPLE_SEC))
        with lock:
            purge_stale_locked()
            prune_inside_entries_locked(now_ts())
            record_occupancy_sample_locked()
 


def console_loop():
    while True:
        time.sleep(CONSOLE_REFRESH_SEC)
        try:
            render_console()
        except Exception as e:
            log_line(f"console render error: {e}")

            
 # ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/report', methods=['POST'])
def report():
    global total_ever_seen

    data = request.json
    if not data:
        return jsonify({"status": "error", "msg": "no JSON body"}), 400

    node       = data.get("node", "Unknown")
    mac        = data.get("mac", "").lower().strip()
    rssi       = data.get("rssi", -100)
    org        = data.get("org", "UNKNOWN")
    randomized = bool(data.get("randomized", False))
    now        = now_ts()

    node_accepted = node in NODES or any(
        node.startswith(prefix) for prefix in ("Listener",)
    )
    if not node_accepted:
        return jsonify({"status": "ignored", "reason": f"unknown node '{node}'"}), 200
    if not mac or len(mac) != 17:
        return jsonify({"status": "ignored", "reason": "invalid MAC"}), 200
    if not isinstance(rssi, (int, float)) or rssi < -120 or rssi > 0:
        return jsonify({"status": "ignored", "reason": "invalid RSSI"}), 200

    # ── Edge Pi check ────────────────────────────────────────────────────────
    # If org starts with "ANCHOR:" this is a known Listener Pi probe — track it
    # as a range anchor
    if org.startswith("ANCHOR:"):
        anchor_label = org.split(":", 1)[1]
        with lock:
            node_last_seen[node]  = now
            anchor_devices[mac] = {
                "mac":        mac,
                "label":      anchor_label,
                "last_rssi":  int(rssi),
                "last_seen":  now.strftime("%H:%M:%S"),
                "node":       node,
            }
        return jsonify({"status": "anchor_recorded"}), 200

    # ── signal floor (coarse gate before processing) ──────────────────────────
    MIN_RSSI = -75
    if rssi < MIN_RSSI:
        return jsonify({"status": "ignored", "reason": "weak signal"}), 200

    with lock:
        node_last_seen[node]    = now
        node_report_count[node] += 1
        purge_stale_locked()
        if should_ignore_probe_for_visitors(mac, org):
            return jsonify({"status": "ignored", "reason": "listener_or_anchor_mac"}), 200

        is_new = mac not in visitors
        device = None

        # likely-same devices?
        if is_new:
            for existing in visitors.values():
                if (
                    abs(existing.best_rssi - int(rssi)) <= 3
                    and (now - existing.last_seen).total_seconds() <= 5
                    and existing.node_count >= 1
                ):
                    device = existing
                    is_new = False
                    break

        # randomized MACs rotate often so merge them
        if is_new and randomized:
            best_match = None
            best_delta = 999
            for existing in visitors.values():
                if not should_merge_randomized(node, int(rssi), now, existing):
                    continue
                delta = abs(existing.best_rssi - int(rssi))
                if delta < best_delta:
                    best_delta = delta
                    best_match = existing
            if best_match is not None:
                device = best_match
                is_new = False
                    
        if is_new:
            total_ever_seen += 1
            device = DeviceRecord(
                mac=mac,
                device_id=short_device_id(mac),
                org=org,
                randomized=randomized,
                first_seen=now,
                last_seen=now,
                state="new",
            )
            visitors[mac] = device
        elif device is None:
            device = visitors[mac]

        device.add_sighting(node=node, rssi=int(rssi), now=now)

        if is_new:
            push_event("NEW", device, extra={"first_node": node, "first_rssi": int(rssi)})

        maybe_transition_state(device)
        record_occupancy_sample_locked()
        prune_inside_entries_locked(now)

        return jsonify({
            "status":          "success",
            "is_new":          is_new,
            "device_id":       device.device_id,
            "state":           device.state,
            "confidence":      device.confidence(),
            "avg_rssi":        device.avg_rssi_room,
            "active":          len(visitors),
            "total_ever_seen": total_ever_seen,
        }), 200



@app.route("/status", methods=["GET"])
def status():
    with lock:
        purge_stale_locked()
        return jsonify({
            "summary":        summary_dict(),
            "active_devices": [d.as_dict() for d in active_devices_sorted()],
            "recent_events":  list(recent_events),
        })

@app.route("/devices", methods=["GET"])
def devices_route():
    with lock:
        purge_stale_locked()
        return jsonify({
            "timestamp": now_ts().strftime("%Y-%m-%d %H:%M:%S"),
            "count":     len(visitors),
            "devices":   [d.as_dict() for d in active_devices_sorted()],
        })

@app.route("/events", methods=["GET"])
def events_route():
    with lock:
        return jsonify({
            "timestamp": now_ts().strftime("%Y-%m-%d %H:%M:%S"),
            "count":     len(recent_events),
            "events":    list(recent_events),
        })

@app.route("/summary", methods=["GET"])
def summary_route():
    window_min = request.args.get("window_min", default=None, type=int)
    with lock:
        purge_stale_locked()
        out = summary_dict()
        if window_min is not None and window_min > 0:
            out["window_stats"] = occupancy_window_stats_locked(window_min)
        return jsonify(out)

@app.route("/occupancy", methods=["GET"])
def occupancy_route():
    window_min = request.args.get("window_min", default=OCCUPANCY_WINDOW_MINUTES, type=int)
    if not window_min or window_min <= 0:
        window_min = OCCUPANCY_WINDOW_MINUTES
    with lock:
        purge_stale_locked()
        now = now_ts()
        cutoff = now - timedelta(minutes=window_min)
        points = [
            {
                "ts": s["ts"].strftime("%Y-%m-%d %H:%M:%S"),
                "inside": s["inside"],
                "inside_phones": s.get("inside_phones", s["inside"]),
            }
            for s in occupancy_history
            if s["ts"] >= cutoff
        ]
        stats = occupancy_window_stats_locked(window_min)
        return jsonify({
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "window_minutes": window_min,
            "count": len(points),
            "stats": stats,
            "points": points,
        })

@app.route("/dashboard", methods=["GET"])
def dashboard():
    with lock:
        purge_stale_locked()
        summary = summary_dict()
        devs    = active_devices_sorted()
        events  = list(recent_events)[:20]
    phones     = [d for d in devs if device_is_probable_phone(d)]
    non_phones = [d for d in devs if not device_is_probable_phone(d)]

    # ── node health rows ──────────────────────────────────────────────────────
    node_rows = ""
    for row in summary["nodes"]:
        age     = row["seconds_since_last_report"]
        age_str = "never" if age is None else f"{age:.1f}s ago"
        color   = {"online": "#22c55e", "stale": "#f59e0b", "offline": "#ef4444"}.get(
            row["status"], "#94a3b8"
        )
        node_rows += (
            f"<tr>"
            f"<td>{row['node']}</td>"
            f"<td style='color:{color}'>{row['status']}</td>"
            f"<td>{age_str}</td>"
            f"<td>{row['reports_received']}</td>"
            f"</tr>"
        )

    # Anchor rows
    anchor_rows = ""
    for a in summary.get("anchors", []):
        anchor_rows += (
            f"<tr>"
            f"<td>{a['label']}</td>"
            f"<td>{a['mac']}</td>"
            f"<td>{a.get('last_rssi','?')}&nbsp;dBm</td>"
            f"<td>{a.get('last_seen','?')}</td>"
            f"<td>{a.get('node','?')}</td>"
            f"</tr>"

        )

    # ── device row builder ────────────────────────────────────────────────────
    def device_row(d):
        state_color = {
            "inside":   "#22c55e",
            "fringe":   "#f59e0b",
            "new":      "#94a3b8",
        }.get(d.state, "#94a3b8")
        state_human = {
            "inside": "Inside",
            "fringe": "Outside", "new": "Detected",
        }.get(d.state, d.state.title())
        return (
            f"<tr>"
            f"<td>{friendly_device_name(d)}</td>"
            f"<td>{d.masked_mac()}</td>"
            f"<td>"
            f"<span class='badge' style='background:{state_color}22; color:{state_color}'>"
            f"{state_human}"
            f"</span>"
            f"</td>"
            f"<td>{d.compact_nodes()}</td>"
            f"<td>{d.best_rssi}&nbsp;dBm</td>"
            f"<td>{d.avg_rssi_room}&nbsp;dBm</td>"
            f"<td>{d.confidence():.0%}</td>"
            f"<td>{d.dwell_seconds}s</td>"
            f"</tr>"
        )

    phone_rows    = "".join(device_row(d) for d in phones)
    other_rows    = "".join(device_row(d) for d in non_phones)

    if not phone_rows:
        phone_rows = "<tr><td colspan='8' style='color:#475569;font-style:italic'>No phones detected</td></tr>"
    if not other_rows:
        other_rows = "<tr><td colspan='8' style='color:#475569;font-style:italic'>None</td></tr>"

    # ── event rows ────────────────────────────────────────────────────────────
    event_rows = ""
    for ev in events:
        ev_name = ev.get("event", "")
        ev_label = human_event_label(ev.get("event", ""))
        event_rows += (
            f"<tr>"
            f"<td>{ev['ts'][11:]}</td>"
            f"<td>{ev_label}</td>"
            f"<td>{ev.get('device_label', 'Unknown')}</td>"
            f"<td>{ev['masked_mac']}</td>"
            f"<td>{ev['state'].title()}</td>"
            f"<td>{ev['best_rssi']}&nbsp;dBm</td>"
            f"<td>{ev['avg_rssi']}&nbsp;dBm</td>"
            f"</tr>"
        )

    anchor_section = ""
    if anchor_rows:
        anchor_section = f"""
        <h2>&#128204; Listener Pi Anchors</h2>
        <table>
          <tr><th>Label</th><th>MAC</th><th>Last RSSI</th><th>Last Seen</th><th>Reported By</th></tr>
          {anchor_rows}
        </table>
        """

    html = f"""<!DOCTYPE html>

<html>
<head>
    <title>Sensor Dashboard</title>
    <meta http-equiv="refresh" content="3">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, Arial, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            padding: 20px;
        }}
        h1 {{ font-size: 22px; color: #f1f5f9; margin-bottom: 4px; }}
        h2 {{
            font-size: 14px;
            color: #94a3b8;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin: 24px 0 10px;
            padding-bottom: 6px;
            border-bottom: 1px solid #1e293b;
        }}
        .subtitle {{ color: #475569; font-size: 13px; margin-bottom: 20px; }}
        .tiny-note {{ color: #64748b; font-size: 11px; margin-top: 6px; line-height: 1.35; max-width: 920px; }}
        .cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 10px;
            margin-bottom: 24px;
        }}
        .card {{
            background: #1e293b;
            padding: 16px 12px;
            border-radius: 12px;
            text-align: center;
        }}
        .card .label {{ font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: .5px; }}
        .card .value {{ font-size: 36px; font-weight: 700; margin-top: 4px; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 8px;
            font-size: 13px;
        }}
        th, td {{
            border: 1px solid #1e293b;
            padding: 7px 10px;
            text-align: left;
        }}
        th {{ background: #1e293b; color: #64748b; font-weight: 600; font-size: 11px; text-transform: uppercase; }}
        tr:hover td {{ background: #1e293b44; }}
        .section-phone {{ background: #0f2720; border-radius: 10px; padding: 12px 16px; margin-bottom: 16px; }}
        .section-other {{ background: #141e2e; border-radius: 10px; padding: 12px 16px; margin-bottom: 16px; }}
        .badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 99px;
            font-size: 11px;
            font-weight: 600;
        }}
        .hero {{
            display: flex;
            justify-content: center;
            margin-bottom: 20px;
        }}
        .hero-card {{
            background: #1e293b;
            padding: 24px 40px;
            border-radius: 16px;
            text-align: center;
        }}
        .hero-card .label {{
            font-size: 13px;
            color: #64748b;
            text-transform: uppercase;
        }}
        .hero-card .value {{
            font-size: 64px;
            font-weight: 800;
            color: #22c55e;
        }}
        .badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 99px;
            font-size: 11px;
            font-weight: 600;
        }}
    </style>
</head>
<body>
    <h1>&#128246; Probe Mesh Dashboard</h1>
    <div class="subtitle">Updated: {summary["timestamp"]} &nbsp;·&nbsp; Nodes: {len([n for n in summary["nodes"] if n["status"]=="online"])} online</div>

    <div class="hero">
        <div class="hero-card">
            <div class="label">People Inside</div>
            <div class="value" style="color:#22c55e">{summary["inside_phones"]}</div>
        </div>
        
        <div class="card">
            <div class="label">Total Seen</div>
            <div class="value" style="color:#60a5fa">{summary["total_ever_seen"]}</div>
        </div>
        <div class="card">
            <div class="label">Total Exits</div>
            <div class="value" style="color:#f59e0b">{summary["total_exits"]}</div>
        </div>
        <div class="card">
            <div class="label">Peak ({summary["window_stats"]["window_minutes"]}m)</div>
            <div class="value" style="color:#22c55e">{summary["window_stats"]["inside_peak"]}</div>
        </div>
        <div class="card">
            <div class="label">Avg Inside ({summary["window_stats"]["window_minutes"]}m)</div>
            <div class="value" style="color:#60a5fa">{summary["window_stats"]["inside_avg"]}</div>
        </div>
        <div class="card">
            <div class="label">Impressions ({summary["window_stats"]["window_minutes"]}m)</div>
            <div class="value" style="color:#a78bfa">{summary["window_stats"]["impressions_window"]}</div>
        </div>
    </div>
    <div class="tiny-note">
        Total Seen counts new MAC records since start (after merge checks), not unique people.
        Total Exits counts records removed after silence; rotating Wi-Fi MACs inflate both.
        Peak / Avg / Impressions use probable phones only (Listener Pi / Pi OUI probes excluded).
    </div>


    <h2>&#128293; Node Health</h2>
    <table>
        <tr><th>Node</th><th>Status</th><th>Last Report</th><th>Reports</th></tr>
        {node_rows}
    </table>

    {anchor_section}

    <h2>&#128247; People Detected (Phones)</h2>
    <div class="section-phone">
        <table>
            <tr>
                <th>Device</th><th>MAC</th><th>Status</th><th>Nodes</th>
                <th>Best RSSI</th><th>Avg RSSI</th><th>Confidence</th><th>Dwell</th>
            </tr>
            {phone_rows}
        </table>
    </div>

    <h2>&#128268; Other / Known Units</h2>
    <div class="section-other">
        <table>
            <tr>
                <th>Device</th><th>MAC</th><th>Status</th><th>Nodes</th>
                <th>Best RSSI</th><th>Avg RSSI</th><th>Confidence</th><th>Dwell</th>
            </tr>
            {other_rows}
        </table>
    </div>

    <h2>&#128336; Recent Events</h2>
    <table>
        <tr><th>Time</th><th>Event</th><th>Device</th><th>MAC</th><th>State</th><th>Best RSSI</th><th>Avg RSSI</th></tr>
        {event_rows}
    </table>
</body>
</html>"""

    return html


if __name__ == "__main__":
    if not os.path.exists(LOG_FILE):
        open(LOG_FILE, "a").close()

    threading.Thread(target=cleanup_loop, daemon=True).start()
    threading.Thread(target=occupancy_loop, daemon=True).start()
    threading.Thread(target=console_loop, daemon=True).start()

    log_line("=== Probe Mesh Aggregator Started ===")
    log_line(f"VISITOR_TIMEOUT={VISITOR_TIMEOUT}  NODE_STALE_AFTER={NODE_STALE_AFTER}")
    log_line(f"INSIDE gate: avg_room≥{INSIDE_AVG_RSSI}dBm  best≥{INSIDE_MIN_BEST_RSSI}dBm  "
             f"sightings≥{INSIDE_MIN_SIGHTINGS}  nodes≥{INSIDE_MIN_NODES}")
    log_line(f"INSIDE confirm: passes≥{INSIDE_CONFIRM_MIN_PASSES} within last "
             f"{INSIDE_CONFIRM_WINDOW} checks")
    log_line(
        f"OCCUPANCY window={OCCUPANCY_WINDOW_MINUTES}m sample={OCCUPANCY_SAMPLE_SEC}s "
        f"history_max={OCCUPANCY_HISTORY_MAX_POINTS}"
    )
    log_line(f"RANDOMIZED merge: window={RANDOMIZED_MERGE_WINDOW_SEC}s  "
             f"tolerance=±{RANDOMIZED_RSSI_TOLERANCE}dBm")
    log_line("Visitor filter: ignore probes from anchor MACs + Raspberry Pi OUIs (Listener hardware)")
    log_line(f"Nodes: {NODES}")

    app.run(host=HOST, port=PORT, threaded=True)
    
