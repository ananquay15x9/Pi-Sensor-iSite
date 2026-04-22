#!/usr/bin/python3
"""
main.py — Runner.
 
Usage:
    python3 main.py                    # normal production mode
    python3 main.py --config 10        # calibration mode for 10 minutes
    python3 main.py --config-file /path/to/config.json
""" 

import argparse
import json
import logging
import os
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
 
import config as cfg_mod
import console as con
from engine import Engine

# ── Logging ───────────────────────────────────────────────────────────────────
 
def log(msg: str, log_file: str):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(log_file, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ── HTTP transport# ──
def make_handler(engine: Engine, cfg: dict):
    log_file = cfg["device"]["log_file"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass #default access log

        def log_error(self, fmt, *args):
            pass #silence BrokenPipe noise from auto-refresh

        def _send_json(self, data: dict, status: int = 200):
            try:
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass #client closed connection before response completed -> normal
            
        def _send_html(self, html: str):
            try:
                body = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass #client closed connection before response completed -> normal

        
        def _read_json(self):
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return None
            return json.loads(self.rfile.read(length))

        def do_POST(self):
            if self.path == "/report":
                data = self._read_json()
                if not data:
                    self._send_json({"status": "error", "msg": "no body"}, 400)
                    return
                result = engine.process_event(data)
                self._send_json(result)
            else:
                self._send_json({"error": "not found"}, 404)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/status":
                self._send_json({
                    "summary":        engine.get_summary(),
                    "active_devices": engine.get_active_devices(),
                    "recent_events":  engine.get_recent_events(),
                })
            elif path == "/summary":
                self._send_json(engine.get_summary())
            elif path == "/devices":
                self._send_json({
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "count":     len(engine.visitors),
                    "devices":   engine.get_active_devices(),
                })
            elif path == "/events":
                self._send_json({
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "count":     len(engine.recent_events),
                    "events":    engine.get_recent_events(),
                })
            elif path == "/occupancy":
                self._send_json(engine.get_occupancy())
            elif path == "/daily":
                daily_file = cfg["device"]["daily_report_file"]
                history = []
                if os.path.exists(daily_file):
                    with open(daily_file) as f:
                        history = [l.strip() for l in f if l.strip()]
                self._send_json({
                    "today": {
                        "date":        datetime.now().strftime("%Y-%m-%d"),
                        "impressions": engine.total_people_inside_events,
                        "seen":        engine.total_ever_seen,
                    },
                    "history": history,
                })
            elif path == "/dashboard":
                self._send_html(con.render_dashboard_html(engine, cfg))
            elif path == "/health":
                self._send_json({"status": "ok", "ts": datetime.now().isoformat()})
            else:
                self._send_json({"error": "not found"}, 404)

    return Handler

def run_http(engine: Engine, cfg: dict):
    host = cfg["http"]["host"]
    port = cfg["http"]["port"]
    handler = make_handler(engine, cfg)
    server  = HTTPServer((host, port), handler)
    print(f"[main] HTTP listening on {host}:{port}", flush=True)
    server.serve_forever()


 # ── background loops ──────────────────────────────────────────────────────────
def cleanup_loop(engine: Engine, log_file: str):
    while True:
        time.sleep(2)
        engine.record_occupancy_sample()
 
 
def console_loop(engine: Engine, cfg: dict):
    refresh = cfg.get("console", {}).get("refresh_sec", 5)
    while True:
        time.sleep(refresh)
        try:
            con.render_console(engine, cfg)
        except Exception as e:
            print(f"[console] render error: {e}", flush=True)


def daily_report_loop(engine: Engine, cfg: dict):
    """Wakes at midnight and writes the daily report."""
    daily_file = cfg["device"]["daily_report_file"]
    log_file   = cfg["device"]["log_file"]
    while True:
        now           = datetime.now()
        next_midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        time.sleep((next_midnight - now).total_seconds())
        report_date = datetime.now().strftime("%Y-%m-%d")
        lines = [
            "",
            "=" * 60,
            f"  DAILY REPORT -- {report_date}",
            "=" * 60,
            f"  Impressions (confirmed inside) : {engine.total_people_inside_events}",
            f"  Unique devices seen            : {engine.total_ever_seen}",
            f"  Generated at                   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 60,
            "",
        ]
        text = "\n".join(lines)
        print(text, flush=True)
        try:
            with open(daily_file, "a") as f:
                f.write(text + "\n")
        except Exception as e:
            print(f"[daily] write error: {e}", flush=True)


#── calibration ──────────────────────────────────────────────────────────
def run_calibration(duration_minutes: int, cfg: dict, cfg_path: str):
    """
    Config mode:
      1. get rssi_limit from observed edge-device RSSI
      2. make a blacklist of static non-phone MACs seen repeatedly
      3. print JSON result to console
      4. write results back to config.json
    """
    print(f"[config] Calibration mode: {duration_minutes} minute(s)", flush=True)

    edge_macs     = cfg_mod.get_edge_device_macs(cfg)        # mac -> label
    pi_ouis       = cfg_mod.get_raspberry_pi_ouis()
    deadline      = time.time() + duration_minutes * 60
    # during calibration we use no RSSI floor - we want to observe everything
    # including far edge devices. the floor computed at the end is the output, not an input gate
    CALIB_RSSI_FLOOR = -100 #accept everything during observation

    # some observations during the calibration
    edge_rssi_samples: dict = defaultdict(list)   # mac -> [rssi, ...]
    all_mac_counts:    dict = defaultdict(int)     # mac -> appearance count
    all_mac_rssi:      dict = defaultdict(list)    # mac -> [rssi, ...]
    all_mac_orgs:      dict = {}                   # mac -> org


    def is_randomized(mac: str) -> bool:
        try:
            return bool(int(mac.split(":")[0], 16) & 0x02)
        except Exception:
            return False

    def is_pi_oui(mac: str) -> bool:
        prefix = ":".join(mac.split(":")[:3]).lower()
        return prefix in pi_ouis

    # mini http server to receive probes during calibration
    class CalibHandler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            if self.path != "/report":
                self.send_response(404); self.end_headers(); return
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                self.send_response(400); self.end_headers(); return
            data = json.loads(self.rfile.read(length))
            mac  = data.get("mac", "").lower().strip()
            rssi = data.get("rssi", -100)
            org  = data.get("org", "UNKNOWN")
 
            if not mac or len(mac) != 17:
                self.send_response(200); self.end_headers()
                self.wfile.write(b'{"status":"ignored"}'); return
 
            # no floor during calibration - just observe everything
            all_mac_counts[mac] += 1
            all_mac_rssi[mac].append(rssi)
            all_mac_orgs[mac] = org
            if mac in edge_macs:
                edge_rssi_samples[mac].append(rssi)
                print(f"[calib] Edge device: {edge_macs.get(mac, mac)} @ {rssi} dBm", flush=True)
 
            self.send_response(200); self.end_headers()
            self.wfile.write(b'{"status":"calibrating"}')
 

    host = cfg["http"]["host"]
    port = cfg["http"]["port"]
    server = HTTPServer((host, port), CalibHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    print(f"[config] Listening on :{port} for {duration_minutes}m — send probes from listeners now.", flush=True)
    dot_interval = max(10, duration_minutes * 60 // 20)
    last_dot = time.time()
    while time.time() < deadline:
        time.sleep(1)
        if time.time() - last_dot >= dot_interval:
            elapsed = int(time.time() - (deadline - duration_minutes * 60))
            remaining = int(deadline - time.time())
            print(f"  {elapsed}s elapsed, {remaining}s remaining …", flush=True)
            last_dot = time.time()

    server.shutdown()
    print("\n[config] Calibration complete. Computing results...", flush=True)

    import statistics
    # compute thresholds from edge devices
    # anchor to the weakest/farthest device
    # edge devices placed at the room boundary represent the minimum
    # the device closest to the sensor gives a strong reading that should not set the bar - the furthest one does
    # using the weakest ensures phones at the room boundary still count as inside

    FLOOR_OFFSET = -10 # rssi_floor = boundary + offset (more negative = looser)
    AVG_OFFSET   = -7    # inside_avg_rssi = boundary + offset
    BEST_OFFSET  = -5   # inside_min_best_rssi = boundary + offset

    # compute per device medians
    device_medians = {}
    edge_summary   = {}
    
    #  ───── compute RSSI limit from edge devices  ───────────────────
    for mac, samples in edge_rssi_samples.items():
        if not samples:
            continue
        label = edge_macs[mac]
        median = statistics.median(samples)
        device_medians[label] = median
        edge_summary[label] = {
            "mac":     mac,
            "samples": len(samples),
            "median":  round(median, 1),
            "min":     min(samples),
            "max":     max(samples),
        }
        

    if device_medians:
        # anchor to the weakest (most negative) edge device
        boundary_label  = min(device_medians, key=device_medians.get)
        boundary_median = device_medians[boundary_label]

        rssi_floor      = int(boundary_median + FLOOR_OFFSET)
        inside_avg      = int(boundary_median + AVG_OFFSET)
        inside_best     = int(boundary_median + BEST_OFFSET)

        # operational range
        rssi_floor  = max(-85, min(-35, rssi_floor))
        inside_avg  = max(-85, min(-35, inside_avg))
        inside_best = max(-85, min(-30, inside_best))

        print(f"[calib] Boundary anchor: {boundary_label} @ {boundary_median:.1f} dBm "
              f"(weakest of {len(device_medians)} edge device(s))", flush=True)
        print(f"[calib] Computed → floor:{rssi_floor}  avg:{inside_avg}  best:{inside_best}", flush=True)

    else:
        rssi_floor  = cfg["thresholds"]["rssi_floor"]
        inside_avg  = cfg["thresholds"]["inside_avg_rssi"]
        inside_best = cfg["thresholds"]["inside_min_best_rssi"]
        boundary_label = "none"
        boundary_median = 0
        print("[calib] WARNING: no edge device probes received — keeping current thresholds", flush=True)
        print("[calib] Make sure beacon.py is running on the calibration Pis and their MACs", flush=True)
        print("[calib] are listed under network.edge_devices in config.json", flush=True)
        
    # ── build blacklist: static MACs seen 3+ times that aren't randomized ──────
    new_blacklist = []
    for mac, count in all_mac_counts.items():
        if is_randomized(mac):
            continue
        if is_pi_oui(mac):
            continue
        if mac in edge_macs:
            continue
        if count >= 3:
            org = all_mac_orgs.get(mac, "")
            avg_rssi = sum(all_mac_rssi[mac]) / len(all_mac_rssi[mac])
            new_blacklist.append({
                "mac":      mac,
                "org":      org,
                "count":    count,
                "avg_rssi": round(avg_rssi, 1),
            })



    result = {
        "calibration_date":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "duration_minutes":    duration_minutes,
        "boundary_anchor":     boundary_label,
        "boundary_median_dbm": round(boundary_median, 1),
        "computed": {
            "rssi_floor":           rssi_floor,
            "inside_avg_rssi":      inside_avg,
            "inside_min_best_rssi": inside_best,
        },
        "edge_devices_observed": edge_summary,
        "new_blacklist":         new_blacklist,
        "total_macs_seen":       len(all_mac_counts),
        "total_probes":          sum(all_mac_counts.values()),
        "tuning_note": (
            "Thresholds anchored to the weakest edge device. "
            "If phones inside are missed, lower inside_avg_rssi by 3-5 dBm. "
            "If outside devices count as inside, raise rssi_floor by 3-5 dBm."
        ),
    }

    print("\n" + "=" * 60, flush=True)
    print("CALIBRATION RESULT", flush=True)
    print("=" * 60, flush=True)
    print(json.dumps(result, indent=2), flush=True)
    print("=" * 60, flush=True)

    # ── update config file ─────────
    cfg["thresholds"]["rssi_floor"]           = rssi_floor
    cfg["thresholds"]["inside_avg_rssi"]      = inside_avg
    cfg["thresholds"]["inside_min_best_rssi"] = inside_best

    existing_blacklist = {m.lower() for m in cfg["network"].get("blacklisted", [])}
    for entry in new_blacklist:
        existing_blacklist.add(entry["mac"])
    cfg["network"]["blacklisted"] = sorted(existing_blacklist)
 
    cfg_mod.save(cfg, cfg_path)
    print(f"\n[config] Config updated → {cfg_path}", flush=True)
    print(f"[config] rssi_floor={rssi_floor}  inside_avg={inside_avg}  inside_best={inside_best}", flush=True)
    print(f"[config] {len(new_blacklist)} MAC(s) added to blacklist", flush=True)
    print(f"[config] Review thresholds and adjust if needed before production run.", flush=True)


# ── entry point ──────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Probe Mesh — Brain Node")
    parser.add_argument(
        "--config", type=int, metavar="MINUTES",
        help="Run calibration mode for N minutes then exit"
    )
    parser.add_argument(
        "--config-file", default=cfg_mod.CONFIG_PATH, metavar="PATH",
        help=f"Path to config.json (default: {cfg_mod.CONFIG_PATH})"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable debug console output"
    )
    args = parser.parse_args()
 
    cfg_path = args.config_file
    cfg      = cfg_mod.load(cfg_path)
    log_file = cfg["device"]["log_file"]
 
    if args.debug:
        cfg["console"]["debug"] = True
 
    if args.config:
        run_calibration(args.config, cfg, cfg_path)
        return

    #  ───production mode──────────────────────────────────────────
    engine = Engine(cfg)

    log(f"=== iSite Sensor Started === role={cfg['device']['role']} id={cfg['device']['id']}", log_file)
    log(f"VISITOR_TIMEOUT={cfg['thresholds']['visitor_timeout_sec']}s", log_file)
    log(f"INSIDE gate: avg>={cfg['thresholds']['inside_avg_rssi']}dBm  "
        f"best>={cfg['thresholds']['inside_min_best_rssi']}dBm  "
        f"sightings>={cfg['thresholds']['inside_min_sightings']}", log_file)
    log(f"MERGE: window={cfg['merge']['window_sec']}s  "
        f"tolerance=±{cfg['merge']['rssi_tolerance']}dBm", log_file)
    log(f"Nodes: {cfg['network']['nodes']}", log_file)
    log(f"Edge devices: {list(cfg_mod.get_edge_device_macs(cfg).values())}", log_file)
    log(f"Blacklisted MACs: {len(cfg_mod.get_blacklisted_macs(cfg))}", log_file)

    # start background threads
    threading.Thread(target=cleanup_loop,      args=(engine, log_file), daemon=True).start()
    threading.Thread(target=console_loop,      args=(engine, cfg),      daemon=True).start()
    threading.Thread(target=daily_report_loop, args=(engine, cfg),      daemon=True).start()

    #── Why the loopback server always runs ──────────────────────────────────
    """
    probemon.py is a separate process. It sends signal traffic via HTTP POST
    to http://127.0.0.1.5000/report. If this server is not running, every report 
    from probemon fails silently. MainBrain stays 'offline' forever and the
    engine receives nothing.

    - http.enabled controls whether the DASHBOARD is accessible externally.
    It does not control whether the internal transport between probemon
    and the engine works. That loopback channel must always be open
    Basically, this is just a local transport layer bound to 127.0.0.1 

    * Internal loopback will always run on 127.0.0.1 so probemon can POST 
    regardless of http.enabled setting (currently set to off by default)
    """
    import threading as _threading
    _internal = HTTPServer(("127.0.0.1", cfg.get("http", {}).get("port", 5000)),
                           make_handler(engine, cfg))
    _threading.Thread(target=_internal.serve_forever, daemon=True).start()
    log("Internal loopback server started on 127.0.0.1 (probemon transport)", log_file)

    # User-facing HTTP server — only starts if http.enabled = true (it is set to false by default)
    http_cfg = cfg.get("http", {})
    if http_cfg.get("enabled", False):
        host = http_cfg.get("host", "127.0.0.1")
        port = http_cfg.get("port", 5000)
        if host == "127.0.0.1":
            # Same address as internal — just use the internal server, nothing extra needed
            log(f"HTTP dashboard enabled → http://127.0.0.1:{port}/dashboard", log_file)
            log("(served by internal loopback — use SSH tunnel to access remotely)", log_file)
        else:
            # Separate network-facing server on 0.0.0.0
            log(f"HTTP enabled → http://{host}:{port}/dashboard", log_file)
            log("WARNING: HTTP exposed on all interfaces — disable before venue deployment", log_file)
            run_http(engine, cfg)  # blocks here — binds 0.0.0.0
            return
    else:
        log("HTTP dashboard disabled (http.enabled=false) — console-only mode", log_file)
        log("Probemon reports via internal loopback — MainBrain will show online", log_file)

    # Keep main thread alive — background threads + internal server handle everything
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("Shutting down.", log_file)


if __name__ == "__main__":
    main()
