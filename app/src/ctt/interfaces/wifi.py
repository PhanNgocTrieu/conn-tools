"""WiFi link information from the DUT (or host): signal, bitrate, channel.

Uses `iw dev <if> link` when available, else /proc/net/wireless (signal and
link quality only). Works locally and over SSH.

When the interface is an access point (hostapd on wlan0,
e.g. wireless projection), `iw dev <if> link` has nothing to say; the link is then
described from `iw dev <if> info` (channel, width) and `iw dev <if> station
dump` (one entry per client: signal, bitrates). The client to report is the
one whose MAC is given (`station=`), else the strongest one.
"""

from __future__ import annotations

import re

from ctt.core.executor import Executor

_SCRIPT = r"""
ifs="{iface}"
if [ -z "$ifs" ]; then
  for d in /sys/class/net/*; do [ -d "$d/wireless" ] || [ -d "$d/phy80211" ] && ifs="$ifs ${{d##*/}}"; done
fi
for i in $ifs; do
  echo "### $i"
  if command -v iw >/dev/null 2>&1; then
    iw dev "$i" info 2>&1 | sed 's/^/info /'
    iw dev "$i" link 2>&1
    iw dev "$i" station dump 2>&1 | sed 's/^/sta /'
  fi
  grep "$i:" /proc/net/wireless 2>/dev/null | sed 's/^/procwireless /'
done
"""


def parse(text: str) -> dict[str, dict]:
    """Parses the collector output into {iface: {...}}."""
    out: dict[str, dict] = {}
    cur = None
    sta = None
    for line in text.splitlines():
        m = re.match(r"^### (\S+)", line)
        if m:
            cur = out.setdefault(m.group(1), {"connected": False})
            sta = None
            continue
        if cur is None:
            continue
        if line.startswith("info "):
            _parse_info(cur, line[5:].strip())
            continue
        if line.startswith("sta "):
            s = line[4:].strip()
            if m := re.match(r"Station ([0-9a-fA-F:]{17})", s):
                sta = {"mac": m.group(1).lower()}
                cur.setdefault("stations", []).append(sta)
            elif sta is not None:
                _parse_station_line(sta, s)
            continue
        s = line.strip()
        if m := re.match(r"Connected to ([0-9a-fA-F:]{17})", s):
            cur["connected"] = True
            cur["bssid"] = m.group(1).lower()
        elif s.startswith("Not connected"):
            cur["connected"] = False
        elif m := re.match(r"SSID: (.*)", s):
            cur["ssid"] = m.group(1)
        elif m := re.match(r"freq: ([\d.]+)", s):
            cur["freq_mhz"] = float(m.group(1))
            cur["band"] = _band(cur["freq_mhz"])
        elif m := re.match(r"signal: (-?\d+) dBm", s):
            cur["signal_dbm"] = int(m.group(1))
        elif m := re.match(r"(rx|tx) bitrate: ([\d.]+) MBit/s\s*(.*)", s):
            cur[f"{m.group(1)}_bitrate_mbps"] = float(m.group(2))
            if m.group(3):
                cur[f"{m.group(1)}_rate_info"] = m.group(3)
        elif s.startswith("procwireless"):
            # "procwireless wlan0: 0000   70.  -40.  -256        0 ..."
            parts = s.split()
            try:
                cur.setdefault("link_quality", float(parts[3].rstrip(".")))
                cur.setdefault("signal_dbm", int(float(parts[4].rstrip("."))))
            except (IndexError, ValueError):
                pass
    for v in out.values():
        if v.get("mode") == "AP":
            v["connected"] = bool(v.get("stations"))
    return out


def _parse_info(cur: dict, s: str) -> None:
    if m := re.match(r"type (\S+)", s):
        cur["mode"] = m.group(1)
    elif m := re.match(r"ssid (.*)", s):
        cur.setdefault("ssid", m.group(1))
    elif m := re.match(r"channel (\d+) \((\d+) MHz\)(?:, width: (\d+) MHz)?", s):
        cur["channel"] = int(m.group(1))
        cur.setdefault("freq_mhz", float(m.group(2)))
        cur.setdefault("band", _band(cur["freq_mhz"]))
        if m.group(3):
            cur["width_mhz"] = int(m.group(3))
    elif m := re.match(r"txpower ([\d.]+) dBm", s):
        cur["txpower_dbm"] = float(m.group(1))


def _parse_station_line(sta: dict, s: str) -> None:
    if m := re.match(r"signal:\s*(-?\d+)", s):
        sta["signal_dbm"] = int(m.group(1))
    elif m := re.match(r"(rx|tx) bitrate:\s*([\d.]+) MBit/s\s*(.*)", s):
        sta[f"{m.group(1)}_bitrate_mbps"] = float(m.group(2))
        if m.group(3):
            sta[f"{m.group(1)}_rate_info"] = m.group(3)
    elif m := re.match(r"(tx retries|tx failed|connected time|inactive time):\s*(\d+)", s):
        sta[m.group(1).replace(" ", "_")] = int(m.group(2))


def _band(freq: float) -> str:
    return "2.4GHz" if freq < 3000 else ("5GHz" if freq < 5950 else "6GHz")


def collect(ex: Executor, iface: str | None = None, timeout: float = 15) -> dict[str, dict]:
    """Link info of `iface` (or every wireless interface). Never raises."""
    try:
        r = ex.run(["sh", "-c", _SCRIPT.format(iface=iface or "")], timeout=timeout)
        return parse(r.stdout)
    except Exception:  # noqa: BLE001 - link info is best effort
        return {}


def pick(info: dict[str, dict], iface: str | None = None, station: str | None = None) -> dict:
    """The interface to report: the requested one, else the first connected one.

    For an access point the chosen client's signal and bitrates are copied to
    the top level (as seen from the DUT), so the same keys work in STA and AP mode."""
    chosen: dict = {}
    if iface:
        chosen = info.get(iface, {})
    else:
        chosen = next((v for v in info.values() if v.get("connected")), None) or \
            next(iter(info.values()), {})
    if chosen.get("mode") == "AP" and chosen.get("stations"):
        stas = chosen["stations"]
        s = next((x for x in stas if station and x["mac"] == station.lower()), None) or \
            max(stas, key=lambda x: x.get("signal_dbm", -999))
        chosen = dict(chosen)
        chosen["station"] = s["mac"]
        for k in ("signal_dbm", "tx_bitrate_mbps", "rx_bitrate_mbps", "tx_rate_info", "rx_rate_info"):
            if k in s:
                chosen[k] = s[k]
    return chosen
