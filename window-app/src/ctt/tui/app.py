"""CTT console app: a menu for every link, each with its own Setup / Scan / Test entries.

    scripts/ctt            (no arguments)     or     ctt menu

    Main menu
      1) WiFi Test        setup WiFi IP, scan link / networks, quick + protocol tests
      2) USB Test         gadget + usb0 IP, scan devices, USB bulk / network tests
      3) Bluetooth Test   prepare, scan, pair, RFCOMM / L2CAP tests, BT app
      4) Board            connect (IP), check, deploy agent, status, inventory
      5) Reports
      6) Settings

Every action runs a `ctt` command and prints it first ("$ ctt test wifi/voip-call"), so the
same step can be scripted later. Answers (board IP, WiFi / USB IPs) are saved in the `ctt setup`
settings file and offered as defaults next time.
"""

from __future__ import annotations

import shlex
import subprocess
import sys

from ctt import __version__
from ctt.cli import settings
from ctt.reporting.console import color
from ctt.tui import ui
from ctt.tui.ui import Back, Item

MODES = [("PC -> board  (upload)", "tx"), ("board -> PC  (download)", "rx"),
         ("both directions at once", "bidir"), ("latency  (round trip)", "echo")]


# --------------------------------------------------------------------------- helpers

def run(argv: list[str]) -> int:
    """Runs one `ctt` command and shows it."""
    from ctt.cli.app import main
    print(color(f"\n  $ ctt {shlex.join(argv)}", "2"))
    try:
        rc = main(argv)
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
        rc = e.code if isinstance(e.code, int) else 2
    if rc not in (0, None):
        print(color(f"\n  (exit code {rc})", "33"))
    return rc or 0


def saved() -> dict[str, str]:
    return settings.load()


def store(**values: str | None) -> None:
    s = saved()
    for k, v in values.items():
        if v:
            s[k] = v
        else:
            s.pop(k, None)
    settings.save(s)


def board() -> str:
    return saved().get("dut", "local")


def is_local() -> bool:
    return board() in ("local", "localhost", "")


def need_board() -> None:
    """Board actions need an SSH target: ask for it now if it is not set."""
    if is_local():
        ui.warn("no board set yet - this needs the board's IP")
        connect_board()
        if is_local():
            raise Back


def board_shell(script: str, timeout: float = 30) -> subprocess.CompletedProcess | None:
    from ctt.core.executor import make_executor
    s = saved()
    ex = make_executor(board(), int(s["ssh_port"]) if s.get("ssh_port") else None,
                       ["ConnectTimeout=8", "StrictHostKeyChecking=accept-new"])
    try:
        return ex.run(["sh", "-c", script], timeout=timeout)
    except subprocess.TimeoutExpired:
        ui.warn(f"no answer from {board()} within {timeout:g}s")
        return None


def board_ifaces() -> list[tuple[str, str]]:
    """[(iface, ipv4)] of the board, loopback excluded."""
    r = board_shell("ip -o -4 addr show 2>/dev/null || ifconfig 2>/dev/null", 15)
    out = []
    for line in (r.stdout if r and r.returncode == 0 else "").splitlines():
        parts = line.split()
        if len(parts) > 3 and parts[2] == "inet" and parts[1] != "lo":
            out.append((parts[1], parts[3].split("/")[0]))
    return out


def ask_data_ip(key: str, label: str, prefixes: tuple[str, ...]) -> None:
    """Asks the board's IP on one interface; offers the addresses found on the board."""
    need_board()
    cur = saved().get(key, "")
    ui.info(f"reading the board's interfaces ({board()}) ...")
    found = [(i, ip) for i, ip in board_ifaces() if i.startswith(prefixes)] or \
        [(i, ip) for i, ip in board_ifaces()]
    options = [f"{ip:<16} {i}" for i, ip in found] + ["type another address",
                                                      "none (use the SSH address)"]
    default = next((n for n, (_, ip) in enumerate(found) if ip == cur), None)
    k = ui.choose(f"Board IP on {label}", options, default)
    if k < len(found):
        value = found[k][1]
    elif k == len(found):
        value = ui.ask(f"Board IP on {label}", cur, ui.check_ip)
    else:
        value = ""
    store(**{key: value})
    ui.ok(f"{key} = {value or '(SSH address)'}")


def ask_duration(default: str = "10s") -> str:
    return ui.ask("Duration per test", default, ui.check_duration)


