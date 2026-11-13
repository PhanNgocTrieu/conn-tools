"""Non-interactive sub-commands: scan, paired, pair, unpair, run, suite, term."""

from __future__ import annotations

import argparse
import json

from ctt.btapp.expectations import (LEVELS, MEASURE, SuiteRunner, all_ok, evaluate, load_suite, outcome_line,
                                    summary_lines, write_report)
from ctt.btapp.formatting import board_event_line, interval_line, result_lines
from ctt.btapp.service import BtTestService, ThroughputParams
from ctt.btapp.settings import AppSettings
from ctt.protocol.links import LINK_LABELS, LINKS, ble_scan, capabilities, supported
from ctt.protocol.ctp import MODE_LABELS


def settings_from_args(args) -> AppSettings:
    """--board / --addr / --channel -> settings (no --board = agent already on the board)."""
    s = AppSettings(board=getattr(args, "board", None) or "", no_ssh=not getattr(args, "board", None),
                    bt_addr=getattr(args, "addr", None) or "")
    if getattr(args, "channel", None):
        s.channel = str(args.channel)
    s.link = getattr(args, "link", None) or "rfcomm"
    return s


def print_board_event(ev: dict) -> None:
    line = board_event_line(ev)
    if line:
        print(line)


def require_target(args) -> None:
    if not args.board and not args.addr:
        raise SystemExit("ctt-agent: give --board root@<ip> or --addr <board BT address>")


