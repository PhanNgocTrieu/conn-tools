"""Interactive console menu (ctt-agent-console.exe without arguments).

Made for a PC reached over SSH (Client Test setup), where no window can be
opened. Uses the same BtTestService and the same saved settings as the GUI.
"""

from __future__ import annotations

import re
import sys
import threading
from typing import Callable

from ctt import __version__
from ctt.btapp.console.terminal import ConsoleTerminal
from ctt.btapp.expectations import DEFAULT_SUITE, LEVELS, SuiteRunner, summary_lines, write_report
from ctt.btapp.formatting import board_event_line, interval_line, result_lines
from ctt.btapp.service import BtTestService, ThroughputParams
from ctt.btapp.settings import AppSettings, SettingsStore
from ctt.protocol.ctp import MODE_LABELS

MODES = ("tx", "rx", "bidir", "echo")


BT_ADDR_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")


def ask(prompt: str, default: str = "", check: Callable[[str], str | None] | None = None) -> str:
    """Ask until the answer passes `check` (returns an error text, or None when OK)."""
    shown = f" [{default}]" if default != "" else ""
    while True:
        value = input(f"{prompt}{shown}: ").strip() or default
        error = check(value) if check else None
        if not error:
            return value
        print(f"  -> {error}")


def validate_board(value: str) -> str | None:
    if "@" not in value or value.startswith("@") or value.endswith("@"):
        return "expected user@ip, e.g. root@192.168.1.10"
    return None


def validate_bt_addr(value: str) -> str | None:
    if value and not BT_ADDR_RE.match(value.upper()):
        return "expected AA:BB:CC:DD:EE:FF, or empty"
    return None


def required_bt_addr(value: str) -> str | None:
    return validate_bt_addr(value) or (None if value else "an address AA:BB:CC:DD:EE:FF is required")


def validate_channel(value: str) -> str | None:
    if value.lower() == "auto" or (value.isdigit() and 1 <= int(value) <= 30):
        return None
    return "expected 'auto' or a number 1-30"


def ask_yes(prompt: str, default: bool = True) -> bool:
    v = ask(f"{prompt} (y/n)", "y" if default else "n",
            lambda v: None if v.lower() in ("y", "yes", "n", "no") else "answer y or n").lower()
    return v.startswith("y")