def quick_test(transports: list[tuple[str, str]], target_key: str | None) -> None:
    t = transports[ui.choose("Transport", [lbl for lbl, _ in transports], 0)][1] \
        if len(transports) > 1 else transports[0][1]
    m = MODES[ui.choose("Direction", [lbl for lbl, _ in MODES], 2)][1]
    argv = ["run", "-t", t, "-m", m, "-d", ask_duration()]
    if t == "udp" and m != "echo":
        argv += ["-r", ui.ask("UDP offered load (bits/s)", "50M")]
    addr = saved().get(target_key) if target_key and t in ("tcp", "udp") else None
    if addr and not is_local():
        argv += ["--target-addr", addr]
    run(argv)


def catalogue(category: str) -> list:
    from ctt.testing import catalog
    return [e for e in catalog.entries() if e.category == category]


def protocol_test(category: str) -> None:
    items = catalogue(category)
    k = ui.choose("Which test?", [f"{e.short:<22} {color(e.title, '2')}" for e in items])
    e = items[k]
    run(["test", e.key, "--list"])
    dur = ui.ask("Duration per stream (Enter = as in the profile; 10s for a quick try)",
                 check=ui.check_duration)
    if ui.ask_yes("Start"):
        run(["test", e.key] + (["-d", dur] if dur else []))


def run_all(category: str) -> None:
    items = catalogue(category)
    ui.info(f"{len(items)} tests: " + ", ".join(e.short for e in items))
    dur = ask_duration("10s")
    if not ui.ask_yes("Start all"):
        return
    results = []
    for e in items:
        results.append((e.short, run(["test", e.key, "-d", dur, "-q"])))
    print()
    for name, rc in results:
        (ui.ok if rc == 0 else ui.warn)(f"{name}: {'PASS' if rc == 0 else 'see report'}")


def status_lines(*keys: str) -> list[str]:
    s = saved()
    names = {"usb_addr": "USB IP", "wifi_addr": "WiFi IP"}
    line = f"Board: {color(board(), '1')}"
    for k in keys:
        line += f"   {names[k]}: {color(s.get(k, '-'), '1')}"
    return [line]


# --------------------------------------------------------------------------- board

def connect_board() -> None:
    cur = saved().get("dut", "")
    value = ui.ask("Board IP or user@IP ('local' = test on this PC only)", cur, ui.check_board)
    target = ui.board_target(value)
    store(dut=target)
    if target == "local":
        ui.ok("local mode: both sides run on this PC")
        return
    ui.info(f"connecting to {target} ...")
    r = board_shell("uname -nm; for p in /tmp/ctt-agent /usr/bin/ctt-agent; do "
                    "[ -x $p ] && $p version && break; done", 20)
    if r is None or r.returncode not in (0, 1) or not r.stdout.strip():
        ui.warn("SSH failed: " + ((r.stderr.strip().splitlines() or ["?"])[-1] if r else "timeout"))
        ui.info("check the IP / cable, and that `ssh " + target + "` logs in without a password")
        return
    lines = r.stdout.strip().splitlines()
    ui.ok(f"connected: {lines[0]}")
    if len(lines) > 1:
        ui.ok(lines[1])
        if f" {__version__} " not in f" {lines[1]} ":
            ui.warn(f"this app is {__version__}: Board -> Deploy agent")
    else:
        ui.warn("ctt-agent is not on the board yet: Board -> Deploy agent")


def board_menu() -> object:
    return ui.menu("Board", [
        Item("Connect (board IP)", connect_board, "SSH address of the board"),
        Item("Check everything", lambda: run(["check"]), "PC agent, SSH, board agent, data IPs"),
        Item("Deploy agent", lambda: (need_board(), run(["deploy"])), "copy ctt-agent to the board"),
        Item("Status", lambda: (need_board(), run(["board", "status"])), "BT / USB / WiFi state"),
        Item("Inventory", lambda: (need_board(), run(["dut-info"])), "kernel, interfaces, tools"),
    ], lambda: status_lines())


# --------------------------------------------------------------------------- WiFi

WIFI_LINK = r"""
for i in $(ls /sys/class/net); do
  [ -d /sys/class/net/$i/wireless ] || continue
  echo "== $i  $(cat /sys/class/net/$i/operstate)  $(ip -o -4 addr show $i 2>/dev/null | awk '{print $4}')"
  iw dev $i info 2>/dev/null | grep -E 'type|ssid|channel|txpower'
  iw dev $i link 2>/dev/null | grep -E 'Connected|SSID|freq|signal|bitrate'
  iw dev $i station dump 2>/dev/null | grep -E '^Station|signal:|tx bitrate|rx bitrate'
done
"""

