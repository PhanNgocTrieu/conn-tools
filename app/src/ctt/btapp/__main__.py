"""ctt-agent / ctt-agent-console: Bluetooth throughput test app.

    python -m ctt.btapp                 desktop window (ctt-agent.exe)
    python -m ctt.btapp console         interactive console menu (ctt-agent-console.exe)
    python -m ctt.btapp scan | paired
    python -m ctt.btapp pair ADDR [--board root@ip]
    python -m ctt.btapp unpair ADDR
    python -m ctt.btapp run  [--board root@ip | --addr ADDR --channel N] -m tx|rx|bidir|echo -d 10
    python -m ctt.btapp term [--board root@ip | --addr ADDR --channel N]
"""

from __future__ import annotations

import argparse
import sys

from ctt import __version__


def build_parser(prog: str = "ctt-agent") -> argparse.ArgumentParser:
    from ctt.btapp.console import commands
    p = argparse.ArgumentParser(prog=prog, description="CTT Bluetooth throughput test")
    p.add_argument("--version", action="version", version=f"{prog} {__version__}")
    sub = p.add_subparsers(dest="cmd")
    commands.register(sub)
    sub.add_parser("gui", help="desktop window")
    sub.add_parser("console", help="interactive console menu")
    return p


def use_utf8_output() -> None:
    """Windows writes to a pipe / SSH session in the ANSI code page (cp1252), which
    turns Vietnamese text into '?'. Always write UTF-8 instead."""
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if stream is not None and enc != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv=None, default: str = "gui", prog: str = "ctt-agent") -> int:
    use_utf8_output()
    args = build_parser(prog).parse_args(argv)
    cmd = args.cmd or default
    if cmd == "gui":
        from ctt.btapp.gui import main as gui_main
        return gui_main()
    if cmd == "console":
        from ctt.btapp.console.menu import main as menu_main
        return menu_main()
    try:
        return args.func(args)
    except (RuntimeError, OSError) as e:
        print(f"{prog}: {e}", file=sys.stderr)
        return 2


def console_main(argv=None) -> int:
    """Entry point of ctt-agent-console.exe: menu by default, same sub-commands."""
    return main(argv, default="console", prog="ctt-agent-console")


if __name__ == "__main__":
    sys.exit(main())
