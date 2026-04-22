"""
engine.py — Core detection engine.
 
owns:
  - DeviceRecord
  - Visitor state (merge, gate, transition)
  - Occupancy history
  - Node health
  - Event log
 
 
Public API:
  engine = Engine(cfg)
  engine.process_event(event_dict)   → dict (result)
  engine.process_anchor(event_dict)  → None
  engine.get_summary()               → dict
  engine.get_active_devices()        → list[dict]
  engine.get_recent_events()         → list[dict]
  engine.get_occupancy(window_min)   → dict
"""

import statistics
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# ── deviceRecord ───────────────────────────────
@dataclass
class DeviceRecord:
    mac:              str
    device_id:        str
    org:              str
    randomized:       bool
    first_seen:       datetime
    last_seen:        datetime
    state:            str = "new"
    state_since:      Optional[datetime] = None
    last_event_at:    Optional[datetime] = None
    sightings:        int = 0
    last_report_node: str = ""
    inside_since:     Optional[datetime] = None
    area_gate_votes:  List[int] = field(default_factory=list)
    rssi_by_node:     Dict[str, List[int]] = field(
        default_factory=lambda: defaultdict(list)
    )
    # thresholds are injected so deviceRecord can compute zone/confidence
    # without importing global state
    _zone_close:  int = -40
    _zone_nearby: int = -60

    def __post_init__(self):
        if self.state_since is None:
            self.state_since = self.first_seen

    def add_sighting(self, node: str, rssi: int, now: datetime):
        self.last_seen        = now
        self.last_report_node = node
        self.sightings       += 1
        self.rssi_by_node[node].append(rssi)
        self.rssi_by_node[node] = self.rssi_by_node[node][-10:]

    #───────────────────────RSSI helpers───────────────────────
    @property
    def best_rssi(self) -> int:
        vals = [r for readings in self.rssi_by_node.values() for r in readings]
        return max(vals) if vals else -100
 
    @property
    def avg_rssi(self) -> float:
        vals = [r for readings in self.rssi_by_node.values() for r in readings]
        return round(sum(vals) / len(vals), 1) if vals else -100.0
 
    @property
    def avg_rssi_per_node(self) -> Dict[str, float]:
        return {
            node: round(sum(readings) / len(readings), 1)
            for node, readings in self.rssi_by_node.items()
            if readings
        }

    @property
    def avg_rssi_room(self) -> float:
        """Median of per-node medians — equal weight per node."""
        node_medians = [
            statistics.median(readings)
            for readings in self.rssi_by_node.values()
            if readings
        ]
        if not node_medians:
            return -100.0
        if len(node_medians) == 1:
            return round(node_medians[0], 1)
        return round(statistics.median(node_medians), 1)
 
    @property
    def strongest_node_avg_rssi(self) -> float:
        sn = self.strongest_node
        return self.avg_rssi_per_node.get(sn, -100.0) if sn != "Unknown" else -100.0

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
        best_node, best_val = None, -999
        for node, readings in self.rssi_by_node.items():
            if readings:
                nb = max(readings)
                if nb > best_val:
                    best_val, best_node = nb, node
        return best_node or "Unknown"
 
    def best_rssi_by_node(self) -> Dict[str, int]:
        return {n: max(r) for n, r in self.rssi_by_node.items() if r}

    # ── classification ────────────────────────────────────────
    def zone(self) -> str:
        if self.best_rssi >= self._zone_close:
            return "close"
        if self.best_rssi >= self._zone_nearby:
            return "nearby"
        return "fringe"


    def confidence(self) -> float:
        score  = min(self.node_count / 3.0, 1.0) * 0.40
        score += min(self.sightings / 6.0, 1.0) * 0.30
        if   self.best_rssi >= -45: score += 0.30
        elif self.best_rssi >= -55: score += 0.24
        elif self.best_rssi >= -65: score += 0.16
        elif self.best_rssi >= -75: score += 0.08
        if self.randomized:         score += 0.08
        return round(min(score, 0.99), 2)

    def classification(self) -> str:
        c = self.confidence()
        if c >= 0.75: return "likely_phone"
        if c >= 0.45: return "visitor_candidate"
        return "uncertain"

    def masked_mac(self) -> str:
        parts = self.mac.split(":")
        if len(parts) != 6:
            return self.mac
        return f"{parts[0]}:{parts[1]}:XX:XX:{parts[4]}:{parts[5]}"

    def compact_nodes(self) -> str:
        return ", ".join(self.nodes_seen)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "device_id":            self.device_id,
            "mac":                  self.mac,
            "masked_mac":           self.masked_mac(),
            "org":                  self.org,
            "randomized":           self.randomized,
            "classification":       self.classification(),
            "confidence":           self.confidence(),
            "state":                self.state,
            "zone":                 self.zone(),
            "first_seen":           self.first_seen.strftime("%Y-%m-%d %H:%M:%S"),
            "last_seen":            self.last_seen.strftime("%Y-%m-%d %H:%M:%S"),
            "dwell_seconds":        self.dwell_seconds,
            "sightings":            self.sightings,
            "nodes_seen":           self.nodes_seen,
            "node_count":           self.node_count,
            "strongest_node":       self.strongest_node,
            "best_rssi":            self.best_rssi,
            "avg_rssi":             self.avg_rssi_room,
            "avg_rssi_all_samples": self.avg_rssi,
            "avg_rssi_by_node":     dict(self.avg_rssi_per_node),
            "rssi_by_node":         self.best_rssi_by_node(),
        }

        