WIFI_SCAN = r"""
i=$(for d in $(ls /sys/class/net); do [ -d /sys/class/net/$d/wireless ] && echo $d && break; done)
[ -n "$i" ] || { echo "no WiFi interface"; exit 1; }
iw dev $i scan 2>&1 | awk '/^BSS/{b=$2} /freq:/{f=$2} /signal:/{s=$2" "$3} /SSID:/{sub(/^[ \t]*SSID: ?/,""); printf "%-32s %6s MHz  %s  %s\n", $0, f, s, substr(b,1,17)}' | sort -u
"""


def wifi_setup() -> None:
    ask_data_ip("wifi_addr", "WiFi (wlan / ap)", ("wlan", "wl", "ap", "uap", "mlan"))
    addr = saved().get("wifi_addr")
    if addr:
        from ctt.cli.commands.easy import _ping
        if _ping(addr):
            ui.ok(f"{addr} answers ping from this PC")
        else:
            ui.warn(f"{addr} does not answer ping: is this PC on the board's WiFi / AP?")


def wifi_scan() -> None:
    need_board()
    k = ui.choose("Scan", ["board WiFi link (interface, SSID, signal, bitrate, stations)",
                           "nearby networks seen by the board"], 0)
    r = board_shell(WIFI_LINK if k == 0 else WIFI_SCAN, 40)
    if r is not None:
        print((r.stdout + r.stderr).rstrip() or "  (no WiFi interface on the board)")


def wifi_menu() -> object:
    return ui.menu("WiFi Test", [
        Item("Setup", wifi_setup, "board WiFi IP (read from the board or typed)"),
        Item("Scan", wifi_scan, "link state / nearby networks"),
        Item("Quick test", lambda: quick_test([("TCP", "tcp"), ("UDP", "udp")], "wifi_addr"),
             "TCP / UDP throughput or latency"),
        Item("Protocol tests", lambda: protocol_test("wifi"),
             "CarPlay, Android Auto, Miracast, VoIP, streaming ..."),
        Item("Run all WiFi tests", lambda: run_all("wifi")),
    ], lambda: status_lines("wifi_addr"))


# --------------------------------------------------------------------------- USB

def usb_setup() -> None:
    need_board()
    k = ui.choose("USB gadget on the board", [
        "USB bulk only     (USB bulk tests, Android Auto / UAC / UVC models)",
        "USB bulk + NCM    (also IP over USB: usb0, for CarPlay wired / NCM tests)",
        "remove the gadget",
        "show gadget status"], 1)
    argv = [["usb", "gadget", "up"], ["usb", "gadget", "up", "--ncm"],
            ["usb", "gadget", "down"], ["usb", "gadget", "status"]][k]
    run(argv)
    if k == 1 and ui.ask_yes("Set the board's usb0 IP now"):
        have = [ip for i, ip in board_ifaces() if i.startswith("usb")]
        if not have:
            ip = ui.ask("Board IP/prefix for usb0", "192.168.7.2/24")
            r = board_shell(f"ip addr add {shlex.quote(ip)} dev usb0 && ip link set usb0 up", 15)
            (ui.ok if r and r.returncode == 0 else ui.warn)(
                f"usb0 {ip}" if r and r.returncode == 0 else f"could not set usb0: {r.stderr.strip() if r else ''}")
            ui.info("give the PC side an address in the same subnet, e.g. 192.168.7.1/24")
        ask_data_ip("usb_addr", "USB networking (usb0)", ("usb", "ncm", "rndis"))


def usb_scan() -> None:
    k = ui.choose("Scan", ["USB devices on this PC (the board as a gadget shows up here)",
                           "USB devices on the board (board as USB host)",
                           "gadget status + board network interfaces"], 0)
    if k == 0:
        run(["usb", "list", "--side", "host"])
        return
    need_board()
    if k == 1:
        run(["usb", "list"])
    else:
        run(["usb", "gadget", "status"])
        for i, ip in board_ifaces():
            print(f"  {i:<10} {ip}")


def usb_menu() -> object:
    return ui.menu("USB Test", [
        Item("Setup", usb_setup, "gadget (bulk / NCM) and usb0 IP"),
        Item("Scan", usb_scan, "USB devices, speed, gadget state"),
        Item("Quick test", lambda: quick_test(
            [("USB bulk (raw)", "usb"), ("USB network TCP (NCM)", "tcp"),
             ("USB network UDP (NCM)", "udp")], "usb_addr"), "bulk or IP-over-USB throughput"),
        Item("Protocol tests", lambda: protocol_test("usb"),
             "CarPlay / Android Auto wired, UAC, UVC, NCM ..."),
        Item("Run all USB tests", lambda: run_all("usb")),
    ], lambda: status_lines("usb_addr"))


