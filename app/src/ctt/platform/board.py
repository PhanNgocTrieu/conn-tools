"""What is special about a board, kept out of the code: the *board profile*.

CTT runs on any Linux board with the standard stack (systemd, BlueZ, configfs USB gadget,
nl80211 WiFi). Everything a given product adds - its own Bluetooth / USB / WiFi daemons, a
different way to accept a pairing, a bond limit, extra status commands - goes into a board
profile, a small YAML (or JSON) file that stays with the project, not in this repository:

    name: my-board
    services: [bluetooth, my-bt-daemon]   # systemd units shown by `ctt board status` / Check board
    status:                               # extra lines for the status: name -> shell command
      bt-daemon-version: my-bt-daemon --version
    bluetooth:
      max_bonds: 5                        # warn when the board already holds that many bonds
      bonds: my-bt-cli count-bonds        # shell printing the number of bonds (default: bluetoothctl)
      address: my-bt-cli address          # shell printing the BD address (default: sysfs / hciconfig)
      unpair: my-bt-cli remove {addr}     # default: bluetoothctl remove {addr}
      pairing:                            # how the board accepts the PC (default: own bluetoothctl agent)
        setup: [my-bt-cli power on, my-bt-cli pairable on, my-bt-cli discoverable on]
        confirm: my-bt-cli confirm-loop   # long running; prints one "ctt-confirm" line per accepted request
        stop: pkill -f confirm-loop
    usb:
      gadget_stop_services: [my-usb-daemon]   # stopped while the CTT gadget runs, restarted after
      role_file: /sys/class/usb_role/x/role   # dual-role switch (default: auto-detect)
    conflicts:                            # extra warnings before a test
      - {service: my-wifi-daemon, affects: [wifi], message: "..."}
    commands:                             # named shell commands: `ctt board cmd NAME`
      wifi-ap: my-wifi-cli mode ap

Where the profile is found (first hit): --board-profile / $CTT_BOARD_PROFILE, ./board.yaml,
config/boards/board.yaml, config/boards/board.private.yaml, <app config dir>/board.yaml (also .json).
Without a profile CTT uses the plain-BlueZ defaults ($CTT_BOARD_PROFILE=none forces them).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ctt.core.executor import Executor

from ctt.paths import CONFIGS
AFFECTS = ("usb", "bt-pairing", "wifi")


@dataclass
class PairingRecipe:
    setup: list[str] = field(default_factory=list)
    confirm: str = ""
    stop: str = ""

    @property
    def custom(self) -> bool:
        return bool(self.setup or self.confirm)


@dataclass
class BoardProfile:
    name: str = "generic Linux (BlueZ)"
    path: str = ""                                   # file it came from, "" = built-in default
    services: list[str] = field(default_factory=lambda: ["bluetooth"])
    status: dict[str, str] = field(default_factory=dict)
    max_bonds: int = 0
    bonds_cmd: str = ""
    address_cmd: str = ""
    unpair_cmd: str = ""
    pairing: PairingRecipe = field(default_factory=PairingRecipe)
    gadget_stop_services: list[str] = field(default_factory=list)
    role_file: str = ""
    conflicts: list[dict] = field(default_factory=list)
    commands: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict, path: str = "") -> "BoardProfile":
        bt = d.get("bluetooth") or {}
        pr = bt.get("pairing") or {}
        usb = d.get("usb") or {}
        p = cls(name=str(d.get("name") or Path(path).stem or cls.name), path=path)
        if "services" in d:
            p.services = [str(s) for s in d["services"] or []]
        p.status = {str(k): str(v) for k, v in (d.get("status") or {}).items()}
        p.max_bonds = int(bt.get("max_bonds") or 0)
        p.bonds_cmd = str(bt.get("bonds") or "")
        p.address_cmd = str(bt.get("address") or "")
        p.unpair_cmd = str(bt.get("unpair") or "")
        setup = pr.get("setup") or []
        p.pairing = PairingRecipe([str(c) for c in ([setup] if isinstance(setup, str) else setup)],
                                  str(pr.get("confirm") or ""), str(pr.get("stop") or ""))
        p.gadget_stop_services = [str(s) for s in usb.get("gadget_stop_services") or []]
        p.role_file = str(usb.get("role_file") or "")
        p.conflicts = list(d.get("conflicts") or [])
        for c in p.conflicts:
            bad = set(c.get("affects") or []) - set(AFFECTS)
            if not c.get("service") or bad:
                raise ValueError(f"{path}: conflict needs 'service' and affects in {AFFECTS}: {c}")
        p.commands = {str(k): str(v) for k, v in (d.get("commands") or {}).items()}
        return p

    def describe(self) -> str:
        return f"{self.name} ({self.path})" if self.path else self.name


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(Path.home(), ".config")
    return Path(base) / "ctt-bt"


def profile_candidates() -> list[Path]:
    out = []
    for base in (Path.cwd(), CONFIGS, config_dir()):
        for stem in ("board", "board.private"):
            for ext in (".yaml", ".yml", ".json"):
                out.append(base / f"{stem}{ext}")
    return out


def _read(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text) or {}
    try:
        import yaml
    except ImportError as e:
        raise ValueError(f"{path}: reading YAML needs PyYAML (pip install pyyaml), or use a .json profile") from e
    return yaml.safe_load(text) or {}


def load_profile(path: str | Path | None = None) -> BoardProfile:
    """Explicit path / $CTT_BOARD_PROFILE, else the first profile file found, else the defaults."""
    explicit = path or os.environ.get("CTT_BOARD_PROFILE")
    if explicit and str(explicit).lower() == "none":   # tests / CI: plain defaults whatever files exist
        return BoardProfile()
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise ValueError(f"board profile not found: {p}")
        return BoardProfile.from_dict(_read(p), str(p))
    for p in profile_candidates():
        if p.is_file():
            return BoardProfile.from_dict(_read(p), str(p))
    return BoardProfile()


# ---- status ---------------------------------------------------------------------------------

def _status_script(p: BoardProfile) -> str:
    q = shlex.quote
    lines = [
        "T=; command -v timeout >/dev/null 2>&1 && T='timeout 8'",
        f"for s in {' '.join(q(s) for s in p.services)}; do",
        '  command -v systemctl >/dev/null 2>&1 && echo "svc.$s=$(systemctl is-active $s 2>/dev/null)"',
        "done",
        # Bluetooth through BlueZ (or the profile's own commands)
        "if command -v bluetoothctl >/dev/null 2>&1; then",
        "  $T bluetoothctl show 2>/dev/null | sed -n 's/^Controller \\([0-9A-Fa-f:]*\\).*/bt.Address=\\1/p;"
        " s/^[[:space:]]*Powered: /bt.Powered=/p; s/^[[:space:]]*Name: /bt.Name=/p'",
        "fi",
    ]
    if p.address_cmd:
        lines.append(f'echo "bt.Address=$( {p.address_cmd} 2>/dev/null | head -n1)"')
    else:
        lines.append('a=$(cat /sys/class/bluetooth/hci0/address 2>/dev/null); [ -n "$a" ] && echo "bt.Address=$a"')
    if p.bonds_cmd:
        lines.append(f'echo "bt.paired=$( {p.bonds_cmd} 2>/dev/null | head -n1)"')
    else:
        lines.append("command -v bluetoothctl >/dev/null 2>&1 && "
                     "echo \"bt.paired=$($T bluetoothctl devices Paired 2>/dev/null | grep -c '^Device')\"")
    lines += [
        # USB: dual-role switch and device controllers (sysfs, any kernel)
        "for f in /sys/bus/platform/devices/*/dwc2_drdmode /sys/class/usb_role/*/role; do",
        '  [ -f "$f" ] && echo "usb.role:$f=$(cat $f 2>/dev/null)"',
        "done",
        "echo \"usb.udc=$(ls /sys/class/udc 2>/dev/null | tr '\\n' ' ')\"",
        # WiFi: interface type from nl80211 (managed / AP / P2P-...)
        "command -v iw >/dev/null 2>&1 && iw dev 2>/dev/null | awk '/Interface/ {i=$2} /type/ "
        "{print \"wifi.if.\" i \"=\" $2}'",
    ]
    for name, cmd in p.status.items():
        lines.append(f'echo "x.{name}=$( {cmd} 2>&1 | tr \'\\n\' \' \' )"')
    return "\n".join(lines)


def parse_status(text: str) -> dict:
    st: dict = {"services": {}, "bt": {}, "usb": {"roles": {}}, "wifi": {"interfaces": {}}, "extra": {}}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip()
        if key.startswith("svc."):
            if val:
                st["services"][key[4:]] = val
        elif key == "bt.paired":
            if val.isdigit():
                st["bt"]["paired"] = int(val)
        elif key.startswith("bt.") and val:
            st["bt"][key[3:]] = val.upper() if key == "bt.Address" else val
        elif key.startswith("usb.role:"):
            st["usb"]["roles"][key[len("usb.role:"):]] = val
        elif key == "usb.udc":
            st["usb"]["udc"] = val.split()
        elif key.startswith("wifi.if."):
            st["wifi"]["interfaces"][key[len("wifi.if."):]] = val
        elif key.startswith("x."):
            st["extra"][key[2:]] = val
    modes = set(st["wifi"]["interfaces"].values())
    if modes:
        st["wifi"]["mode"] = "AP" if "AP" in modes else sorted(modes)[0]
    return st


def status(ex: Executor, profile: BoardProfile | None = None, timeout: float = 30) -> dict:
    """State of the board's connectivity stack. Never raises; {} when the board cannot be read."""
    p = profile or load_profile()
    try:
        r = ex.run(["sh", "-c", _status_script(p)], timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return {}
    st = parse_status(r.stdout)
    st["profile"] = p.describe()
    return st


def active(st: dict, service: str) -> bool:
    return st.get("services", {}).get(service) == "active"


def format_status(st: dict) -> str:
    if not st:
        return "(board state not available)"
    out = [f"profile: {st.get('profile', '-')}", "services:"]
    for s, v in st["services"].items():
        out.append(f"  {s:<24} {v}")
    b = st["bt"]
    out.append(f"bt:    powered={b.get('Powered', '?')} address={b.get('Address', '?')} "
               f"name={b.get('Name', '?')} paired={b.get('paired', '?')}")
    u = st["usb"]
    roles = ", ".join(f"{p.split('/')[-2]}={v}" for p, v in u["roles"].items()) or "-"
    out.append(f"usb:   role {roles}; UDC: {' '.join(u.get('udc', [])) or 'none'}")
    ifs = ", ".join(f"{i}={t}" for i, t in st["wifi"]["interfaces"].items()) or "-"
    out.append(f"wifi:  {ifs}")
    for k, v in st.get("extra", {}).items():
        out.append(f"{k}: {v}")
    return "\n".join(out)


def warnings_for(st: dict, transports: set[str], bt_pairing: bool = False,
                 profile: BoardProfile | None = None) -> list[str]:
    """Collisions between the planned tests and what runs on the board."""
    w: list[str] = []
    if not st:
        return w
    p = profile or load_profile()
    if "usb" in transports:
        busy = [s for s in p.gadget_stop_services if active(st, s)]
        if busy:
            w.append(f"{', '.join(busy)} running and owning the USB port: `ctt usb gadget up` stops "
                     "it while the CTT gadget runs; `ctt usb gadget down` restarts it.")
    paired = st.get("bt", {}).get("paired")
    if bt_pairing and p.max_bonds and isinstance(paired, int) and paired >= p.max_bonds:
        w.append(f"the board already holds {paired} bonds (limit {p.max_bonds} in the board profile): "
                 "new pairings may fail. Remove old bonds first (`ctt board unpair <addr>`).")
    if transports & {"tcp", "udp"} and st.get("wifi", {}).get("mode") == "AP":
        w.append("WiFi of the board is in AP mode: connect the PC to that AP and use the board's AP "
                 "address as endpoint (e.g. --dut root@<ap-ip> or -e <ap-ip>:5201).")
    want = set()
    if "usb" in transports:
        want.add("usb")
    if transports & {"tcp", "udp"}:
        want.add("wifi")
    if bt_pairing:
        want.add("bt-pairing")
    for c in p.conflicts:
        if active(st, c["service"]) and want & set(c.get("affects") or []):
            w.append(c.get("message") or f"{c['service']} is running and may disturb this test")
    return w


# ---- actions ---------------------------------------------------------------------------------

def run_command(ex: Executor, profile: BoardProfile, name: str, timeout: float = 60) -> str:
    if name not in profile.commands:
        known = ", ".join(sorted(profile.commands)) or "none (add `commands:` to the board profile)"
        raise ValueError(f"no command {name!r} in the board profile; known: {known}")
    r = ex.run(["sh", "-c", profile.commands[name]], timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{name}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def unpair(ex: Executor, profile: BoardProfile, addr: str, timeout: float = 30) -> str:
    if not re.fullmatch(r"[0-9A-Fa-f:/_a-z]+", addr):
        raise ValueError(f"not an address: {addr!r}")
    cmd = (profile.unpair_cmd or "bluetoothctl remove {addr}").format(addr=addr)
    r = ex.run(["sh", "-c", cmd], timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"unpair {addr}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


class ProfileAcceptor:
    """Board side of a pairing done with the profile's commands (setup, confirm loop, stop)."""

    def __init__(self, ex: Executor, recipe: PairingRecipe):
        self.ex = ex
        self.recipe = recipe
        self.proc = None
        self.confirmed = 0

    def __enter__(self):
        for cmd in self.recipe.setup:
            r = self.ex.run(["sh", "-c", cmd], timeout=30)
            if r.returncode != 0:
                raise RuntimeError(f"board pairing setup `{cmd}`: {(r.stderr or r.stdout).strip()}")
        if self.recipe.confirm:
            self.proc = self.ex.popen(["sh", "-c", self.recipe.confirm])
        return self

    def close(self):
        if self.recipe.stop:
            try:
                self.ex.run(["sh", "-c", self.recipe.stop], timeout=10)
            except Exception:  # noqa: BLE001 - cleanup is best effort
                pass
        if self.proc:
            self.proc.terminate()
            try:
                out, _ = self.proc.communicate(timeout=5)
                self.confirmed = (out or "").count("ctt-confirm")
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    def __exit__(self, *exc):
        self.close()
