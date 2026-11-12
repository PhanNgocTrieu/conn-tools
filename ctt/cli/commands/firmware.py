"""`ctt fw`: state / control of the vendor connectivity daemons on the DUT."""

from __future__ import annotations

import json

from ctt.cli.base import Command, CommandGroup
from ctt.cli.options import add_dut_args, dut_executor


class FwStatusCommand(Command):
    name = "status"
    help = "usb-daemon / bt-daemon / wifi-daemon state"

    def configure(self, p):
        p.add_argument("--json", action="store_true")
        add_dut_args(p)

    def run(self, args):
        from ctt.firmware import vendor_daemons as fw
        st = fw.status(dut_executor(args))
        print(json.dumps(st, indent=2) if args.json else fw.format_status(st))
        return 0 if st else 1


class FwWifiModeCommand(Command):
    name = "wifi-mode"
    help = "wifi-daemon SetMode"

    def configure(self, p):
        p.add_argument("mode", choices=("sta", "ap", "p2p"))
        add_dut_args(p)

    def run(self, args):
        from ctt.firmware import vendor_daemons as fw
        print(fw.wifi_set_mode(dut_executor(args), args.mode))
        return 0


class FwBtUnpairCommand(Command):
    name = "bt-unpair"
    help = "remove a bond through bt-daemon (frees one of its 5 slots)"

    def configure(self, p):
        p.add_argument("addr", help="address or BlueZ object path")
        add_dut_args(p)

    def run(self, args):
        from ctt.firmware import vendor_daemons as fw
        print(fw.vendor_call(dut_executor(args), "RemoveDevice", "s", args.addr))
        return 0


class FirmwareCommand(CommandGroup):
    name = "fw"
    help = "state / control of the vendor connectivity daemons on the DUT"
    dest = "fw_cmd"

    def subcommands(self):
        return [FwStatusCommand(), FwWifiModeCommand(), FwBtUnpairCommand()]
