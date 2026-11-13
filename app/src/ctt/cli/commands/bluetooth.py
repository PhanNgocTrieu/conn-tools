"""`ctt bt`: Bluetooth controllers and pairing through bluetoothctl."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import time
from pathlib import Path

from ctt.cli.base import Command, CommandGroup
from ctt.cli.options import add_dut_args, add_out_args, dut_executor, warn
from ctt.core.executor import LocalExecutor


def open_ctl(args, side: str, **kw):
    """bluetoothctl session on the host or on the DUT."""
    from ctt.bluetooth.bluetoothctl import BluetoothCtl
    ex = LocalExecutor() if side == "host" else dut_executor(args)
    if side == "dut" and isinstance(ex, LocalExecutor):
        raise SystemExit("ctt bt: --side dut needs --dut user@host")
    cmd = shlex.split(args.btctl) if args.btctl else None
    ctl = BluetoothCtl(ex, label=f"bt@{side}", command=cmd, **kw)
    ctl.wait_ready()
    return ctl


class BtSubcommand(Command):
    """Shared arguments: DUT connection, --side, optional peer address."""

    side_default = "host"
    addr: str | None = None   # None | "required" | "optional"

    def configure(self, p):
        add_dut_args(p)
        p.add_argument("--side", choices=("host", "dut"), default=self.side_default,
                       help=f"where to run bluetoothctl (default {self.side_default})")
        p.add_argument("--btctl", default=os.environ.get("CTT_BLUETOOTHCTL"),
                       help=argparse.SUPPRESS)  # alternate bluetoothctl command (tests)
        if self.addr == "required":
            p.add_argument("addr", help="peer address AA:BB:CC:DD:EE:FF")
        elif self.addr == "optional":
            p.add_argument("addr", nargs="?", default=None,
                           help="peer address (default: the DUT's controller, read via --dut)")


class BtListCommand(BtSubcommand):
    name = "list"
    help = "list controllers"

    def run(self, args):
        with open_ctl(args, args.side) as ctl:
            for c in ctl.controllers():
                print(f"{c['address']}  {c['name']}{'  [default]' if c['default'] else ''}")
        return 0


class BtScanCommand(BtSubcommand):
    name = "scan"
    help = "discover nearby devices"

    def configure(self, p):
        super().configure(p)
        p.add_argument("-t", "--time", type=float, default=10)

    def run(self, args):
        with open_ctl(args, args.side) as ctl:
            ctl.set("power", True)
            for addr, name in sorted(ctl.scan(args.time).items()):
                print(f"{addr}  {name}")
        return 0


class BtInfoCommand(BtSubcommand):
    name = "info"
    help = "device properties"
    addr = "required"

    def run(self, args):
        with open_ctl(args, args.side) as ctl:
            info = ctl.info(args.addr)
        if info is None:
            print(f"{args.addr}: not known on {args.side}")
            return 1
        for k, v in info.items():
            print(f"{k:>16}: {', '.join(v) if isinstance(v, list) else v}")
        return 0


class BtUnpairCommand(BtSubcommand):
    name = "unpair"
    help = "remove a paired device"
    addr = "required"

    def run(self, args):
        with open_ctl(args, args.side) as ctl:
            print("removed" if ctl.remove(args.addr) else "not paired / unknown")
        return 0


class BtPrepareCommand(BtSubcommand):
    name = "prepare"
    help = "power on + pairable + discoverable + agent"
    side_default = "dut"

    def configure(self, p):
        from ctt.bluetooth.bluetoothctl import CAPABILITIES
        super().configure(p)
        p.add_argument("--capability", choices=CAPABILITIES, default="NoInputNoOutput")
        p.add_argument("--no-discoverable", action="store_true")
        p.add_argument("--hold", type=float, default=0,
                       help="keep the agent alive N seconds to accept incoming pairing")

    def run(self, args):
        from ctt.bluetooth.bluetoothctl import prepare_acceptor
        with open_ctl(args, args.side) as ctl:
            prepare_acceptor(ctl, args.capability, discoverable=not args.no_discoverable)
            print(f"{args.side}: powered, pairable"
                  f"{'' if args.no_discoverable else ', discoverable'}, agent {args.capability}")
            if args.hold:
                print(f"holding the agent for {args.hold:g}s (auto-accepting pairing) ...")
                time.sleep(args.hold)
        return 0


class DutPairingAcceptor:
    """DUT side of `--dut-accept`: the board profile's pairing commands or a bluetoothctl agent."""

    def __init__(self, args):
        from ctt.cli.options import board_profile
        from ctt.platform import board
        self.args = args
        self.dut_ex = dut_executor(args)
        self.profile = board_profile(args)
        self.mode = args.dut_accept
        self.acc = None
        self.ctl = None
        if self.mode == "auto":
            self.mode = "profile" if self.profile.pairing.custom else "bluetoothctl"
            if not isinstance(self.dut_ex, LocalExecutor):
                for w in board.warnings_for(board.status(self.dut_ex, self.profile), set(), True, self.profile):
                    warn(w)
        if self.mode == "profile" and not self.profile.pairing.custom:
            raise SystemExit(f"--dut-accept profile: no bluetooth.pairing in the board profile "
                             f"({self.profile.describe()})")

    def peer_from_manager(self) -> str | None:
        """The DUT address from the board profile's bluetooth.address command, if any."""
        if not self.profile.address_cmd:
            return None
        r = self.dut_ex.run(["sh", "-c", self.profile.address_cmd], timeout=15)
        addr = r.stdout.strip().splitlines()[0].upper() if r.returncode == 0 and r.stdout.strip() else ""
        return addr or None

    def start(self) -> None:
        if self.mode == "profile":
            from ctt.platform.board import ProfileAcceptor
            self.acc = ProfileAcceptor(self.dut_ex, self.profile.pairing).__enter__()
            print(f"DUT: pairing accepted with the board profile's commands ({self.profile.name})")
        else:
            from ctt.bluetooth.bluetoothctl import prepare_acceptor
            self.ctl = open_ctl(self.args, "dut")
            prepare_acceptor(self.ctl, "NoInputNoOutput")
            print("DUT: agent registered, pairable + discoverable")

    def close(self) -> None:
        if self.ctl:
            self.ctl.close()
        if self.acc:
            self.acc.close()
            print(f"DUT: confirmed {self.acc.confirmed} pairing request(s)")


