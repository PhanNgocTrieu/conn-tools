"""Awareness of the vendor connectivity firmware on the DUT.

The production image runs daemons that own the hardware CTT tests:

  usb-daemon        com.example.Connectivity.Usb       dwc2 port (host by default,
                                                              gadget only for CarPlay)
  bt-daemon  ...Connectivity.BtDaemon          default BlueZ pairing agent
  wifi-daemon       ...Connectivity.Wifi                      STA / AP / P2P mode of wlan0

This module reads their state (systemd + D-Bus properties via busctl), warns
when a test would fight one of them, and drives the few D-Bus calls CTT needs
(WiFi mode, accepting a pairing through bt-daemon). Everything is
best effort: on a plain Linux DUT without these daemons nothing changes.
"""

from __future__ import annotations

import re
import subprocess

from ctt.core.executor import Executor

USB_SVC = "com.example.Connectivity.Usb"
BT_SVC = "com.example.Connectivity.BtDaemon"
BT_OBJ = "/com/example/Connectivity/BtDaemon"
BT_IF = BT_SVC + "1"
WIFI_SVC = "com.example.Connectivity.Wifi"
WIFI_OBJ = "/com/example/Connectivity/Wifi"

WIFI_MODES = {0: "STA", 1: "AP", 2: "P2P"}
SERVICES = ("usb-daemon", "bt-daemon", "bluetooth", "wifi-daemon",
            "hostapd@ap", "wpa-supplicant", "audio-daemon")

STATUS_SCRIPT = r"""
for s in SERVICES; do
  command -v systemctl >/dev/null 2>&1 && echo "svc.$s=$(systemctl is-active $s 2>/dev/null)"
done
gp() { busctl --system get-property "$@" 2>/dev/null | sed 's/^[a-z]* //; s/^"//; s/"$//'; }
if command -v busctl >/dev/null 2>&1; then
  W=WIFI_SVC; WO=WIFI_OBJ
  echo "wifi.mode=$(gp $W $WO/Manager $W.Manager Mode)"
  echo "wifi.enabled=$(gp $W $WO/Manager $W.Manager Enabled)"
  for p in Enabled SSID Channel Frequency ClientCount Passphrase; do
    echo "wifi.ap.$p=$(gp $W $WO/AP $W.AP $p)"
  done
  for p in Enabled GroupSSID GroupPassphrase OperatingChannel; do
    echo "wifi.p2p.$p=$(gp $W $WO/P2P $W.P2P $p)"
  done
  for p in Powered Address Name; do echo "bt.$p=$(gp BT_SVC BT_OBJ BT_IF $p)"; done
  echo "bt.paired_raw=$(busctl --system call BT_SVC BT_OBJ BT_IF GetPairedDeviceList 2>/dev/null)"
  echo "usb.api=$(gp USB_SVC /com/example/Connectivity/Usb USB_SVC ApiVersion)"
fi
for f in /sys/bus/platform/devices/*/dwc2_drdmode /sys/class/usb_role/*/role; do
  [ -f "$f" ] && echo "usb.role:$f=$(cat $f 2>/dev/null)"
done
echo "usb.udc=$(ls /sys/class/udc 2>/dev/null | tr '\n' ' ')"
[ -f /etc/usb-daemon.conf ] && grep -E '^port\.[0-9]+\.(name|udc|role_path|carplay)=' /etc/usb-daemon.conf | sed 's/^/usbconf./'
echo "mfi.lib=$(ls /usr/lib/libvendor-mfi.so* 2>/dev/null | head -n1)"
echo "mfi.testapp=$(command -v vendor-mfi-test 2>/dev/null)"
"""
for _k, _v in {"SERVICES": " ".join(SERVICES), "WIFI_SVC": WIFI_SVC, "WIFI_OBJ": WIFI_OBJ,
               "BT_SVC": BT_SVC, "BT_OBJ": BT_OBJ, "BT_IF": BT_IF, "USB_SVC": USB_SVC}.items():
    STATUS_SCRIPT = STATUS_SCRIPT.replace(_k, _v)


