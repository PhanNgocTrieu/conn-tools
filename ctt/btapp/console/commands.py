"""Non-interactive sub-commands: scan, paired, pair, unpair, run, term."""

from __future__ import annotations

import json

from ctt.btapp.formatting import interval_line, passed, result_lines
from ctt.btapp.service import BtTestService, ThroughputParams
from ctt.btapp.settings import AppSettings
from ctt.protocol.ctp import MODE_LABELS


def settings_from_args(args) -> AppSettings:
    """--board / --addr / --channel -> settings (no --board = agent already on the board)."""
    s = AppSettings(board=getattr(args, "board", None) or "", no_ssh=not getattr(args, "board", None),
                    bt_addr=getattr(args, "addr", None) or "")
    if getattr(args, "channel", None):
        s.channel = str(args.channel)
    return s


def require_target(args) -> None:
    if not args.board and not args.addr:
        raise SystemExit("ctt-bt: give --board root@<ip> or --addr <board BT address>")


def cmd_run(args) -> int:
    require_target(args)
    svc = BtTestService(settings_from_args(args))
    params = ThroughputParams(args.mode, args.duration, args.block,
                              int(args.rate * 1e6) if args.rate else 0, not args.no_verify)
    try:
        r = svc.run_throughput(
            params, on_interval=lambda iv: print(interval_line(iv)), auto_deploy=True,
            on_connect=lambda t: print(f"RFCOMM {t.address} channel {t.channel}: "
                                       f"{MODE_LABELS[args.mode]} {args.duration:g}s block {args.block}"))
    finally:
        svc.close()
    if args.json:
        print(json.dumps(r.to_dict(), indent=2))
    print()
    print("\n".join(result_lines(r)))
    return 0 if passed(r) else 1


def cmd_term(args) -> int:
    from ctt.btapp.console.terminal import ConsoleTerminal
    require_target(args)
    svc = BtTestService(settings_from_args(args))
    try:
        return ConsoleTerminal(svc, auto_deploy=True).run()
    finally:
        svc.close()


def cmd_pairing(args) -> int:
    svc = BtTestService(AppSettings(board=getattr(args, "board", None) or "",
                                    no_ssh=not getattr(args, "board", None)))
    ok, msg = svc.pairing_status()
    if not ok:
        raise SystemExit(f"ctt-bt: {msg}")
    if args.cmd in ("scan", "paired"):
        devs = svc.scan(args.time) if args.cmd == "scan" else svc.paired()
        for d in devs:
            print(f"{d.address}  {'[paired] ' if d.paired else ''}{d.name}")
        return 0
    if args.cmd == "unpair":
        ok, msg = svc.unpair(args.addr)
        print(msg)
        return 0 if ok else 1
    ok, msg = svc.pair(args.addr, accept_on_board=bool(args.board),
                       on_pin=lambda p: print(f"passkey {p}"))
    print(msg)
    return 0 if ok else 1


def add_link_args(sp) -> None:
    sp.add_argument("--board", help="SSH of the board (root@ip): starts the agent there")
    sp.add_argument("--addr", help="board Bluetooth address (default: read over SSH)")
    sp.add_argument("--channel", type=int, default=None, help="RFCOMM channel")


def register(sub) -> None:
    r = sub.add_parser("run", help="throughput / latency test")
    add_link_args(r)
    r.add_argument("-m", "--mode", choices=("tx", "rx", "bidir", "echo"), default="tx")
    r.add_argument("-d", "--duration", type=float, default=10)
    r.add_argument("-b", "--block", type=int, default=990)
    r.add_argument("-r", "--rate", type=float, default=0, help="Mbit/s limit, 0 = none")
    r.add_argument("--no-verify", action="store_true")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_run)
    t = sub.add_parser("term", help="interactive raw data both ways")
    add_link_args(t)
    t.set_defaults(func=cmd_term)
    for name in ("scan", "paired"):
        s = sub.add_parser(name)
        s.add_argument("-t", "--time", type=float, default=10)
        s.set_defaults(func=cmd_pairing)
    s = sub.add_parser("pair")
    s.add_argument("addr")
    s.add_argument("--board", help="SSH of the board, to accept the pairing there")
    s.set_defaults(func=cmd_pairing)
    s = sub.add_parser("unpair")
    s.add_argument("addr")
    s.set_defaults(func=cmd_pairing)