# ── engine ────────────────────────────────────────────────────
class Engine:
    def __init__(self, cfg: dict):
        self._cfg = cfg
        self._lock = threading.Lock()

        # pull thresholds once - engine uses these internally
        t = cfg["thresholds"]
        m = cfg["merge"]
        o = cfg["occupancy"]

        self.rssi_floor              = t["rssi_floor"]
        self.inside_avg_rssi         = t["inside_avg_rssi"]
        self.inside_min_best_rssi    = t["inside_min_best_rssi"]
        self.inside_min_sightings    = t["inside_min_sightings"]
        self.inside_min_nodes        = t["inside_min_nodes"]
        self.inside_confirm_window   = t["inside_confirm_window"]
        self.inside_confirm_min_passes = t["inside_confirm_min_passes"]
        self.visitor_timeout         = timedelta(seconds=t["visitor_timeout_sec"])
        self.zone_close              = t["zone_close"]
        self.zone_nearby             = t["zone_nearby"]

        self.merge_window_sec        = m["window_sec"]
        self.merge_rssi_tolerance    = m["rssi_tolerance"]

        self.occupancy_window_min    = o["window_minutes"]
        self.occupancy_sample_sec    = o["sample_sec"]
        self.impression_retention    = timedelta(minutes=o["impression_retention_minutes"])
 
        self.nodes                   = cfg["network"]["nodes"]
        self.node_stale_after        = timedelta(seconds=15)
        self.event_memory            = 100

        # helper sets from config
        from config import get_edge_device_macs, get_blacklisted_macs, get_raspberry_pi_ouis
        self.edge_macs:   dict  = get_edge_device_macs(cfg)   # mac -> label
        self.blacklisted: set   = get_blacklisted_macs(cfg)
        self.pi_ouis:     frozenset = get_raspberry_pi_ouis()

        # state
        self.visitors:    Dict[str, DeviceRecord] = {}
        self.anchor_devices: Dict[str, dict]      = {}
        self.node_last_seen:   Dict[str, datetime] = {}
        self.node_report_count: Dict[str, int]     = defaultdict(int)
        self.recent_events     = deque(maxlen=self.event_memory)
        self.occupancy_history = deque(maxlen=o["history_max_points"])
        self.inside_entries    = deque()   # (ts, device_id)

        # daily totals
        self.total_ever_seen             = 0
        self.total_people_inside_events  = 0

    # ── public API ───────────────────────────────────────
    def process_event(self, event: dict) -> dict:
        """
        main entry point. Accepts event dict:
          { node, mac, rssi, org, randomized, timestamp? }
        Returns a result dict.
        """
        node       = event.get("node", "Unknown")
        mac        = event.get("mac", "").lower().strip()
        rssi       = event.get("rssi", -100)
        org        = event.get("org", "UNKNOWN")
        randomized = bool(event.get("randomized", False))
        now        = datetime.now()

        # validate
        if not mac or len(mac) != 17:
            return {"status": "ignored", "reason": "invalid MAC"}
        if not isinstance(rssi, (int, float)) or rssi < -120 or rssi > 0:
            return {"status": "ignored", "reason": "invalid RSSI"}
        if rssi < self.rssi_floor:
            return {"status": "ignored", "reason": "weak signal"}

        # anchor probe
        if org.startswith("ANCHOR:"):
            return self.process_anchor(event)

        with self._lock:
            self.node_last_seen[node]     = now
            self.node_report_count[node] += 1
            self._purge_stale(now)

            if self._should_ignore(mac, org):
                return {"status": "ignored", "reason": "listener_or_anchor_mac"}

            is_new = mac not in self.visitors
            device = None

            # randomized MAC merge logic
            if is_new and randomized:
                device = self._find_merge_candidate(node, int(rssi), now)
                if device is not None:
                    is_new = False

            if is_new:
                self.total_ever_seen += 1
                device = DeviceRecord(
                    mac=mac, device_id=self._short_id(mac),
                    org=org, randomized=randomized,
                    first_seen=now, last_seen=now, state="new",
                    _zone_close=self.zone_close,
                    _zone_nearby=self.zone_nearby,
                )
                self.visitors[mac] = device
            elif device is None:
                device = self.visitors[mac]

            device.add_sighting(node=node, rssi=int(rssi), now=now)

            if is_new:
                self._push_event("NEW", device, first_node=node, first_rssi=int(rssi))

            self._transition_state(device, now)
            self._record_occupancy(now)
            self._prune_entries(now)


            return {
                "status":          "success",
                "is_new":          is_new,
                "device_id":       device.device_id,
                "state":           device.state,
                "confidence":      device.confidence(),
                "avg_rssi":        device.avg_rssi_room,
                "active":          len(self.visitors),
                "total_ever_seen": self.total_ever_seen,
            }


    def process_anchor(self, event: dict) -> dict:
        """Record a known Listener Pi probe as edge  beacon."""
        mac   = event.get("mac", "").lower().strip()
        rssi  = event.get("rssi", -100)
        org   = event.get("org", "")
        node  = event.get("node", "Unknown")
        label = org.split(":", 1)[1] if ":" in org else org
        now   = datetime.now()
        with self._lock:
            self.node_last_seen[node] = now
            self.anchor_devices[mac]  = {
                "mac":       mac,
                "label":     label,
                "last_rssi": int(rssi),
                "last_seen": now.strftime("%H:%M:%S"),
                "node":      node,
            }
        return {"status": "anchor_recorded"}


    def get_summary(self) -> dict:
        with self._lock:
            self._purge_stale(datetime.now())
            return self._summary()

    def get_active_devices(self) -> List[dict]:
        with self._lock:
            return [d.as_dict() for d in self._sorted_devices()]

    def get_recent_events(self) -> list:
        with self._lock:
            return list(self.recent_events)

    def get_occupancy(self, window_min: int = None) -> dict:
        with self._lock:
            return self._occupancy_stats(window_min or self.occupancy_window_min)

    def get_node_health(self) -> List[dict]:
        with self._lock:
            return self._node_health()

    def record_occupancy_sample(self):
        """Call from external loop (occupancy_loop in main.py)."""
        with self._lock:
            now = datetime.now()
            self._purge_stale(now)
            self._prune_entries(now)
            self._record_occupancy(now)


    def reload_config(self, cfg: dict):
        """Hot-reload thresholds and network settings from a new config dict."""
        with self._lock:
            t = cfg["thresholds"]
            m = cfg["merge"]
            self.rssi_floor              = t["rssi_floor"]
            self.inside_avg_rssi         = t["inside_avg_rssi"]
            self.inside_min_best_rssi    = t["inside_min_best_rssi"]
            self.inside_min_sightings    = t["inside_min_sightings"]
            self.inside_min_nodes        = t["inside_min_nodes"]
            self.inside_confirm_window   = t["inside_confirm_window"]
            self.inside_confirm_min_passes = t["inside_confirm_min_passes"]
            self.visitor_timeout         = timedelta(seconds=t["visitor_timeout_sec"])
            self.merge_window_sec        = m["window_sec"]
            self.merge_rssi_tolerance    = m["rssi_tolerance"]
            from config import get_edge_device_macs, get_blacklisted_macs
            self.edge_macs   = get_edge_device_macs(cfg)
            self.blacklisted = get_blacklisted_macs(cfg)
            self._cfg = cfg


    # ── internal helpers ──────────────────────────
    def _short_id(self, mac: str) -> str:
        return f"dev_{mac.replace(':', '').lower()[-4:]}"

    def _should_ignore(self, mac: str, org: str) -> bool:
        """
        edge devices will never be dropped by Pi OUI filter.
        The Pi OUI filter exists because Raspberry Pis (b8:27:eb etc.) transmits signal
        traffic and would otherwise inflate the people count.
        Blacklisted MACs are known static infrastructure (APs, printers, etc.) discovered during 
        calibration and will be excluded.
        1. edge_macs -> NOT ignored - they are edge devices
        2. blacklisted -> drop completely
        3. Pi OUI that is not a configured edge devices -> drop (these Pis are not edge devices)
        """
        if mac in self.edge_macs:
            return True #exclude from visitor/people count, but already tracked as anchor
        if mac in self.anchor_devices:
            return True
        if mac in self.blacklisted:
            return True
        prefix = ":".join(mac.split(":")[:3]).lower()
        if prefix in self.pi_ouis:
            return True
        if "raspberry" in (org or "").lower():
            return True
        return False
 
    def _is_probable_phone(self, d: DeviceRecord) -> bool:
        # no sightings minimum - rotating MACs only ever get 1 sighting
        if d.randomized:
            return True
        ol = (d.org or "").lower()
        brands = ["apple", "samsung", "google", "oneplus", "xiaomi",
                  "huawei", "motorola", "sony", "lg electron", "oppo",
                  "realme", "vivo", "nothing tech"]
        if any(b in ol for b in brands):
            return True
        return d.classification() == "likely_phone"
 
    def _count_toward_people(self, d: DeviceRecord) -> bool:
        return (
            d.state == "inside"
            and not self._should_ignore(d.mac, d.org)
            and self._is_probable_phone(d)
        )

    def _passes_area_gate(self, d: DeviceRecord) -> bool:
        """ 
        three sequential gates - all must pass:
        + gate 1 (sightings): ensures device has been seen enough times.
        with inside_min_sightings=1 this is disabled for rotating MACs, 
        which only get one sighting each.
        + gate 2 (best_rssi): at least one node heard the phone strongly enough.
        + gate 3 (avg_rssi): the room-wide median signal is strong enough.
        We use medians so no single nearby node dominates
        because if a phone next to one Pi but far from all others still needs
        a reasonable room-average signal to count as "inside"
        """
        if d.sightings < self.inside_min_sightings:
            return False
        if d.node_count < self.inside_min_nodes:
            return False
        if d.best_rssi < self.inside_min_best_rssi:
            return False
        if d.node_count >= 2:
            return (
                d.avg_rssi_room >= self.inside_avg_rssi
                or d.strongest_node_avg_rssi >= self.inside_avg_rssi
            )
        return d.best_rssi >= self.inside_min_best_rssi


    def _should_merge(self, node: str, rssi: int, now: datetime, candidate: DeviceRecord) -> bool:
        """
        MAC rotation merging - the hardest problem in this system
        - Modern phones rotate their random MAC. Without merging, 
        one person in a room for 30 minutes could generate 5-10 MAC records.
        - WIth too-aggressive merging, a crowd of 20 people will turn into 3 records.

        Current thought is:
        - Only merge randomized MACs (static MACs are never merged - each
        is its own device). Require: short time window, spatial consistency (same
        node saw both), RSSI closeness, and that the candidate is already detected
        (sightings >=3) so we don't merge two brand-new into one.

        This move is made to  overcounting (each rotation = new record) rather 
        than undercounting (agressive merging collapses crowds). Overcounting is
        safer than undercounting.
        
        """
        if not candidate.randomized:
            return False
        if (now - candidate.last_seen).total_seconds() > self.merge_window_sec:
            return False
        if candidate.sightings < 3:
            return False
        if candidate.best_rssi < -75:
            return False
        if node not in candidate.nodes_seen and candidate.strongest_node != node:
            return False
        rssi_ref = candidate.avg_rssi_per_node.get(node, candidate.strongest_node_avg_rssi)
        return abs(rssi_ref - rssi) <= self.merge_rssi_tolerance


    
    def _find_merge_candidate(self, node: str, rssi: int, now: datetime) -> Optional[DeviceRecord]:
        best, best_delta = None, 999
        for existing in self.visitors.values():
            if not self._should_merge(node, rssi, now, existing):
                continue
            rssi_ref = existing.avg_rssi_per_node.get(node, existing.strongest_node_avg_rssi)
            delta = abs(rssi_ref - rssi)
            if delta < best_delta:
                best_delta, best = delta, existing
        return best


    def _transition_state(self, device: DeviceRecord, now: datetime):
        old_state = device.state
        if old_state == "inside":
            return   # only timeout evicts from inside
 
        passed = self._passes_area_gate(device)
        device.area_gate_votes.append(1 if passed else 0)
        if len(device.area_gate_votes) > self.inside_confirm_window:
            device.area_gate_votes = device.area_gate_votes[-self.inside_confirm_window:]

        new_state = (
            "inside"
            if sum(device.area_gate_votes) >= self.inside_confirm_min_passes
            else "fringe"
        )

        if old_state == "new" and new_state == "fringe":
            return

        if new_state != old_state:
            device.state       = new_state
            device.state_since = now
            if new_state == "inside":
                device.inside_since = now
                if self._count_toward_people(device):
                    self.total_people_inside_events += 1
                    self.inside_entries.append((now, device.device_id))
                self._push_event("INSIDE", device)
            device.last_event_at = now


    def _purge_stale(self, now: datetime) -> int:
        expired = [
            mac for mac, d in self.visitors.items()
            if (now - d.last_seen) > self.visitor_timeout
        ]
        for mac in expired:
            del self.visitors[mac]
        return len(expired)
 
    def _prune_entries(self, now: datetime):
        cutoff = now - self.impression_retention
        while self.inside_entries and self.inside_entries[0][0] < cutoff:
            self.inside_entries.popleft()
 
    def _record_occupancy(self, now: datetime):
        inside_all    = sum(1 for d in self.visitors.values() if d.state == "inside")
        inside_phones = sum(1 for d in self.visitors.values() if self._count_toward_people(d))
        self.occupancy_history.append({
            "ts": now, "inside": inside_all, "inside_phones": inside_phones,
        })


    def _push_event(self, event_type: str, device: DeviceRecord, **extra):
        from utils import friendly_device_name
        payload = {
            "ts":             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
            "nodes_seen":     device.nodes_seen,
            "dwell_seconds":  device.dwell_seconds,
        }
        payload.update(extra)
        self.recent_events.appendleft(payload)


    def _sorted_devices(self) -> List[DeviceRecord]:
        return sorted(
            self.visitors.values(),
            key=lambda d: (d.state != "inside", -d.best_rssi, -d.sightings),
        )
 
    def _node_health(self) -> List[dict]:
        now = datetime.now()
        rows = []
        for node in self.nodes:
            last = self.node_last_seen.get(node)
            if last is None:
                status, age = "offline", None
            else:
                delta = now - last
                age   = round(delta.total_seconds(), 1)
                status = "online" if delta <= self.node_stale_after else "delayed"
            rows.append({
                "node":                      node,
                "status":                    status,
                "last_seen":                 last.strftime("%Y-%m-%d %H:%M:%S") if last else None,
                "seconds_since_last_report": age,
                "reports_received":          self.node_report_count.get(node, 0),
            })
        return rows

    def _occupancy_stats(self, window_min: int) -> dict:
        now    = datetime.now()
        cutoff = now - timedelta(minutes=window_min)
        samples = [s for s in self.occupancy_history if s["ts"] >= cutoff]
        self._prune_entries(now)
        entries_window  = [(ts, dev) for ts, dev in self.inside_entries if ts >= cutoff]
        unique_devices  = len({dev for _, dev in entries_window})
 
        if not samples:
            return {
                "window_minutes": window_min, "sample_count": 0,
                "inside_now": 0, "inside_avg": 0.0, "inside_peak": 0, "inside_min": 0,
                "all_inside_now": 0, "all_inside_avg": 0.0, "all_inside_peak": 0, "all_inside_min": 0,
                "impressions_window": 0, "unique_devices_window": 0,
            }

        phone_vals  = [s.get("inside_phones", s["inside"]) for s in samples]
        inside_vals = [s["inside"] for s in samples]
        return {
            "window_minutes":       window_min,
            "sample_count":         len(samples),
            "inside_now":           phone_vals[-1],
            "inside_avg":           round(sum(phone_vals) / len(phone_vals), 2),
            "inside_peak":          max(phone_vals),
            "inside_min":           min(phone_vals),
            "all_inside_now":       inside_vals[-1],
            "all_inside_avg":       round(sum(inside_vals) / len(inside_vals), 2),
            "all_inside_peak":      max(inside_vals),
            "all_inside_min":       min(inside_vals),
            "impressions_window":   len(entries_window),
            "unique_devices_window": unique_devices,
        }

    def _summary(self) -> dict:
        active        = list(self.visitors.values())
        inside        = sum(1 for d in active if d.state == "inside")
        inside_phones = sum(1 for d in active if self._count_toward_people(d))
        outside       = sum(1 for d in active if d.state != "inside")
        win           = self._occupancy_stats(self.occupancy_window_min)

        return {
            "timestamp":                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "active":                    len(active),
            "inside":                    inside,
            "inside_phones":             inside_phones,
            "outside_or_nearby":         outside,
            "total_ever_seen":           self.total_ever_seen,
            "total_people_inside_events": self.total_people_inside_events,
            "window_stats":              win,
            "thresholds": {
                "inside_avg_rssi":           self.inside_avg_rssi,
                "inside_min_best_rssi":      self.inside_min_best_rssi,
                "inside_min_sightings":      self.inside_min_sightings,
                "inside_min_nodes":          self.inside_min_nodes,
                "inside_confirm_window":     self.inside_confirm_window,
                "inside_confirm_min_passes": self.inside_confirm_min_passes,
                "occupancy_window_minutes":  self.occupancy_window_min,
                "visitor_timeout_sec":       int(self.visitor_timeout.total_seconds()),
            },
            "nodes":   self._node_health(),
            "anchors": list(self.anchor_devices.values()),
        }
