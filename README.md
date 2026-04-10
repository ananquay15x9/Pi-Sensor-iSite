# Pi-Sensor-iSite

Wi‑Fi **probe-request** mesh for estimating **people in an area** using multiple Raspberry Pis. One Pi runs the **aggregator + dashboard** (`sensor.py`); the others **listen** on monitor mode and POST sightings to the brain.

## Hardware roles

| Role | Machine | USB Wi‑Fi (e.g. Panda) | Runs |
|------|---------|-------------------------|------|
| **Sensor / MainBrain** | Pi that hosts the UI | Recommended (for `probemon.py` on `mon0`) | `sensor.py`, `probemon.py` |
| **Listener** | Edge Pis in the space | **Yes** — dongle provides `wlan1` / `mon0` | `sniffer.py` |
| **Known unit** | Same as listener in your setup | No need for Panda dongle | `sniffer.py` |

**Listeners** should use a dedicated dongle (e.g. Panda) so the built‑in Wi‑Fi can stay associated while the dongle captures probes. The **Sensor Pi** also runs local capture with `probemon.py` on its monitor interface.

## Prerequisites (each Pi)

- Raspberry Pi OS (or similar), Python 3.10+
- `iw`, `ip`, wireless tools
- **Listener / MainBrain capture:** Wireshark command line — install **tshark** on listener Pis (`sniffer.py`), and system deps for **Scapy** on the MainBrain (`probemon.py`)

```bash
sudo apt update
sudo apt install -y python3-pip python3-venv iw wireshark-common
# Optional: libpcap for scapy
sudo apt install -y libpcap0.8
```

Clone and install Python deps:

```bash
git clone https://github.com/ananquay15x9/Pi-Sensor-iSite.git
cd Pi-Sensor-iSite
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`sudo python3 …` often uses **system** Python. Either install deps globally:

```bash
pip install --user -r requirements.txt
# or
sudo pip install --break-system-packages -r requirements.txt
```

…or run sniffer/probemon with the venv interpreter under sudo:

```bash
sudo /path/to/Pi-Sensor-iSite/.venv/bin/python3 sniffer.py -i mon0
```

## Monitor interface (listeners + MainBrain)

On Pis that capture probes, create monitor mode on the **dongle** interface (your setup uses `wlan1` → `mon0`):

```bash
sudo iw dev wlan1 interface add mon0 type monitor
sudo ip link set mon0 up
```

Confirm:

```bash
iw dev
```

> Names can differ (`wlan0`, `wlan1`). Use the interface that belongs to the **capture** adapter.

## Configure node names and brain URL

### `sensor.py` (Sensor Pi only)

Edit the `NODES` list so it matches the `node` values sent by your reporters (e.g. `MainBrain`, `Listener1`, `Listener2`).

### `probemon.py` (MainBrain)

- Set **listener anchor MACs** in `LISTENER_PI_MACS` so the brain can report `ANCHOR:ListenerX` to `sensor.py` (dashboard “anchors” section).
- Optional environment variables:
  - `AGGREGATOR_URL` — you will have to find the correct IP address of the Pi you're using as sensor, etc `http://127.0.0.1:5000/report`
  - `NODE_ID` — default `MainBrain`

### `sniffer.py` (each listener)

Set **per device** (use your Sensor Pi’s IP and a unique node id):

```bash
export BRAIN_URL='http://YOUR_SENSOR_PI_IP:5000/report'
export NODE_ID='Listener1'   # Listener2, etc.
sudo -E env "BRAIN_URL=$BRAIN_URL" "NODE_ID=$NODE_ID" \
  python3 sniffer.py -i mon0
```

If `sudo -E` does not pass env on your system:

```bash
sudo env BRAIN_URL='http://192.168.1.50:5000/report' NODE_ID='Listener2' \
  python3 sniffer.py -i mon0
```

## Run (three-Pi example)

### Sensor Pi (brain + local probes)

```bash
pkill -f sensor.py 2>/dev/null; pkill -f probemon.py 2>/dev/null
python3 sensor.py
```

In a **second** terminal (after `mon0` is up):

```bash
sudo python3 probemon.py -i mon0
```

### Each listener Pi

```bash
sudo python3 sniffer.py -i mon0
```

(with `BRAIN_URL` and `NODE_ID` set as above)

## Dashboard

The web UI is served by Flask on port **5000**:

- `http://YOUR_SENSOR_PI_IP:5000/dashboard`
- JSON: `http://YOUR_SENSOR_PI_IP:5000/summary`

### Find your Sensor Pi IP

On the Sensor Pi:

```bash
hostname -I
ip -4 addr show```

On another machine, try your router’s DHCP client list or (if you use Tailscale/ZeroTier) that product’s admin UI — e.g. Tailscale: `tailscale ip -4` on the Pi.

**Example:** If the Pi is `100.124.55.96`, open `http://100.124.55.96:5000/dashboard`. After you clone the project, **your IP will differ** — always substitute `YOUR_SENSOR_PI_IP`.

## Data / persistence

- **RSSIs and visitor state** are kept **in memory** while `sensor.py` runs; restart clears live state.
- **Event text lines** append to `visitors.log` in the current working directory (if enabled in code) — useful for a coarse history, not a full database.

## Tuning “inside” / RSSI

Area thresholds are at the top of `sensor.py` (e.g. `INSIDE_AVG_RSSI`, `INSIDE_MIN_BEST_RSSI`). You can override many with **environment variables** — see comments in `sensor.py` near `env_int` / `INSIDE_*`.

## Repository layout

| File | Purpose |
|------|---------|
| `sensor.py` | Aggregator, REST `/report`, dashboard |
| `probemon.py` | MainBrain: Scapy sniff, posts to `sensor.py` |
| `sniffer.py` | Listeners: `tshark`, posts to `sensor.py` |
| `requirements.txt` | Python dependencies |

## License

Specify your license here if applicable.