class BtPairCommand(BtSubcommand):
    name = "pair"
    help = "pair host -> peer (default peer: the DUT)"
    addr = "optional"
    stress = False

    def configure(self, p):
        from ctt.bluetooth.bluetoothctl import CAPABILITIES
        super().configure(p)
        p.add_argument("--capability", choices=CAPABILITIES, default="NoInputNoOutput",
                       help="host agent IO capability")
        p.add_argument("--pin", default="0000", help="legacy PIN to answer with")
        p.add_argument("--passkey", default=None, help="passkey to answer with")
        p.add_argument("--no-trust", action="store_true")
        p.add_argument("--dut-accept", nargs="?", const="auto", default=None,
                       choices=("auto", "profile", "bluetoothctl"),
                       help="make the DUT accept the pairing: profile = the board profile's "
                            "bluetooth.pairing commands, bluetoothctl = own BlueZ agent; "
                            "auto (default) picks profile when the board profile defines it")
        p.add_argument("--scan-timeout", type=float, default=20)
        p.add_argument("--timeout", type=float, default=40, help="pairing timeout")

    def _peer(self, args) -> str:
        """Peer address for host-side operations: explicit, or the DUT's controller."""
        from ctt.bluetooth.bluetoothctl import default_controller
        if args.addr:
            return args.addr.upper()
        with open_ctl(args, "dut") as dut:
            addr = default_controller(dut)
        print(f"DUT controller: {addr}")
        return addr

    def run(self, args):
        acceptor = DutPairingAcceptor(args) if args.dut_accept else None
        if acceptor and not args.addr:
            addr = acceptor.peer_from_manager()
            if addr:
                args.addr = addr
                print(f"DUT controller (board profile): {args.addr}")
        peer = self._peer(args)
        try:
            if acceptor:
                acceptor.start()
            with open_ctl(args, "host", pin=args.pin, passkey=args.passkey) as host:
                host.register_agent(args.capability)
                host.set("power", True)
                return self.pair(args, host, peer)
        finally:
            if acceptor:
                acceptor.close()

    def pair(self, args, host, peer: str) -> int:
        from ctt.bluetooth.bluetoothctl import pair_once
        a = pair_once(host, peer, args.scan_timeout, args.timeout, trust=not args.no_trust)
        print(f"{'OK  ' if a.ok else 'FAIL'} pair {peer}: {a.message} "
              f"(discover {a.discover_s:.1f}s, pair {a.pair_s:.1f}s)")
        if host.prompts:
            print("  prompts answered: " + "; ".join(host.prompts))
        return 0 if a.ok else 1


class BtPairStressCommand(BtPairCommand):
    name = "pair-stress"
    help = "repeat remove/discover/pair N times"

    def configure(self, p):
        super().configure(p)
        p.add_argument("-n", "--count", type=int, default=10)
        p.add_argument("--connect", action="store_true", help="also connect after pairing")
        p.add_argument("--min-success-rate", type=float, default=None,
                       help="pass threshold 0..1 (default: every attempt must pass)")
        add_out_args(p)

    def pair(self, args, host, peer: str) -> int:
        from ctt.bluetooth.bluetoothctl import pair_stress

        def show(a):
            conn = "" if a.connect_ok is None else f" connect={'ok' if a.connect_ok else 'FAIL'}"
            print(f"  [{a.index:3d}/{args.count}] {'OK  ' if a.ok else 'FAIL'} "
                  f"pair {a.pair_s:5.2f}s discover {a.discover_s:5.2f}s{conn}  {a.message}")

        print(f"pair stress: {peer} x{args.count}")
        res = pair_stress(host, peer, args.count, args.scan_timeout, args.timeout,
                          connect=args.connect, on_attempt=show)
        summ = res.summary()
        print(f"\nsuccess {summ['success']}/{summ['attempts']} "
              f"({summ['success_rate'] * 100:.1f}%)", end="")
        if "pair_s_avg" in summ:
            print(f"  pair time min/avg/max {summ['pair_s_min']:.2f}/"
                  f"{summ['pair_s_avg']:.2f}/{summ['pair_s_max']:.2f} s")
        else:
            print()
        out = Path(args.out) / f"{time.strftime('%Y%m%d-%H%M%S')}_bt-pair-stress"
        out.mkdir(parents=True, exist_ok=True)
        (out / "result.json").write_text(json.dumps(res.to_dict(), indent=2))
        print(f"report: {out}")
        ok = res.success == len(res.attempts)
        if args.min_success_rate is not None:
            ok = summ["success_rate"] >= args.min_success_rate
        return 0 if ok else 1


class BluetoothCommand(CommandGroup):
    name = "bt"
    help = "Bluetooth controller / pairing (via bluetoothctl)"
    dest = "bt_cmd"

    def subcommands(self):
        return [BtListCommand(), BtScanCommand(), BtInfoCommand(), BtUnpairCommand(),
                BtPrepareCommand(), BtPairCommand(), BtPairStressCommand()]


__all__ = ["BluetoothCommand"]
