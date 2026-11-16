"""Saved settings for the `ctt` command line, so the board address is typed once.

    ~/.config/ctt/settings.yaml     (or $CTT_CONFIG)

        dut: root@192.168.1.10      # board reached by SSH ('local' = this machine)
        usb_addr: 192.168.7.2       # board IP on USB networking (usb0), for usb/ profiles
        wifi_addr: 192.168.4.1      # board IP on WiFi (wlan0), for wifi/ profiles

Precedence for every value: command-line option > environment ($CTT_DUT) > this file > default.
The file lives in the user's home, never in the repository (it holds lab addresses).
"""

from __future__ import annotations

import os
from pathlib import Path

# key -> (question shown by `ctt setup`, argparse dest it fills)
KEYS: dict[str, tuple[str, str | None]] = {
    "dut": ("Board SSH target [user@]host ('local' = run both sides on this PC)", "dut"),
    "ssh_port": ("SSH port (empty = 22)", "ssh_port"),
    "dut_agent": ("ctt-agent path on the board (empty = /tmp/ctt-agent)", "dut_agent"),
    "board_profile": ("Board profile file (empty = auto: config/boards/board*.yaml)", "board_profile"),
    "usb_addr": ("Board IP on USB networking / usb0 (empty = same as SSH)", None),
    "wifi_addr": ("Board IP on WiFi / wlan0 (empty = same as SSH)", None),
}


def path() -> Path:
    env = os.environ.get("CTT_CONFIG")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "ctt" / "settings.yaml"


def load() -> dict[str, str]:
    """Flat `key: value` file (a YAML subset, read without PyYAML)."""
    p = path()
    out: dict[str, str] = {}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip().strip("'\"")
        if k.strip() in KEYS and v:
            out[k.strip()] = v
    return out


def save(values: dict[str, str]) -> Path:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# ctt settings - written by `ctt setup`; edit freely"]
    lines += [f"{k}: {values[k]}" for k in KEYS if values.get(k)]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def apply(args) -> None:
    """Fills options the user did not give from $CTT_DUT and the settings file."""
    if not hasattr(args, "dut"):          # command without a board connection (selftest, report, ...)
        return
    saved = load()
    if args.dut is None:
        args.dut = os.environ.get("CTT_DUT") or saved.get("dut") or "local"
    if args.dut in ("", "local", "localhost"):
        return                             # board-side settings do not apply to loopback runs
    for key, (_, dest) in KEYS.items():
        if dest and dest != "dut" and hasattr(args, dest) and getattr(args, dest) is None \
                and key in saved:
            setattr(args, dest, int(saved[key]) if key == "ssh_port" else saved[key])


def validate(values: dict[str, str]) -> list[str]:
    """Problems in the values (empty list = fine)."""
    from ctt.tui.ui import check_board, check_ip
    out = []
    if values.get("dut") and check_board(values["dut"]):
        hint = " (put the port in ssh_port)" if ":" in values["dut"].rpartition("@")[2] else ""
        out.append(f"dut: {check_board(values['dut'])}{hint}")
    if values.get("ssh_port") and not values["ssh_port"].isdigit():
        out.append("ssh_port: a number, e.g. 22")
    for k in ("usb_addr", "wifi_addr"):
        if values.get(k) and check_ip(values[k]):
            out.append(f"{k}: {check_ip(values[k])}")
    return out


def data_addr(category: str, saved: dict[str, str] | None = None) -> str | None:
    """Board IP the PC connects to for a catalogue category (usb -> usb_addr, wifi -> wifi_addr)."""
    saved = load() if saved is None else saved
    return saved.get({"usb": "usb_addr", "wifi": "wifi_addr"}.get(category, ""))
