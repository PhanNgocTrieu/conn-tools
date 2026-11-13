"""`ctt board`: state of the board's connectivity stack and the board profile's commands."""

from __future__ import annotations

import json

from ctt.cli.base import Command, CommandGroup
from ctt.cli.options import add_dut_args, board_profile, dut_executor


class BoardStatusCommand(Command):
    name = "status"
    help = "services of the board profile, Bluetooth (BlueZ), USB role / UDC, WiFi interfaces"

    def configure(self, p):
        p.add_argument("--json", action="store_true")
        add_dut_args(p)

    def run(self, args):
        from ctt.platform import board
        st = board.status(dut_executor(args), board_profile(args))
        print(json.dumps(st, indent=2) if args.json else board.format_status(st))
        return 0 if st else 1


class BoardCmdCommand(Command):
    name = "cmd"
    help = "run a named command of the board profile (no name: list them)"

    def configure(self, p):
        p.add_argument("command", nargs="?")
        add_dut_args(p)

    def run(self, args):
        from ctt.platform import board
        prof = board_profile(args)
        if not args.command:
            print(f"board profile: {prof.describe()}")
            for k, v in sorted(prof.commands.items()):
                print(f"  {k:<16} {v}")
            if not prof.commands:
                print("  (no commands: add `commands:` to the board profile)")
            return 0
        print(board.run_command(dut_executor(args), prof, args.command))
        return 0


class BoardUnpairCommand(Command):
    name = "unpair"
    help = "remove a bond on the board (bluetoothctl, or the profile's bluetooth.unpair)"

    def configure(self, p):
        p.add_argument("addr")
        add_dut_args(p)

    def run(self, args):
        from ctt.platform import board
        print(board.unpair(dut_executor(args), board_profile(args), args.addr) or "removed")
        return 0


class BoardCommand(CommandGroup):
    name = "board"
    help = "board state and the board profile's commands (see ctt/platform/board.py)"
    dest = "board_cmd"

    def subcommands(self):
        return [BoardStatusCommand(), BoardCmdCommand(), BoardUnpairCommand()]