class ConsoleMenu:
    def __init__(self, store: SettingsStore | None = None):
        self.store = store or SettingsStore()
        self.settings: AppSettings = self.store.load()
        self.svc = BtTestService(self.settings)
        self.items: list[tuple[str, str, Callable[[], None]]] = [
            ("1", "Settings (board SSH / no SSH / BT address / channel)", self.configure),
            ("2", "Check board (SSH)", self.check_board),
            ("3", "Deploy agent to the board (SSH)", self.deploy),
            ("4", "Scan Bluetooth", self.scan),
            ("5", "Paired devices", self.paired),
            ("6", "Pair with the board", self.pair),
            ("7", "Unpair", self.unpair),
            ("8", "Throughput / latency test", self.throughput),
            ("9", "Terminal: send / receive data", self.terminal),
            ("10", "Test suite with Mbit/s expectations (BT-TP-01..06)", self.suite),
            ("0", "Exit", self.quit),
        ]
        self._running = True

    # ---- loop ------------------------------------------------------------------------

    def header(self) -> str:
        s = self.settings
        link = f"SSH {s.board}" if s.uses_ssh else "no SSH (agent already running on the board)"
        return (f"\n=== CTT Bluetooth console {__version__} ===\n"
                f"Board: {link} | BT: {s.address() or '?'} | channel: {s.channel}")

    def run(self) -> int:
        ok, msg = self.svc.pairing_status()
        print(f"PC Bluetooth: {self.svc.pairing.name}: {'OK' if ok else msg}")
        try:
            while self._running:
                print(self.header())
                for key, label, _ in self.items:
                    print(f"  {key}) {label}")
                choice = input("Choice: ").strip()
                action = next((fn for key, _, fn in self.items if key == choice), None)
                if not action:
                    print("Invalid choice")
                    continue
                self._safe(action)
        except (EOFError, KeyboardInterrupt):
            print()
        finally:
            self.store.save(self.settings)
            self.svc.close()
        return 0

    @staticmethod
    def _safe(action: Callable[[], None]) -> None:
        try:
            action()
        except KeyboardInterrupt:
            print("\n(stopped)")
        except (RuntimeError, OSError, ValueError) as e:
            print(f"Error: {e}")

    # ---- actions -------------------------------------------------------------------------

    def configure(self) -> None:
        s = self.settings
        s.no_ssh = not ask_yes("SSH to the board?", s.uses_ssh)
        if s.uses_ssh:
            s.board = ask("SSH board (user@ip)", s.board if validate_board(s.board) is None else "",
                          validate_board)
            s.bt_addr = ask("Board BT address (empty = read over SSH)",
                            s.address() if validate_bt_addr(s.address()) is None else "",
                            validate_bt_addr).upper()
            s.channel = ask("RFCOMM channel (auto = ask the board over SSH)", s.channel, validate_channel)
        else:
            s.bt_addr = ask("Board BT address (empty = pick later with Scan / Paired devices)",
                            s.address() if validate_bt_addr(s.address()) is None else "",
                            validate_bt_addr).upper()
            ch = s.channel if str(s.channel).isdigit() else "22"
            s.channel = ask("RFCOMM channel on the board", ch,
                            lambda v: None if v.isdigit() and 1 <= int(v) <= 30 else "expected a number 1-30")
        self.store.save(s)
        print("Saved.")

    def check_board(self) -> None:
        info = self.svc.check_board()
        fw = info.get("fw", {})
        print(f"  host       : {info['hostname'] or '?'} ({info['arch']})")
        print(f"  agent      : {info['agent'] or 'MISSING -> choose 3 to deploy'}")
        print(f"  BT address : {info.get('bt_address') or '?'}")
        print(f"  bt-manager : {fw.get('services', {}).get('bt-daemon', '-')}")
        if isinstance(fw.get("bt", {}).get("paired"), int):
            print(f"  bond       : {fw['bt']['paired']}/5")
        self.store.save(self.settings)

    def deploy(self) -> None:
        print("Deploy OK: " + self.svc.deploy_agent())

    def _devices(self, devs) -> None:
        for i, d in enumerate(devs, 1):
            print(f"  {i:2d}. {d.address}  {'[paired] ' if d.paired else ''}{d.name or '(no name)'}")
        print(f"  ({len(devs)} devices)")
        if devs and ask_yes("Use one of them as the board?", False):
            idx = int(ask("Number", "1")) - 1
            self.settings.bt_addr = devs[idx].address
            self.store.save(self.settings)

    def scan(self) -> None:
        print("Scanning (10-15 s)...")
        self._devices(self.svc.scan(10))

    def paired(self) -> None:
        self._devices(self.svc.paired())

    def pair(self) -> None:
        addr = ask("Board BT address", self.settings.address(), required_bt_addr).upper()
        accept = self.settings.uses_ssh and ask_yes("Board accepts the pairing itself over SSH?", True)
        ok, msg = self.svc.pair(addr, accept, on_pin=lambda p: print(f"  Passkey: {p}"),
                                on_log=lambda m: print(f"  {m}"))
        print(("OK  " if ok else "FAIL ") + msg)
        if ok:
            self.settings.bt_addr = addr.upper()
            self.store.save(self.settings)

    def unpair(self) -> None:
        addr = ask("Address to unpair", self.settings.address(), required_bt_addr).upper()
        print(self.svc.unpair(addr)[1])

    def throughput(self) -> None:
        for i, m in enumerate(MODES, 1):
            print(f"  {i}) {MODE_LABELS[m]}")
        mode = MODES[int(ask("Direction", "1")) - 1]
        params = ThroughputParams(
            mode=mode,
            duration_s=float(ask("Duration (s)", self.settings.duration)),
            block=int(ask("Block (byte)", self.settings.block)),
            rate_bps=int(float(ask("Rate limit Mbit/s (0 = unlimited)", "0")) * 1e6),
            verify=ask_yes("Verify CRC?", True))
        self.settings.duration, self.settings.block = str(params.duration_s), str(params.block)
        self.store.save(self.settings)
        print("Ctrl+C stops early (the result is still collected).")
        result = self._run_in_worker(
            lambda: self.svc.run_throughput(
                params, on_interval=lambda iv: print(interval_line(iv)),
                on_board_event=self._board_event,
                on_connect=lambda t: print(f"RFCOMM connect {t.address} channel {t.channel}...")),
            self.svc.stop_throughput)
        print("\n".join(result_lines(result)))

    def suite(self) -> None:
        for c in DEFAULT_SUITE.cases:
            print(f"  {c.id}  {c.mode:<5} block {c.block:<5} {c.duration_s:g}s  {c.title}")
        level = ask(f"Level ({' / '.join(LEVELS)})", "baseline",
                    lambda v: None if v in LEVELS else f"expected one of: {', '.join(LEVELS)}")
        only = ask("Case ids, comma-separated (empty = all)", "")
        cases = DEFAULT_SUITE.select(only.split(",") if only else None)
        runner = SuiteRunner(self.svc)
        print("Ctrl+C stops after the current case.")
        outcomes = self._run_in_worker(
            lambda: runner.run(
                cases, level,
                on_case=lambda i, c: print(f"\n[{i}/{len(cases)}] {c.id} {c.title} ({c.duration_s:g}s)"),
                on_interval=lambda iv: print(interval_line(iv)),
                on_board_event=self._board_event,
                on_outcome=lambda o: print(f"  -> {'PASS' if o.ok else 'FAIL'} "
                                           + (o.error or "  ".join(c.text() for c in o.checks[3:])))),
            runner.stop)
        print()
        print("\n".join(summary_lines(outcomes)))
        print(f"report: {write_report(outcomes, DEFAULT_SUITE, level, 'ctt-results')} (+ .csv)")

    def _run_in_worker(self, work: Callable, stop: Callable):
        """Run `work` in a thread so Ctrl+C calls `stop` and the result is still returned."""
        outcome: dict = {}

        def target():
            try:
                outcome["value"] = work()
            except Exception as e:  # noqa: BLE001 - re-raised below
                outcome["error"] = e

        worker = threading.Thread(target=target, daemon=True)
        worker.start()
        while worker.is_alive():
            try:
                worker.join(0.2)
            except KeyboardInterrupt:
                print("\nStopping...")
                stop()
        if "error" in outcome:
            raise outcome["error"]
        return outcome["value"]

    @staticmethod
    def _board_event(ev: dict) -> None:
        """Board agent trace (SSH mode only): what the board received / sent so far."""
        line = board_event_line(ev)
        if line:
            print(line)

    def terminal(self) -> None:
        ConsoleTerminal(self.svc).run()

    def quit(self) -> None:
        self._running = False


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    return ConsoleMenu().run()
