"""The easy way in: `ctt setup`, `ctt check`, `ctt test`, `ctt menu` (also plain `ctt`).

    ctt setup                      answer a few questions once (board address, data IPs)
    ctt check                      is everything ready? (PC agent, SSH, board agent, IPs)
    ctt test                       list the test catalogue
    ctt test carplay-wireless      run one catalogue test with the saved settings
    ctt                            menus: WiFi / USB / Bluetooth / Board (ctt.tui)

They build on the full commands (`ctt run`, `ctt profile`, ...), which stay for scripts / CI.
"""

from __future__ import annotations

import shlex
import subprocess
import sys

from ctt import __version__
from ctt.cli import settings
from ctt.cli.base import Command
from ctt.cli.options import add_dut_args, add_out_args
from ctt.reporting.console import color

CATEGORY_TITLES = {"usb": "USB", "wifi": "WiFi", "bluetooth": "Bluetooth", "": "General"}


def _ok(msg: str) -> None:
    print(f"  {color('OK  ', '32')} {msg}")


def _bad(msg: str, hint: str = "") -> None:
    print(f"  {color('FAIL', '31')} {msg}" + (f"\n        -> {hint}" if hint else ""))


def _info(msg: str) -> None:
    print(f"  {color('--  ', '36')} {msg}")


# --------------------------------------------------------------------------- setup

class SetupCommand(Command):
    name = "setup"
    help = "save the board address and data IPs once (asked interactively)"

    def configure(self, p):
        p.add_argument("values", nargs="*", metavar="KEY=VALUE",
                       help=f"set without questions; keys: {', '.join(settings.KEYS)} "
                            "(empty value removes the key)")
        p.add_argument("--show", action="store_true", help="print the saved settings")

    def run(self, args):
        saved = settings.load()
        if args.show:
            print(f"# {settings.path()}")
            for k in settings.KEYS:
                print(f"{k:<14} {saved.get(k, '-')}")
            return 0
        if args.values:
            for item in args.values:
                k, sep, v = item.partition("=")
                if not sep or k not in settings.KEYS:
                    raise ValueError(f"expected KEY=VALUE with KEY in {', '.join(settings.KEYS)}")
                saved[k] = v.strip()
        else:
            print("ctt setup - Enter keeps the value in [brackets], '-' clears it.\n")
            for k, (question, _) in settings.KEYS.items():
                cur = saved.get(k, "")
                ans = _input(f"{question}\n  {k} [{cur}]: ").strip()
                if ans == "-":
                    saved.pop(k, None)
                elif ans:
                    saved[k] = ans
        saved = {k: v for k, v in saved.items() if v}
        problems = settings.validate(saved)
        if problems:
            raise ValueError("not saved: " + "; ".join(problems))
        print(f"saved {settings.save(saved)}")
        print("next: ctt check")
        return 0


# --------------------------------------------------------------------------- check