def cmd_run(args) -> int:
    require_target(args)
    svc = BtTestService(settings_from_args(args))
    params = ThroughputParams(args.mode, args.duration, args.block,
                              int(args.rate * 1e6) if args.rate else 0, not args.no_verify)
    try:
        r = svc.run_throughput(
            params, on_interval=lambda iv: print(interval_line(iv)), auto_deploy=True,
            on_board_event=print_board_event,
            on_connect=lambda t: print(f"{t.describe()}: "
                                       f"{MODE_LABELS[args.mode]} {args.duration:g}s block {args.block}"))
    finally:
        svc.close()
    checks = evaluate(r, {"up_mbps_min": args.min_up, "down_mbps_min": args.min_down,
                          "rtt_p99_ms_max": args.max_rtt_p99})
    ok = all_ok(checks)
    if args.json:
        print(json.dumps({**r.to_dict(), "checks": [c.__dict__ for c in checks],
                          "verdict": "PASS" if ok else "FAIL"}, indent=2))
    print()
    print("\n".join(result_lines(r)[:-1]))
    for c in checks:
        print(f"  [{'ok' if c.ok else 'FAIL'}] {c.text()}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_suite(args) -> int:
    suite = load_suite(args.file, args.link)
    if args.dump:
        print(suite.to_json())
        return 0
    if args.list:
        for c in suite.cases:
            exp = ", ".join(f"{k} {v}" for k, v in c.expect_for(args.level).items()) or "no limits"
            print(f"{c.id:<9} {c.mode:<6} block {c.block:<5} {c.duration_s:g}s  {c.title}   [{args.level}: {exp}]")
        return 0
    require_target(args)
    cases = suite.select(args.only.split(",") if args.only else None)
    svc = BtTestService(settings_from_args(args))
    runner = SuiteRunner(svc)
    print(f"suite {suite.name}, level {args.level}, {len(cases)} cases")
    try:
        outcomes = runner.run(
            cases, args.level,
            on_case=lambda i, c: print(f"\n[{i}/{len(cases)}] {c.id} {c.title} ({c.duration_s:g}s)"),
            on_interval=None if args.quiet else (lambda iv: print(interval_line(iv))),
            on_board_event=None if args.quiet else print_board_event,
            on_outcome=lambda o: print(outcome_line(o)),
            auto_deploy=True)
    except KeyboardInterrupt:
        runner.stop()
        raise
    finally:
        svc.close()
    print()
    print("\n".join(summary_lines(outcomes)))
    path = write_report(outcomes, suite, args.level, args.out)
    print(f"report: {path} (+ .csv)")
    if args.level == MEASURE:   # only measuring: fail only when a case could not run at all
        return 0 if outcomes and not any(o.error for o in outcomes) else 1
    return 0 if outcomes and all(o.ok for o in outcomes) else 1


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
        raise SystemExit(f"ctt-agent: {msg}")
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
    sp.add_argument("--link", choices=LINKS, default="rfcomm",
                    help="rfcomm (Classic), ble-gatt (BLE write/notify), ble-coc (BLE L2CAP CoC, Linux PC). "
                         "See: caps")


def cmd_caps(_args) -> int:
    """What this PC can run, and why not when it cannot."""
    print(f"{'link':<9} {'':<4} {'what':<30} detail")
    for c in capabilities():
        print(f"{c.link:<9} {'yes' if c.ok else 'no':<4} {LINK_LABELS[c.link]:<30} {c.why}")
    print("board side (ctt-agent): rfcomm, ble-gatt (server -t gatt), ble-coc (server -t l2cap -o type=le)")
    return 0


def cmd_ble_scan(args) -> int:
    cap = supported("ble-gatt")
    if not cap.ok:
        raise SystemExit(f"ctt-agent: BLE scan needs bleak: {cap.why}")
    devs = ble_scan(args.time, only_ctt=not args.all)
    for addr, name, rssi in devs:
        print(f"{addr}  {rssi if rssi is not None else '?':>4} dBm  {name}")
    if not devs:
        print("no BLE device found" + ("" if args.all else " with the CTT service (board: ctt-agent server -t gatt)"))
    return 0


def register(sub) -> None:
    r = sub.add_parser("run", help="throughput / latency test")
    add_link_args(r)
    r.add_argument("-m", "--mode", choices=("tx", "rx", "bidir", "echo"), default="tx")
    r.add_argument("-d", "--duration", type=float, default=10)
    r.add_argument("-b", "--block", type=int, default=990)
    r.add_argument("-r", "--rate", type=float, default=0, help="Mbit/s limit, 0 = none")
    r.add_argument("--no-verify", action="store_true")
    r.add_argument("--json", action="store_true")
    r.add_argument("--min-up", type=float, help="FAIL if PC->board Mbit/s is below this")
    r.add_argument("--min-down", type=float, help="FAIL if board->PC Mbit/s is below this")
    r.add_argument("--max-rtt-p99", type=float, help="FAIL if RTT p99 (ms, echo mode) is above this")
    r.set_defaults(func=cmd_run)
    u = sub.add_parser("suite", help="run the Bluetooth test cases with Mbit/s expectations")
    add_link_args(u)
    u.add_argument("-l", "--level", choices=LEVELS, default=MEASURE,
                   help="measure (default) = only record the numbers; baseline = regression floor of the "
                        "current setup, target = product goal (both PASS/FAIL on Mbit/s limits)")
    u.add_argument("--only", help="comma-separated case ids, e.g. BT-TP-01,BT-TP-04")
    u.add_argument("--file", help="suite JSON (default: built-in; see --dump)")
    u.add_argument("--out", default="ctt-results", help="folder for the JSON/CSV report")
    u.add_argument("--list", action="store_true", help="list the cases and expectations, run nothing")
    u.add_argument("--dump", action="store_true", help="print the suite as JSON (to edit and pass to --file)")
    u.add_argument("-q", "--quiet", action="store_true",
                   help="no live trace (by default: every second, sent / received so far on PC and board)")
    u.add_argument("-v", "--verbose", action="store_true", help=argparse.SUPPRESS)  # old flag, trace is default now
    u.set_defaults(func=cmd_suite)
    t = sub.add_parser("term", help="interactive raw data both ways")
    add_link_args(t)
    t.set_defaults(func=cmd_term)
    for name in ("scan", "paired"):
        s = sub.add_parser(name)
        s.add_argument("-t", "--time", type=float, default=10)
        s.set_defaults(func=cmd_pairing)
    s = sub.add_parser("ble-scan", help="BLE scan: boards running ctt-agent server -t gatt (--all: every device)")
    s.add_argument("-t", "--time", type=float, default=8)
    s.add_argument("--all", action="store_true")
    s.set_defaults(func=cmd_ble_scan)
    s = sub.add_parser("caps", help="which links (RFCOMM / BLE GATT / BLE CoC) this PC supports")
    s.set_defaults(func=cmd_caps)
    s = sub.add_parser("pair")
    s.add_argument("addr")
    s.add_argument("--board", help="SSH of the board, to accept the pairing there")
    s.set_defaults(func=cmd_pairing)
    s = sub.add_parser("unpair")
    s.add_argument("addr")
    s.set_defaults(func=cmd_pairing)
