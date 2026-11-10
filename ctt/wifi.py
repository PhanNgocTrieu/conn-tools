"""WiFi link information from the DUT (or host): signal, bitrate, channel.

Uses `iw dev <if> link` when available, else /proc/net/wireless (signal and
link quality only). Works locally and over SSH.
"""

from __future__ import annotations

import re

from ctt.executor import Executor

_SCRIPT = r"""
ifs="{iface}"
if [ -z "$ifs" ]; then
  for d in /sys/class/net/*; do [ -d "$d/wireless" ] || [ -d "$d/phy80211" ] && ifs="$ifs ${{d##*/}}"; done
fi
for i in $ifs; do
  echo "### $i"
  if command -v iw >/dev/null 2>&1; then iw dev "$i" link 2>&1; fi
  grep "$i:" /proc/net/wireless 2>/dev/null | sed 's/^/procwireless /'
done
"""


def parse(text: str) -> dict[str, dict]:
    """Parses the collector output into {iface: {...}}."""
    out: dict[str, dict] = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"^### (\S+)", line)
        if m:
            cur = out.setdefault(m.group(1), {"connected": False})
            continue
        if cur is None:
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
            cur["band"] = "2.4GHz" if cur["freq_mhz"] < 3000 else ("5GHz" if cur["freq_mhz"] < 5950 else "6GHz")
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
    return out


def collect(ex: Executor, iface: str | None = None, timeout: float = 15) -> dict[str, dict]:
    """Link info of `iface` (or every wireless interface). Never raises."""
    try:
        r = ex.run(["sh", "-c", _SCRIPT.format(iface=iface or "")], timeout=timeout)
        return parse(r.stdout)
    except Exception:  # noqa: BLE001 - link info is best effort
        return {}


def pick(info: dict[str, dict], iface: str | None = None) -> dict:
    """The interface to report: the requested one, else the first connected one."""
    if iface:
        return info.get(iface, {})
    for v in info.values():
        if v.get("connected"):
            return v
    return next(iter(info.values()), {})
