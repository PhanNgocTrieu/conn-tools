"""ctt-bt: Bluetooth throughput test app.

    ctt-bt                       desktop window
    ctt-bt scan | paired         PC-side discovery
    ctt-bt pair ADDR [--board root@ip]     pair (board accepts over SSH)
    ctt-bt unpair ADDR
    ctt-bt run  [--board root@ip | --addr ADDR --channel N] -m tx|rx|bidir|echo -d 10
    ctt-bt term [--board root@ip | --addr ADDR --channel N]   interactive raw link
"""

from __future__ import annotations

import argparse
import json
import sys
import threading


def _target(args, pipe: bool = False, on_event=None):
    """(addr, channel, board, pipe_obj) from --board / --addr / --channel."""
    from ctt.btapp.board import SERVICE_CHANNEL, Board
    board = pipe_obj = None
    addr = (args.addr or "").upper()
    if args.board:
        board = Board(args.board)
        if not board.find_agent():
            board.deploy()
        if pipe:
            pipe_obj = board.start_pipe(args.channel or 0, on_event=on_event)
            b_addr, ch = pipe_obj.endpoint
        else:
            b_addr, ch = board.start_server(args.channel or 0)
        addr = addr or b_addr
    else:
        if not addr:
            raise SystemExit("ctt-bt: give --board root@<ip> or --addr <board BT address>")
        ch = args.channel or SERVICE_CHANNEL
    return addr, ch, board, pipe_obj


def cmd_run(args) -> int:
    from ctt.btapp.ctp_client import MODE_LABELS, CtpClient, open_rfcomm
    addr, ch, board, _ = _target(args)
    print(f"RFCOMM {addr} channel {ch}: {MODE_LABELS[args.mode]} {args.duration:g}s block {args.block}")
    sock = open_rfcomm(addr, ch)
    try:
        def show(iv):
            parts = [f"{iv.t:6.1f}s"]
            if iv.up_mbps is not None:
                parts.append(f"up {iv.up_mbps:7.3f}")
            if iv.down_mbps is not None:
                parts.append(f"down {iv.down_mbps:7.3f}")
            if iv.rtt_ms is not None:
                parts.append(f"rtt {iv.rtt_ms:7.2f} ms")
            print("  " + "  ".join(parts) + ("  Mbit/s" if iv.rtt_ms is None else ""))
        r = CtpClient(sock).run(args.mode, args.duration, args.block,
                                int(args.rate * 1e6) if args.rate else 0, not args.no_verify,
                                on_interval=show)
    finally:
        sock.close()
        if board:
            board.close()
    if args.json:
        print(json.dumps(r.to_dict(), indent=2))
    f = lambda v: "-" if v is None else f"{v:.3f}"  # noqa: E731
    print(f"\nPC -> board {f(r.up_mbps)} Mbit/s   board -> PC {f(r.down_mbps)} Mbit/s   "
          f"lost {r.lost}  crc {r.crc_errors}")
    if r.rtt_ms:
        print("RTT ms: " + "  ".join(f"{k} {v:.2f}" for k, v in r.rtt_ms.items() if k != "count")
              + f"  ({r.rtt_ms['count']})")
    ok = r.lost == 0 and r.crc_errors == 0
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_term(args) -> int:
    from ctt.btapp.ctp_client import open_rfcomm

    def board_event(ev):
        if ev.get("event") == "rx":
            print(f"\r[board nhận] {ev['data']!r}\n> ", end="", flush=True)

    addr, ch, board, pipe = _target(args, pipe=True, on_event=board_event)
    sock = open_rfcomm(addr, ch)
    print(f"connected {addr} channel {ch}. Gõ dòng rồi Enter để PC gửi; "
          f"'!text' để board gửi về PC (cần --board); Ctrl+D để thoát.")

    def reader():
        while True:
            try:
                d = sock.recv(4096)
            except OSError:
                break
            if not d:
                break
            print(f"\r[PC nhận]    {d!r}\n> ", end="", flush=True)
        print("\r[link closed]")

    threading.Thread(target=reader, daemon=True).start()
    try:
        while True:
            line = input("> ")
            if line.startswith("!"):
                if not pipe:
                    print("board send needs --board")
                    continue
                pipe.send((line[1:] + "\n").encode())
            else:
                sock.sendall((line + "\n").encode())
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        sock.close()
        if pipe:
            pipe.close()
    return 0


def cmd_pairing(args) -> int:
    from ctt.btapp import pairing
    be = pairing.backend()
    ok, msg = be.available()
    if not ok:
        raise SystemExit(f"ctt-bt: {msg}")
    if args.cmd in ("scan", "paired"):
        devs = be.scan(args.time) if args.cmd == "scan" else be.paired()
        for d in devs:
            print(f"{d.address}  {'[paired] ' if d.paired else ''}{d.name}")
        return 0
    if args.cmd == "unpair":
        ok, msg = be.unpair(args.addr.upper())
        print(msg)
        return 0 if ok else 1
    acc = None
    if args.board:
        from ctt.btapp.board import Board
        acc = Board(args.board).acceptor()
        acc.__enter__()
    try:
        ok, msg = be.pair(args.addr.upper(), on_pin=lambda p: print(f"passkey {p}"))
    finally:
        if acc:
            acc.close()
    print(msg)
    return 0 if ok else 1


def main(argv=None) -> int:
    from ctt import __version__
    p = argparse.ArgumentParser(prog="ctt-bt", description="CTT Bluetooth throughput test")
    p.add_argument("--version", action="version", version=f"ctt-bt {__version__}")
    sub = p.add_subparsers(dest="cmd")

    def link(sp):
        sp.add_argument("--board", help="SSH of the board (root@ip): starts the agent there")
        sp.add_argument("--addr", help="board Bluetooth address (default: read over SSH)")
        sp.add_argument("--channel", type=int, default=None, help="RFCOMM channel")

    r = sub.add_parser("run", help="throughput / latency test")
    link(r)
    r.add_argument("-m", "--mode", choices=("tx", "rx", "bidir", "echo"), default="tx")
    r.add_argument("-d", "--duration", type=float, default=10)
    r.add_argument("-b", "--block", type=int, default=990)
    r.add_argument("-r", "--rate", type=float, default=0, help="Mbit/s limit, 0 = none")
    r.add_argument("--no-verify", action="store_true")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_run)
    t = sub.add_parser("term", help="interactive raw data both ways")
    link(t)
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
    sub.add_parser("gui", help="desktop window (default)")

    args = p.parse_args(argv)
    if args.cmd in (None, "gui"):
        from ctt.btapp.gui import main as gui_main
        return gui_main()
    try:
        return args.func(args)
    except (RuntimeError, OSError) as e:
        print(f"ctt-bt: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