# --------------------------------------------------------------------------- Bluetooth

def bt_scan() -> None:
    k = ui.choose("Scan", ["nearby devices (from this PC)", "nearby devices (from the board)",
                           "controllers on this PC and on the board"], 0)
    secs = "10"
    if k < 2:
        secs = ui.ask("Scan time in seconds", "10", lambda v: None if v.isdigit() else "a number")
    if k == 0:
        run(["bt", "scan", "-t", secs])
    elif k == 1:
        need_board()
        run(["bt", "scan", "-t", secs, "--side", "dut"])
    else:
        run(["bt", "list"])
        if not is_local():
            run(["bt", "list", "--side", "dut"])


def bt_pair() -> None:
    need_board()
    addr = ui.ask("Board BT address (Enter = read it from the board)", "",
                  lambda v: None if not v or len(v.split(":")) == 6 else "AA:BB:CC:DD:EE:FF")
    run(["bt", "pair"] + ([addr] if addr else []) + ["--dut-accept"])


def bt_quick() -> None:
    need_board()
    quick_test([("RFCOMM (SPP)", "rfcomm"), ("L2CAP (BR/EDR)", "l2cap")], None)


def bt_app() -> None:
    from ctt.btapp.console.menu import main as bt_menu
    print(color("\n  $ ctt-app console", "2"))
    bt_menu()


def bt_menu() -> object:
    return ui.menu("Bluetooth Test", [
        Item("Setup", lambda: (need_board(), run(["bt", "prepare"])),
             "board controller on, pairable, discoverable"),
        Item("Scan", bt_scan, "nearby devices / controllers"),
        Item("Pair PC <-> board", bt_pair),
        Item("Quick test", bt_quick, "RFCOMM / L2CAP throughput or latency"),
        Item("Protocol tests", lambda: (need_board(), protocol_test("bluetooth"))),
        Item("Bluetooth app", bt_app, "BLE GATT / L2CAP CoC, terminal, suites"),
    ], lambda: status_lines())


# --------------------------------------------------------------------------- reports / settings

def reports() -> None:
    from ctt.paths import REPORTS
    runs = sorted((d for d in REPORTS.glob("*_*") if d.is_dir()), reverse=True)[:10]
    if not runs:
        ui.info(f"no reports yet in {REPORTS}")
        return
    print(f"\n  {REPORTS}")
    for d in runs:
        verdict = ""
        try:
            import json
            s = json.loads((d / "summary.json").read_text())
            res = s.get("tests", [])
            passed = sum(1 for r in res if r.get("verdict") == "PASS")
            verdict = f"{passed}/{len(res)} passed" + (f"  [{s['dut']}]" if s.get("dut") else "")
        except (OSError, ValueError, AttributeError):
            pass
        print(f"   {d.name:<44} {verdict}")
    print(f"\n  open: {runs[0] / 'report.html'}")


def settings_menu() -> object:
    return ui.menu("Settings", [
        Item("Show", lambda: run(["setup", "--show"])),
        Item("Board IP", connect_board),
        Item("Edit all (questions)", lambda: run(["setup"])),
        Item("Reset", lambda: settings.save({}) if ui.ask_yes("Forget all saved values", False)
             else None),
    ], lambda: [f"file: {settings.path()}"])


# --------------------------------------------------------------------------- main

def welcome() -> None:
    ui.banner(f"CTT - Connectivity Test Tool   v{__version__}",
              "throughput / latency tests for WiFi, USB and Bluetooth")
    if not saved().get("dut"):
        print("\n  Welcome! Enter the board's IP to test it over SSH,")
        print("  or press Enter to try the tool on this PC only (local loopback).")
        try:
            value = ui.ask("Board IP", "local", ui.check_board)
        except Back:
            value = "local"
        store(dut=ui.board_target(value))
        if not is_local():
            connect_board()


def main(argv: list[str] | None = None) -> int:
    ui.use_utf8_output()
    if argv:   # `python -m ctt.tui run ...`: the full CLI
        from ctt.cli.app import main as cli_main
        return cli_main(argv)
    try:
        welcome()
        ui.menu("Main menu", [
            Item("WiFi Test", wifi_menu),
            Item("USB Test", usb_menu),
            Item("Bluetooth Test", bt_menu),
            Item("Board", board_menu, "connect, check, deploy agent"),
            Item("Reports", reports),
            Item("Settings", settings_menu),
        ], lambda: status_lines("wifi_addr", "usb_addr"), exit_label="Exit")
    except (Back, KeyboardInterrupt):
        pass
    print("bye")
    return 0