def _count_paired(raw: str) -> int | None:
    """busctl reply 'bsa(ssssnbbbbbb) true "ok" N ...' -> N."""
    m = re.match(r'^bsa\(\S+\) (?:true|false) "(?:[^"\\]|\\.)*" (\d+)', raw.strip())
    return int(m.group(1)) if m else None


def parse_status(text: str) -> dict:
    st: dict = {"services": {}, "wifi": {"ap": {}, "p2p": {}}, "bt": {}, "usb": {"roles": {},
                "ports": {}}, "mfi": {}}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip()
        if key.startswith("svc."):
            if val:
                st["services"][key[4:]] = val
        elif key.startswith("wifi.ap.") or key.startswith("wifi.p2p."):
            _, grp, prop = key.split(".", 2)
            if val:
                st["wifi"][grp][prop] = val
        elif key == "wifi.mode" and val:
            try:
                st["wifi"]["mode"] = WIFI_MODES.get(int(val), val)
            except ValueError:
                st["wifi"]["mode"] = val
        elif key == "wifi.enabled" and val:
            st["wifi"]["enabled"] = val
        elif key == "bt.paired_raw":
            n = _count_paired(val)
            if n is not None:
                st["bt"]["paired"] = n
        elif key.startswith("bt.") and val:
            st["bt"][key[3:]] = val
        elif key.startswith("usb.role:"):
            st["usb"]["roles"][key[len("usb.role:"):]] = val
        elif key == "usb.udc":
            st["usb"]["udc"] = val.split()
        elif key == "usb.api" and val:
            st["usb"]["api"] = val
        elif key.startswith("usbconf."):
            m = re.match(r"usbconf\.port\.(\d+)\.(\w+)", key)
            if m:
                st["usb"]["ports"].setdefault(m.group(1), {})[m.group(2)] = val
        elif key.startswith("mfi.") and val:
            st["mfi"][key[4:]] = val
    return st


