"""
console.py — Terminal rendering only.
 
takes already-computed data from Engine and prints it. No merge logic, no config and no transport.

"""


import os
import sys
from datetime import datetime
from typing import Any, Dict, List

# ── add colors ─────────────────────────────────────────────────────────────

def _color_enabled(cfg: dict) -> bool:
    if not cfg.get("console", {}).get("color", True):
        return False
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("TERM", "").lower() == "dumb":
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _c(text: str, code: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


def _status_badge(status: str, color: bool) -> str:
    codes = {"online": "32", "delayed": "33", "offline": "31"}
    return _c(status, codes.get(status, "37"), color)
 
 
def _trend_arrow(current: int, previous: int, color: bool) -> str:
    if current > previous: return _c("↑", "32", color)
    if current < previous: return _c("↓", "31", color)
    return _c("→", "36", color)


# name helper 
def friendly_device_name(device) -> str:
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


# ── main render ───────────────────────────────────────────────────────────────
def render_console(engine, cfg: dict):
    """
    print a live status block to stdout
    engine: engine instance
    cfg: full config dict
    """
    color   = _color_enabled(cfg)
    debug   = cfg.get("console", {}).get("debug", False) or \
              os.getenv("DEBUG_CONSOLE", "0").lower() in {"1", "true", "yes"}
    refresh = cfg.get("console", {}).get("refresh_sec", 5)


    with engine._lock:
        engine._purge_stale(datetime.now())
        summary         = engine._summary()
        devices         = engine._sorted_devices()[:10]
        events          = list(engine.recent_events)[:8]
        node_rows       = list(summary["nodes"])
        anchor_rows     = list(summary["anchors"])
        occ_points      = list(engine.occupancy_history)

    window     = summary["window_stats"]["window_minutes"]
    prev_phones = occ_points[-2]["inside_phones"] if len(occ_points) > 1 else summary["inside_phones"]
    arrow       = _trend_arrow(summary["inside_phones"], prev_phones, color)

    bar = "=" * 40
    sep = "-" * 40
 
    print("\n" + _c(bar, "1;37", color), flush=True)
    print(_c("iSite Sensor Monitor", "1;36", color), flush=True)
    print(_c(bar, "1;37", color), flush=True)
    print("", flush=True)
 
    print(
        f"{_c('PEOPLE INSIDE:', '1;37', color)}      "
        f"{_c(str(summary['inside_phones']), '1;32', color)} {arrow}",
        flush=True,
    )
    print(f"TOTAL SEEN:           {summary['total_ever_seen']}", flush=True)
    print(f"PEAK INSIDE ({window}m):      {summary['window_stats']['inside_peak']}", flush=True)
    print(f"AVERAGE INSIDE ({window}m):   {summary['window_stats']['inside_avg']:.2f}", flush=True)
    print(f"INSIDE HITS ({window}m):      {summary['window_stats']['impressions_window']}", flush=True)
    print("", flush=True)

    print(_c(sep, "2;37", color), flush=True)
    print(_c("NODES", "1;37", color), flush=True)
    for row in node_rows:
        age     = row["seconds_since_last_report"]
        age_str = "never" if age is None else f"{age:>4.1f}s ago"
        print(f"  {row['node']:<12} {_status_badge(row['status'], color):<7} {age_str}", flush=True)

    print("", flush=True)
    print(_c(sep, "2;37", color), flush=True)
    nearby = summary["outside_or_nearby"]
    print(f"{nearby} detected nearby / outside", flush=True)
    print(_c("(set DEBUG_CONSOLE=1 for more detail)", "2;37", color), flush=True)

    if not debug:
        return

    print("", flush=True)
    print(_c(sep, "2;37", color), flush=True)
    print(_c("DEBUG", "1;35", color), flush=True)
    print(
        f"time={summary['timestamp']} | seen={summary['total_ever_seen']} "
        f"| inside_events={summary['total_people_inside_events']}",
        flush=True,
    )

    if events:
        print("recent activity:", flush=True)
        for ev in events[:6]:
            print(
                f"  {ev['ts'][11:19]} - {human_event_label(ev['event'])} "
                f"({ev['state']}, {ev['zone']})",
                flush=True,
            )

    if anchor_rows:
        print("anchors:", flush=True)
        for a in anchor_rows:
            print(
                f"  {a['label']:<14} mac={a['mac']} "
                f"rssi={a.get('last_rssi','?')}dBm seen={a.get('last_seen','?')}",
                flush=True,
            )

    if devices:
        print("active devices:", flush=True)
        print("  ID        MAC                  TYPE               STATE     NODES  BEST  AVG  DWELL", flush=True)
        for d in devices:
            print(
                f"  {d.device_id:<8} {d.masked_mac():<20}  {d.classification():<17}  "
                f"{d.state:<8}  {d.compact_nodes():<6} {d.best_rssi:>4}  "
                f"{d.avg_rssi_room:>6}  {d.dwell_seconds:>4}s",
                flush=True,
            )
    print(_c(sep, "2;37", color), flush=True)


# ── Dashboard HTML ──────────
def render_dashboard_html(engine, cfg: dict) -> str:
    """Return the full dashboard HTML string."""
    with engine._lock:
        engine._purge_stale(datetime.now())
        summary    = engine._summary()
        devs       = engine._sorted_devices()
        events     = list(engine.recent_events)[:20]
 
    phones     = [d for d in devs if engine._is_probable_phone(d)]
    non_phones = [d for d in devs if not engine._is_probable_phone(d)]

    # node rows
    node_rows = ""
    for row in summary["nodes"]:
        age   = row["seconds_since_last_report"]
        age_s = "never" if age is None else f"{age:.1f}s ago"
        color = {"online": "#22c55e", "delayed": "#f59e0b", "offline": "#ef4444"}.get(row["status"], "#94a3b8")
        node_rows += (
            f"<tr><td>{row['node']}</td>"
            f"<td style='color:{color}'>{row['status']}</td>"
            f"<td>{age_s}</td><td>{row['reports_received']}</td></tr>"
        )

    # anchor rows
    anchor_rows = ""
    for a in summary.get("anchors", []):
        anchor_rows += (
            f"<tr><td>{a['label']}</td><td>{a['mac']}</td>"
            f"<td>{a.get('last_rssi','?')}&nbsp;dBm</td>"
            f"<td>{a.get('last_seen','?')}</td><td>{a.get('node','?')}</td></tr>"
        )

    def device_row(d):
        sc = {"inside": "#22c55e", "fringe": "#f59e0b", "new": "#94a3b8"}.get(d.state, "#94a3b8")
        sh = {"inside": "Inside", "fringe": "Outside", "new": "Detected"}.get(d.state, d.state.title())
        return (
            f"<tr><td>{friendly_device_name(d)}</td><td>{d.masked_mac()}</td>"
            f"<td style='color:{sc};font-weight:bold'>{sh}</td>"
            f"<td>{d.compact_nodes()}</td>"
            f"<td>{d.best_rssi}&nbsp;dBm</td><td>{d.avg_rssi_room}&nbsp;dBm</td>"
            f"<td>{d.confidence():.0%}</td><td>{d.dwell_seconds}s</td></tr>"
        )
 
    phone_rows = "".join(device_row(d) for d in phones) or \
        "<tr><td colspan='8' style='color:#475569;font-style:italic'>No phones detected</td></tr>"
    other_rows = "".join(device_row(d) for d in non_phones) or \
        "<tr><td colspan='8' style='color:#475569;font-style:italic'>None</td></tr>"
 
    event_rows = ""
    for ev in events:
        event_rows += (
            f"<tr><td>{ev['ts'][11:]}</td>"
            f"<td>{human_event_label(ev.get('event',''))}</td>"
            f"<td>{ev.get('device_label','Unknown')}</td>"
            f"<td>{ev['masked_mac']}</td>"
            f"<td>{ev['state'].title()}</td>"
            f"<td>{ev['best_rssi']}&nbsp;dBm</td>"
            f"<td>{ev['avg_rssi']}&nbsp;dBm</td></tr>"
        )

    anchor_section = ""
    if anchor_rows:
        anchor_section = f"""
        <h2>&#128204; Listener Pi Anchors</h2>
        <table><tr><th>Label</th><th>MAC</th><th>Last RSSI</th><th>Last Seen</th><th>Reported By</th></tr>
        {anchor_rows}</table>"""

    win = summary["window_stats"]
    nodes_online = len([n for n in summary["nodes"] if n["status"]=="online"])
    nodes_label = f'{nodes_online} node{"s" if nodes_online != 1 else ""} online'
    return f"""<!DOCTYPE html>
<html>
<head>
    <title>iSite Sensor Dashboard</title>
    <meta http-equiv="refresh" content="3">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
        *{{box-sizing:border-box;margin:0;padding:0}}
        body{{font-family:-apple-system,Arial,sans-serif;background:#0f172a;color:#e2e8f0;padding:20px}}
        h1{{font-size:22px;color:#f1f5f9;margin-bottom:4px}}
        h2{{font-size:14px;color:#94a3b8;text-transform:uppercase;letter-spacing:1px;margin:24px 0 10px;
            padding-bottom:6px;border-bottom:1px solid #1e293b}}
        .subtitle{{color:#475569;font-size:13px;margin-bottom:20px}}
        .debug-note{{color:#334155;font-size:11px;margin-top:16px;padding:8px 12px;
            border:1px solid #1e293b;border-radius:6px;max-width:700px}}
        .hero{{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-start;margin-bottom:8px}}
        .hero-card{{background:#1e293b;padding:28px 48px;border-radius:16px;text-align:center;
            border:1px solid #22c55e33}}
        .hero-card .label{{font-size:13px;color:#64748b;text-transform:uppercase;letter-spacing:1px}}
        .hero-card .value{{font-size:72px;font-weight:800;color:#22c55e;line-height:1}}
        .stat-card{{background:#1e293b;padding:16px 20px;border-radius:12px;text-align:center}}
        .stat-card .label{{font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.5px}}
        .stat-card .value{{font-size:32px;font-weight:700;margin-top:4px}}
        .debug-grid{{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}}
        .debug-card{{background:#0f1a2e;border:1px solid #1e293b;padding:10px 16px;
            border-radius:8px;text-align:center;min-width:120px}}
        .debug-card .label{{font-size:10px;color:#475569;text-transform:uppercase}}
        .debug-card .value{{font-size:20px;font-weight:600;color:#475569;margin-top:2px}}
        table{{width:100%;border-collapse:collapse;margin-bottom:8px;font-size:13px}}
        th,td{{border:1px solid #1e293b;padding:7px 10px;text-align:left}}
        th{{background:#1e293b;color:#64748b;font-weight:600;font-size:11px;text-transform:uppercase}}
        tr:hover td{{background:#1e293b44}}
        .section-phone{{background:#0f2720;border-radius:10px;padding:12px 16px;margin-bottom:16px}}
        .section-other{{background:#141e2e;border-radius:10px;padding:12px 16px;margin-bottom:16px}}
    </style>
</head>
<body>
    <h1>&#128246; iSite Sensor Dashboard</h1>
    <div class="subtitle">Updated: {summary["timestamp"]} &nbsp;·&nbsp; {nodes_label}</div>

    <div class="hero">
        <div class="hero-card">
            <div class="label">People Inside</div>
            <div class="value">{summary["inside_phones"]}</div>
        </div>
        <div class="stat-card">
            <div class="label">Peak ({win["window_minutes"]}m)</div>
            <div class="value" style="color:#22c55e">{win["inside_peak"]}</div>
        </div>
        <div class="stat-card">
            <div class="label">Avg ({win["window_minutes"]}m)</div>
            <div class="value" style="color:#60a5fa">{win["inside_avg"]:.1f}</div>
        </div>
        <div class="stat-card">
            <div class="label">Impressions ({win["window_minutes"]}m)</div>
            <div class="value" style="color:#a78bfa">{win["impressions_window"]}</div>
        </div>
    </div>
    
    <div class="debug-grid">
        <div class="debug-card">
            <div class="label">MAC Records</div>
            <div class="value">{summary["total_ever_seen"]}</div>
        </div>
        <div class="debug-card">
            <div class="label">Inside Transitions</div>
            <div class="value">{summary["total_people_inside_events"]}</div>
        </div>
        <div class="debug-card">
            <div class="label">Active Devices</div>
            <div class="value">{summary["active"]}</div>
        </div>
    </div>
    <div class="debug-note">
        MAC Records and Inside Transitions are debug counters — they count MAC rotation events, not unique people.
        Trust <strong>People Inside</strong> for live occupancy.
    </div>


    <h2>&#128293; Node Health</h2>
    <table><tr><th>Node</th><th>Status</th><th>Last Report</th><th>Reports</th></tr>
    {node_rows}</table>

    {anchor_section}

    <h2>&#128247; People Detected (Phones)</h2>
    <div class="section-phone">
        <table><tr><th>Device</th><th>MAC</th><th>Status</th><th>Nodes</th>
            <th>Best RSSI</th><th>Avg RSSI</th><th>Confidence</th><th>Dwell</th></tr>
        {phone_rows}</table>
    </div>

    <h2>&#128268; Other / Known Units</h2>
    <div class="section-other">
        <table><tr><th>Device</th><th>MAC</th><th>Status</th><th>Nodes</th>
            <th>Best RSSI</th><th>Avg RSSI</th><th>Confidence</th><th>Dwell</th></tr>
        {other_rows}</table>
    </div>

    
    <h2>&#128336; Recent Events</h2>
    <table><tr><th>Time</th><th>Event</th><th>Device</th><th>MAC</th>
        <th>State</th><th>Best RSSI</th><th>Avg RSSI</th></tr>
    {event_rows}</table>
</body></html>"""
