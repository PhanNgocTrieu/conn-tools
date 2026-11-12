"""`ctt` command line: parser assembly and entry point."""

from __future__ import annotations

import argparse
import sys

from ctt import __version__
from ctt.cli.base import Command
from ctt.cli.commands.bluetooth import BluetoothCommand
from ctt.cli.commands.dut import DeployCommand, DutInfoCommand
from ctt.cli.commands.firmware import FirmwareCommand
from ctt.cli.commands.mfi import MfiCommand
from ctt.cli.commands.reports import CompareCommand, ReportCommand
from ctt.cli.commands.testing import ProfileCommand, RunCommand, SelftestCommand
from ctt.cli.commands.usb import UsbCommand

COMMANDS: list[type[Command]] = [
    RunCommand, ProfileCommand, SelftestCommand,
    DeployCommand, DutInfoCommand,
    BluetoothCommand, UsbCommand, FirmwareCommand,
    ReportCommand, CompareCommand, MfiCommand,
]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ctt", description="Connectivity Test Tool", allow_abbrev=False)
    p.add_argument("--version", action="version", version=f"ctt {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    for cls in COMMANDS:
        cls().add_to(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler.run(args)
    except (FileNotFoundError, ValueError, RuntimeError) as e:  # includes BtError
        print(f"ctt: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nctt: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