def status(ex: Executor, timeout: float = 20) -> dict:
    """Firmware state of the DUT. Never raises; {} when the DUT cannot be read."""
    try:
        r = ex.run(["sh", "-c", STATUS_SCRIPT], timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return {}
    return parse_status(r.stdout)


def active(st: dict, service: str) -> bool:
    return st.get("services", {}).get(service) == "active"


def has_vendor_firmware(st: dict) -> bool:
    return any(active(st, s) for s in ("usb-daemon", "bt-daemon", "wifi-daemon"))


def format_status(st: dict) -> str:
    if not st:
        return "(DUT firmware state not available)"
    out = ["services:"]
    for s in SERVICES:
        v = st["services"].get(s)
        if v:
            out.append(f"  {s:<20} {v}")
    w = st["wifi"]
    out.append(f"wifi:  mode={w.get('mode', '?')} enabled={w.get('enabled', '?')}")
    ap = w["ap"]
    if ap.get("Enabled") == "true" or w.get("mode") == "AP":
        out.append(f"  AP: ssid={ap.get('SSID', '?')} passphrase={ap.get('Passphrase', '?')} "
                   f"channel={ap.get('Channel', '?')} freq={ap.get('Frequency', '?')} MHz "
                   f"clients={ap.get('ClientCount', '?')}")
    p2p = w["p2p"]
    if p2p.get("Enabled") == "true":
        out.append(f"  P2P: group={p2p.get('GroupSSID', '?')} passphrase={p2p.get('GroupPassphrase', '?')} "
                   f"channel={p2p.get('OperatingChannel', '?')}")
    b = st["bt"]
    out.append(f"bt:    powered={b.get('Powered', '?')} address={b.get('Address', '?')} "
               f"name={b.get('Name', '?')} paired={b.get('paired', '?')}")
    u = st["usb"]
    roles = ", ".join(f"{p.split('/')[-2]}={v}" for p, v in u["roles"].items()) or "-"
    out.append(f"usb:   role {roles}; UDC: {' '.join(u.get('udc', [])) or 'none'}")
    for i, p in sorted(u["ports"].items()):
        out.append(f"  port {i}: {p.get('name', '?')} udc={p.get('udc', '?')} carplay={p.get('carplay', '?')}")
    m = st["mfi"]
    out.append(f"mfi:   lib={m.get('lib', '-')} vendor-mfi-test={m.get('testapp', '-')}")
    return "\n".join(out)


def warnings_for(st: dict, transports: set[str], bt_pairing: bool = False) -> list[str]:
    """Collisions between the planned tests and the running firmware."""
    w = []
    if not st:
        return w
    if "usb" in transports and active(st, "usb-daemon"):
        w.append("usb-daemon is running and owns the dwc2 port (host mode). "
                 "Run `ctt usb gadget up` first: it stops usb-daemon, switches the port to "
                 "device and builds the CTT gadget; `ctt usb gadget down` restores everything.")
    if bt_pairing and active(st, "bt-daemon"):
        w.append("bt-daemon is the default BlueZ agent on the DUT: use "
                 "`--dut-accept bm` (pairing is confirmed through its D-Bus API).")
    paired = st.get("bt", {}).get("paired")
    if bt_pairing and isinstance(paired, int) and paired >= 5:
        w.append(f"bt-daemon already holds {paired} bonds (limit 5): new pairings fail "
                 "with CapacityReached. Remove old bonds first.")
    if transports & {"tcp", "udp"} and st.get("wifi", {}).get("mode") == "AP":
        ap = st["wifi"]["ap"]
        w.append(f"WiFi of the DUT is in AP mode (ssid {ap.get('SSID', '?')}): for a WiFi test "
                 "connect the PC/dongle to that AP and use the DUT's AP address as endpoint "
                 "(e.g. --dut root@<ap-ip> or -e <ap-ip>:5201).")
    return w


# ---- actions ------------------------------------------------------------------

def _busctl(ex: Executor, *args: str, timeout: float = 20) -> str:
    r = ex.run(["busctl", "--system", *args], timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"busctl {' '.join(args[:4])}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def wifi_set_mode(ex: Executor, mode: str) -> str:
    code = {v.lower(): k for k, v in WIFI_MODES.items()}.get(mode.lower())
    if code is None:
        raise ValueError(f"unknown WiFi mode {mode!r} (sta, ap, p2p)")
    return _busctl(ex, "call", WIFI_SVC, f"{WIFI_OBJ}/Manager", f"{WIFI_SVC}.Manager",
                   "SetMode", "i", str(code))


def vendor_call(ex: Executor, method: str, signature: str = "", *args: str) -> str:
    extra = [signature, *args] if signature else []
    return _busctl(ex, "call", BT_SVC, BT_OBJ, BT_IF, method, *extra)


# Confirms every pairing request bt-daemon raises while it runs.
_VENDOR_ACCEPT = (
    "busctl --system monitor --match "
    "\"type='signal',interface='{iface}',member='PairingConfirmationRequested'\" 2>&1 | "
    "while IFS= read -r l; do case \"$l\" in *Member=PairingConfirmationRequested*) "
    "echo ctt-vendor: confirm; busctl --system call {svc} {obj} {iface} ConfirmPairing;; esac; done"
).format(svc=BT_SVC, obj=BT_OBJ, iface=BT_IF)
_VENDOR_ACCEPT_KILL = "pkill -f \"member='PairingConfirmationRequested'\" 2>/dev/null; true"


class VendorAcceptor:
    """DUT side of a pairing through bt-daemon: pairable + discoverable,
    and every PairingConfirmationRequested answered with ConfirmPairing."""

    def __init__(self, ex: Executor, discoverable: bool = True):
        self.ex = ex
        self.discoverable = discoverable
        self.proc = None
        self.confirmed = 0

    def __enter__(self):
        vendor_call(self.ex, "EnableBluetooth")
        vendor_call(self.ex, "SetPairable", "b", "true")
        if self.discoverable:
            vendor_call(self.ex, "SetDiscoverable", "b", "true")
        self.proc = self.ex.popen(["sh", "-c", _VENDOR_ACCEPT])
        return self

    def close(self):
        try:
            self.ex.run(["sh", "-c", _VENDOR_ACCEPT_KILL], timeout=10)
        except Exception:  # noqa: BLE001 - cleanup is best effort
            pass
        if self.proc:
            self.proc.terminate()
            try:
                out, _ = self.proc.communicate(timeout=5)
                self.confirmed = (out or "").count("ctt-vendor: confirm")
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    def __exit__(self, *exc):
        self.close()

