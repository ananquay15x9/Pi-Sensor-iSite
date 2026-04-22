# iSite Sensor

Detects people in a defined space by passively listening for WiFi probe
requests that phones broadcast automatically.

---

## How it works

Every phone with WiFi enabled periodically broadcasts "probe request" frames
looking for known networks. These frames are visible to any adapter in
monitor mode. This system captures those frames, filters out non-phones,
and estimates how many people are currently inside a defined area.
The system counts presence, not identity.

---

## Deployment shape

### Deployed restroom unit (permanent)

One Raspberry Pi with one Panda WiFi dongle.

```
main.py       ← brain / aggregator / HTTP server
engine.py     ← all detection logic
config.py     ← config loader
console.py    ← terminal display + dashboard HTML (if enabled)
utils.py      ← shared helpers
probemon.py   ← local packet sniffer (runs separately, sudo required)
config.json   ← the one file operators edit
```

### Temporary calibration kit (setup only, not deployed)

Raspberry Pis running `beacon.py`. Placed at the room boundary during
the 10-minute calibration run, then removed.

```
beacon.py     ← forces 2.4GHz probe requests so sensor can measure their RSSI
```

---

## Normal startup (production)
Before starting, ensure mon0 exists:
```bash
sudo iw dev wlan1 interface add mon0 type monitor 2>/dev/null
sudo ip link set mon0 up
```
Then, run this:
```bash
# Terminal 1 — brain + aggregator
python3 main.py

# Terminal 2 — local packet sniffer (requires root)
sudo python3 probemon.py -i mon0
```



---

## Calibration flow

Calibration computes realistic RSSI thresholds for the specific room.
Run it every time we deploy in a new space.

```bash
# 1. Place calibration Pis at the room boundary (doorway / far corners)
#    Run on each calibration Pi:
sudo python3 beacon.py

# 2. On the sensor Pi — two terminals simultaneously:
python3 main.py --config 10
sudo python3 probemon.py -i mon0

# 3. Wait 10 minutes. Calibration exits automatically.
# 4. Review config.json — thresholds are updated automatically.
# 5. Manually verify thresholds make sense (see Threshold Tuning below).
# 6. Edit the config.json if RSSI thresholds need to be adjusted.
# 7. Remove calibration Pis.
# 8. Start normal production mode.
```

Calibration anchors thresholds to the **weakest** edge device — the one
furthest from the sensor. This correctly defines the room boundary.

**Important:** restart both `main.py` and `probemon.py` after any config.json
change. Both load config once at startup.

---

## Viewing the dashboard

The dashboard is disabled by default in production (`http.enabled = false`).
To view it safely without exposing the Pi to the venue network:

```bash
# On your laptop — create SSH tunnel
ssh -L 5000:127.0.0.1:5000 sensor@<tailscale-ip>

# Then open in browser
http://localhost:5000/dashboard
```

Enable it in config.json:
```json
"http": {
  "enabled": true,
  "host": "127.0.0.1",
  "port": 5000
}
```

**Never set `host` to `"0.0.0.0"` at a venue.** That exposes the server
on the venue's WiFi network.

> **Note:** Even when `http.enabled = false`, an internal loopback server
> always runs on `127.0.0.1:5000`. This is required — `probemon.py` sends
> probe reports to that address. The `http.enabled` flag controls only
> whether the dashboard is accessible, not whether the internal transport works.

---

## What each metric means

### People Inside 
Live estimate of phones currently inside the defined area. Updated every
few seconds. This is the primary operational metric.

### Peak / Avg / Impressions
Rolling window stats (default 10 minutes). Useful for post-event reporting.
Peak = highest simultaneous count. Impressions = how many times a device
transitioned to "inside" during the window.

### MAC Records (debug)
How many unique MAC address records the engine has created since startup.
This is **not** a people count. Modern phones rotate their MAC address every
few minutes, so one phone can generate many MAC records over an hour.
Use for tuning, not for reporting.

### Inside Transitions (debug)
How many times any device transitioned to "inside" state. Also inflated by
MAC rotation. Use for tuning, not for reporting.

> **In plain English:** People Inside is your live occupancy estimate.
> MAC Records and Inside Transitions are debug counters, not unique-person counts.

---

## Threshold tuning

Three thresholds control what counts as "inside." All in config.json under
`"thresholds"`. Adjust after calibration if the auto-computed values are off.

### `rssi_floor` — Gate 1, hard cutoff
Signals weaker than this are dropped before any processing.
- Too many outside/background devices counted? → **raise/tighten** (e.g. -63 → -58)
- Phones inside being missed entirely? → **lower/loosen** (e.g. -63 → -70)

### `inside_avg_rssi` — Gate 2, room average
The median-of-node-medians RSSI must clear this to count as inside.
- Phones detected but never reach "inside"? → **loosen** (e.g. -60 → -65)

### `inside_min_best_rssi` — Gate 3, closest node floor
At least one node must hear the phone stronger than this.
- At 9-10ft mounting height, pocketed phones typically read -62 to -70 dBm.
- Inside count is low? → **loosen** (e.g. -58 → -63)

**Safe starting values:**
```json
"rssi_floor":           -75,
"inside_avg_rssi":      -68,
"inside_min_best_rssi": -65
```

---

## Known limitations

**Randomized MAC churn**
Modern iOS and Android devices use MAC aeddress randomization during Wi-Fi probe scanning. 
As a result, a single physical phone may appear as multiple MAC addresses over time.
The scripts apply time and signal-based merging to reduce duplication, but this cannot
fully eliminate MAC churn, especially in crowded environments.
Occupancy is therfore an estimate, not an exact count. The accuracy may vary depending on crowd
density, movement, and layout. 

**Totals are not unique people**
MAC Records and Inside Transitions inflate over time due to MAC rotation.
Do not use them as people counts in reports. Use People Inside (live) and
Impressions (rolling window).



**Probe request frequency varies by OS**
iOS phones with screen off probe rarely (every 1-5 minutes).
Android phones probe more frequently.
A phone sitting quietly may timeout and reappear as a "new" device.
`visitor_timeout_sec` (default 60s) controls how long we wait before
considering a device gone.

---

## File reference

| File | Lives on | Purpose |
|---|---|---|
| `main.py` | Sensor Pi | Runner — local transport layer, calibration mode |
| `engine.py` | Sensor Pi | All detection logic — imported by main.py |
| `config.py` | Sensor Pi | Config loader — imported by main.py |
| `console.py` | Sensor Pi | Terminal display + dashboard HTML (if enabled) — imported by main.py |
| `utils.py` | Sensor Pi | Shared helpers — imported by engine + console |
| `probemon.py` | Sensor Pi | Local packet sniffer — run separately with sudo |
| `config.json` | Sensor Pi | The one file operators edit |
| `beacon.py` | Calibration Pis | Forces 2.4GHz probe bursts during calibration |

---

## This script is based on the original script.

Key differences:

| Original | Current Script |
|---|---|
| Single script, no config file | Modular: engine / config / console / transport |
| Hardcoded thresholds | Config-driven, auto-calibrated per room |
| No HTTP transport | Internal loopback + optional dashboard |
| fuzzywuzzy for brand matching | OUI keyword filter + randomized MAC detection |
| Python 2 | Python 3.13 |
| Config mode = blacklist only | Config mode = blacklist + RSSI threshold derivation |
| No MAC rotation handling | Rotation detection via RSSI group matching |
| No occupancy history | Rolling occupancy window, peak, avg, impressions |
| No anchor/edge device concept | Temporary calibration Pis define room boundary |