class CheckCommand(Command):
    name = "check"
    help = "check that the PC, the SSH link and the board agent are ready"

    def configure(self, p):
        add_dut_args(p)

    def run(self, args):
        from ctt.cli.options import dut_agent_path, dut_executor
        from ctt.core.agent import find_host_agent
        from ctt.core.executor import LocalExecutor
        saved = settings.load()
        failures = 0
        print(f"ctt {__version__}   settings: {settings.path()}"
              + ("" if saved else "  (none yet: run `ctt setup`)"))

        print("\nPC")
        try:
            import yaml  # noqa: F401
            _ok("PyYAML (test profiles)")
        except ImportError:
            failures += 1
            _bad("PyYAML missing", "pip install pyyaml  (or apt install python3-yaml)")
        try:
            agent = find_host_agent()
            out = subprocess.run([agent, "version"], capture_output=True, text=True, timeout=10)
            _ok(f"{out.stdout.strip() or agent}  ({agent})")
        except (FileNotFoundError, OSError, subprocess.SubprocessError) as e:
            failures += 1
            _bad(f"PC agent: {e}", "scripts/build.sh linux, or set CTT_AGENT=/path/to/ctt-agent")

        print(f"\nBoard ({args.dut})")
        dut = dut_executor(args)
        if isinstance(dut, LocalExecutor):
            _info("DUT is 'local': both sides run on this PC (loopback). "
                  "Set the board with `ctt setup` to test real hardware.")
        else:
            try:
                r = dut.run("uname -m", timeout=15)
                reachable = r.returncode == 0
            except subprocess.TimeoutExpired:
                reachable, r = False, None
            if not reachable:
                failures += 1
                _bad(f"SSH to {args.dut}: " + ((r.stderr.strip().splitlines() or ['no answer'])[-1]
                                               if r else "timeout"),
                     "check the cable / IP, and that `ssh <board>` works without a password prompt")
            else:
                _ok(f"SSH reachable (arch {r.stdout.strip()})")
                path = dut_agent_path(args, dut)
                r = dut.run(f"{shlex.quote(path)} version", timeout=15)
                ver = r.stdout.strip()
                if r.returncode != 0:
                    failures += 1
                    _bad(f"agent not found at {path}", "ctt deploy  (copies build/ctt-agent-aarch/ctt-agent)")
                elif f" {__version__} " not in f" {ver} ":
                    _bad(f"{ver} at {path} - this PC runs {__version__}",
                         "ctt deploy  (an older board agent misses newer features, e.g. bursts)")
                    failures += 1
                else:
                    _ok(f"{ver}  ({path})")

        print("\nData addresses (for usb/ and wifi/ tests)")
        for key in ("usb_addr", "wifi_addr"):
            addr = saved.get(key)
            if not addr:
                _info(f"{key}: not set - tests connect to the SSH address")
            elif _ping(addr):
                _ok(f"{key} {addr} answers ping")
            else:
                _bad(f"{key} {addr} does not answer ping",
                     "link down? (USB: ctt usb gadget up --ncm; WiFi: join the board's AP)")
        print("\n" + (color("ready", "32") if not failures
                      else color(f"{failures} problem(s) - see the hints above", "31")))
        return 0 if not failures else 1


def _ping(addr: str) -> bool:
    flag = ["-n", "1", "-w", "1000"] if sys.platform == "win32" else ["-c", "1", "-W", "1"]
    try:
        return subprocess.run(["ping", *flag, addr], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# --------------------------------------------------------------------------- test

class TestCommand(Command):
    name = "test"
    help = "list the test catalogue, or run one test by name (e.g. ctt test carplay-wireless)"

    def configure(self, p):
        p.add_argument("test", nargs="?", help="catalogue name, e.g. wifi/voip-call or voip-call")
        p.add_argument("-d", "--duration", default=None, help="override every stream's duration")
        p.add_argument("--only", action="append", help="run sub-tests whose name contains this")
        p.add_argument("--list", action="store_true", help="show what the test runs and exit")
        p.add_argument("--addr", default=None, dest="target_addr",
                       help="board IP for the data (default: usb_addr / wifi_addr from ctt setup)")
        add_dut_args(p)
        add_out_args(p)

    def run(self, args):
        from ctt.testing import catalog
        if not args.test:
            print_catalog(catalog.entries())
            return 0
        entry = catalog.find(args.test)
        if args.target_addr is None and args.dut != "local":
            args.target_addr = settings.data_addr(entry.category)
        print(f"{entry.key}: {entry.title}")
        if args.target_addr:
            print(f"data address: {args.target_addr}")
        from ctt.cli.commands.testing import ProfileCommand
        args.profile = str(entry.path)
        return ProfileCommand().run(args)


def print_catalog(items) -> None:
    from ctt.testing import catalog
    for cat in catalog.categories(items):
        print(f"\n{CATEGORY_TITLES.get(cat, cat)}")
        for e in (e for e in items if e.category == cat):
            print(f"  {e.key:<28} {e.title}")
    print("\nrun:  ctt test <name> [-d 10s]      details: ctt test <name> --list")


# --------------------------------------------------------------------------- menu

def _input(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        raise KeyboardInterrupt from None


class MenuCommand(Command):
    name = "menu"
    help = "console app: WiFi / USB / Bluetooth menus (same as `ctt` alone)"

    def run(self, args):
        from ctt.tui.app import main
        return main()
