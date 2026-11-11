"""`ctt` command line."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ctt import __version__
from ctt.agent import DEFAULT_DUT_AGENT, REPO_ROOT, find_host_agent
from ctt.executor import LocalExecutor, SshExecutor, make_executor
from ctt.report import (VERDICT_COLOR, ReportWriter, color, print_live_event, print_result,
                        print_summary)
from ctt.runner import MODES, Runner, TestSpec

DUT_INFO_SCRIPT = r"""
section() { echo; echo "### $1"; }
section system;     uname -a; cat /etc/os-release 2>/dev/null | head -4; uptime
section cpu;        grep -m1 -iE 'model name|Hardware|Processor' /proc/cpuinfo; nproc 2>/dev/null
section memory;     free -m 2>/dev/null || head -3 /proc/meminfo
section network;    (ip -br addr 2>/dev/null || ifconfig -a) | head -30
section wifi;       (iw dev 2>/dev/null; for i in $(ls /sys/class/net); do [ -d /sys/class/net/$i/wireless ] && iw dev $i link 2>/dev/null; done) | head -30
section bluetooth;  (hciconfig -a 2>/dev/null || btmgmt info 2>/dev/null || ls /sys/class/bluetooth) | head -30
section bt-kernel;  grep -E 'bluetooth|rfcomm|l2cap|btusb|hci_uart' /proc/modules 2>/dev/null; ls /proc/net/rfcomm* 2>/dev/null; (command -v bluetoothd || ls /usr/libexec/bluetooth/bluetoothd) 2>/dev/null
section usb;        (lsusb 2>/dev/null || ls /sys/bus/usb/devices) | head -30; echo "UDC (device/gadget controllers):"; ls /sys/class/udc 2>/dev/null; for r in /sys/class/usb_role/*/role /sys/bus/platform/devices/*/dwc2_drdmode; do [ -f "$r" ] && echo "$r: $(cat $r)"; done
section i2c;        ls /dev/i2c-* 2>/dev/null; (command -v i2cdetect >/dev/null && echo 'i2c-tools: present' || echo 'i2c-tools: missing')
section tools;      for t in busctl iperf3 bluetoothctl btmgmt sdptool rfcomm iw wpa_cli hostapd i2cdetect python3 gdbserver; do printf '%-14s %s\n' $t "$(command -v $t || echo -)"; done
section ctt-agent;  for p in /tmp/ctt-agent /usr/bin/ctt-agent; do [ -x $p ] && $p version; done
"""


def _add_dut_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("DUT connection")
    g.add_argument("--dut", default=os.environ.get("CTT_DUT", "local"),
                   help="[user@]host of the DUT reached by SSH, or 'local' to run both sides "
                        "on this machine (default: $CTT_DUT or local)")
    g.add_argument("--ssh-port", type=int, default=None)
    g.add_argument("--ssh-opt", action="append", default=[],
                   help="extra ssh -o option, repeatable (e.g. StrictHostKeyChecking=no)")
    g.add_argument("--dut-agent", default=None,
                   help=f"ctt-agent path on the DUT (default {DEFAULT_DUT_AGENT})")
    g.add_argument("--host-agent", default=None, help="ctt-agent path on this machine")


def _add_test_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("test")
    g.add_argument("-t", "--transport", default="tcp")
    g.add_argument("-m", "--mode", choices=MODES, default="tx",
                   help="tx: host->DUT, rx: DUT->host, bidir, echo (latency) - when the "
                        "server runs on the DUT")
    g.add_argument("-d", "--duration", default="10s")
    g.add_argument("-b", "--block", default=None, help="payload per frame, e.g. 990, 64K")
    g.add_argument("-r", "--rate", default=None, help="sender rate limit, e.g. 2M (bits/s)")
    g.add_argument("--no-verify", action="store_true", help="disable payload CRC checks")
    g.add_argument("-n", "--count", type=int, default=None, help="echo: number of round trips")
    g.add_argument("--echo-interval", type=float, default=None, dest="echo_interval_ms",
                   help="echo: ms between requests (0 = back-to-back)")
    g.add_argument("-i", "--interval", default="1s", help="report interval")
    g.add_argument("--port", type=lambda v: int(v, 0), default=None,
                   help="tcp port (5201) / RFCOMM channel (0 = auto) / L2CAP PSM")
    g.add_argument("--bdaddr", default=None,
                   help="BT address of the server side, if the agent cannot detect it")
    g.add_argument("-e", "--endpoint", default=None, help="explicit client target")
    g.add_argument("--listen", default=None, help="explicit server bind endpoint")
    g.add_argument("--server-side", choices=("dut", "host"), default="dut",
                   help="which side runs the agent server (default dut)")
    g.add_argument("--sndbuf", default=None)
    g.add_argument("--rcvbuf", default=None)
    g.add_argument("--nodelay", action="store_true")
    g.add_argument("--wifi", nargs="?", const="auto", default=None, dest="wifi_iface",
                   metavar="IFACE", help="record the DUT's WiFi link (signal, bitrate) "
                                         "before/after; IFACE default: auto")
    g.add_argument("--timeout", default=None,
                   help="agent idle I/O timeout, e.g. 30s (default 10s)")
    g.add_argument("-o", "--opt", action="append", default=[], metavar="KEY=VALUE",
                   help="transport option, repeatable")
    g.add_argument("--expect", action="append", default=[], metavar="KEY=VALUE",
                   help="pass criterion, e.g. up_mbps_min=1.5, lost_max=0")
    g.add_argument("--name", default=None)


def _kv(items: list[str], what: str) -> dict:
    out = {}
    for it in items:
        if "=" not in it:
            raise SystemExit(f"ctt: {what} must be KEY=VALUE, got {it!r}")
        k, v = it.split("=", 1)
        out[k] = v
    return out


def _add_out_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", default=str(REPO_ROOT / "out"), help="report directory root")
    p.add_argument("-q", "--quiet", action="store_true", help="no per-interval output")


def _make_runner(args) -> tuple[Runner, object]:
    dut = make_executor(args.dut, args.ssh_port, args.ssh_opt)
    host = LocalExecutor()
    host_agent = args.host_agent or find_host_agent()
    if isinstance(dut, LocalExecutor):
        dut_agent = args.dut_agent or host_agent
    else:
        dut_agent = args.dut_agent or DEFAULT_DUT_AGENT
    quiet = getattr(args, "quiet", False)
    runner = Runner(dut, host, dut_agent, host_agent,
                    on_event=lambda side, ev: print_live_event(side, ev, quiet))
    return runner, dut


def _fw_preflight(dut, transports: set[str], bt_pairing: bool = False) -> None:
    """Warns when a test would collide with the vendor firmware daemons on the DUT."""
    if isinstance(dut, LocalExecutor) or os.environ.get("CTT_NO_FW_CHECK"):
        return
    from ctt import fw
    for w in fw.warnings_for(fw.status(dut), transports, bt_pairing):
        print(color(f"warning: {w}", "33"), file=sys.stderr)


def _run_specs(args, run_name: str, specs: list[TestSpec]) -> int:
    runner, dut = _make_runner(args)
    _fw_preflight(dut, {s.transport for s in specs})
    report = ReportWriter(args.out, run_name)
    results = []
    for i, spec in enumerate(specs, 1):
        print(f"\n[{i}/{len(specs)}] {spec.name}: {spec.transport} {spec.mode} "
              f"{spec.duration:g}s (server on {spec.server_side}, DUT={dut.name})")
        r = runner.run(spec)
        print_result(r)
        report.add(r)
        results.append(r)
    report.finish({"run": run_name, "dut": dut.name, "ctt_version": __version__})
    print_summary(results, report.dir)
    return 0 if all(r.verdict == "PASS" for r in results) else 1


# ---- commands -----------------------------------------------------------------

def cmd_run(args) -> int:
    d = {
        "name": args.name or f"{args.transport}-{args.mode}",
        "transport": args.transport, "mode": args.mode, "duration": args.duration,
        "block": args.block, "rate": args.rate, "verify": not args.no_verify,
        "count": args.count, "echo_interval_ms": args.echo_interval_ms,
        "interval": args.interval, "port": args.port, "bdaddr": args.bdaddr,
        "endpoint": args.endpoint,
        "listen": args.listen, "server_side": args.server_side, "sndbuf": args.sndbuf,
        "rcvbuf": args.rcvbuf, "nodelay": args.nodelay, "timeout": args.timeout,
        "wifi_iface": args.wifi_iface,
        "transport_opts": _kv(args.opt, "--opt"),
        "expect": {k: float(v) for k, v in _kv(args.expect, "--expect").items()},
    }
    spec = TestSpec.from_dict(d)
    return _run_specs(args, spec.name, [spec])


def cmd_profile(args) -> int:
    from ctt.profile import load_profile
    overrides = {}
    if args.duration:
        overrides["duration"] = args.duration
    name, specs = load_profile(args.profile, overrides)
    if args.only:
        specs = [s for s in specs if any(o in s.name for o in args.only)]
    if args.list:
        for s in specs:
            print(f"{s.name:<40} {s.transport:<8} {s.mode:<6} {s.duration:g}s "
                  f"block={s.block or 'default'} expect={s.expect or '-'}")
        return 0
    return _run_specs(args, name, specs)


def cmd_selftest(args) -> int:
    args.dut = "local"
    specs = [TestSpec.from_dict({"name": f"selftest-{m}", "mode": m, "duration": args.duration,
                                 "port": args.port, "interval": 0,
                                 "expect": {"lost_max": 0}})
             for m in MODES]
    return _run_specs(args, "selftest", specs)


def cmd_deploy(args) -> int:
    dut = make_executor(args.dut, args.ssh_port, args.ssh_opt)
    if not isinstance(dut, SshExecutor):
        raise SystemExit("ctt deploy: --dut user@host is required")
    binary = Path(args.binary or REPO_ROOT / "build" / "dut" / "agent" / "ctt-agent")
    if not binary.is_file():
        raise SystemExit(f"ctt deploy: {binary} not found - build it with scripts/build-dut.sh")
    remote = args.dut_agent or DEFAULT_DUT_AGENT
    print(f"copying {binary} -> {dut.target}:{remote}")
    dut.put(str(binary), remote + ".new")
    r = dut.run(f"chmod +x {remote}.new && mv -f {remote}.new {remote} && {remote} version")
    if r.returncode != 0:
        print(r.stdout + r.stderr, file=sys.stderr)
        raise SystemExit("ctt deploy: agent does not run on the DUT (wrong architecture or "
                         "missing libraries? try a static build: scripts/build-dut.sh --static)")
    print(r.stdout.strip())
    return 0


def cmd_dut_info(args) -> int:
    from ctt import fw
    dut = make_executor(args.dut, args.ssh_port, args.ssh_opt)
    r = dut.run(["sh", "-c", DUT_INFO_SCRIPT], timeout=60)
    print(r.stdout)
    st = fw.status(dut)
    if fw.has_vendor_firmware(st):
        print("\n### firmware (vendor connectivity daemons)")
        print(fw.format_status(st))
    if r.returncode != 0 and r.stderr.strip():
        print(r.stderr, file=sys.stderr)
    return r.returncode


# ---- mfi ------------------------------------------------------------------------

def cmd_mfi(args) -> int:
    from ctt import mfi
    cfg = mfi.load_config(args.config)
    tests = cfg.tests
    if args.only:
        tests = [t for t in tests if any(o in t.id or o in t.name for o in args.only)]
    dut = make_executor(args.dut, args.ssh_port, args.ssh_opt)
    if isinstance(dut, LocalExecutor):
        agent = args.dut_agent or args.host_agent or find_host_agent()
    else:
        agent = args.dut_agent or DEFAULT_DUT_AGENT
    report = ReportWriter(args.out, f"mfi-{Path(args.config).stem}")
    results = []
    for i, t in enumerate(tests, 1):
        print(f"\n[{i}/{len(tests)}] {t.id} {t.name}: {len(t.steps)} step(s) x{t.repeat} "
              f"({mfi.describe(cfg)}, DUT={dut.name})")
        r = mfi.run_test(dut, agent, cfg, t, args.repeat_scale)
        results.append(r)
        print(f"  => {color(r.verdict, VERDICT_COLOR[r.verdict])}  ({r.elapsed_s:.1f}s)")
        if r.result and not args.quiet:
            print(mfi.format_steps(r.result))
        for c in r.checks:
            if not c["ok"]:
                print(f"     check {c['check']}: {c['value']} not {c['op']} {c['limit']}")
        if r.error:
            print(f"     error: {r.error}")
        report.add_record(f"{t.id}_{t.name}", r.row(), r.to_dict())
    report.finish({"run": "mfi", "config": str(args.config), "dut": dut.name,
                   "ctt_version": __version__})
    passed = sum(r.verdict == "PASS" for r in results)
    print(f"\n{passed}/{len(results)} passed\nreport: {report.dir}  (report.html, junit.xml)")
    return 0 if passed == len(results) else 1


# ---- bluetooth ------------------------------------------------------------------

def _bt_ctl(args, side: str, **kw):
    import shlex
    from ctt.bt import BluetoothCtl
    ex = LocalExecutor() if side == "host" else make_executor(args.dut, args.ssh_port,
                                                              args.ssh_opt)
    if side == "dut" and isinstance(ex, LocalExecutor):
        raise SystemExit("ctt bt: --side dut needs --dut user@host")
    cmd = shlex.split(args.btctl) if args.btctl else None
    ctl = BluetoothCtl(ex, label=f"bt@{side}", command=cmd, **kw)
    ctl.wait_ready()
    return ctl


def _bt_peer(args) -> str:
    """Peer address for host-side operations: explicit, or the DUT's controller."""
    from ctt.bt import default_controller
    if args.addr:
        return args.addr.upper()
    with _bt_ctl(args, "dut") as dut:
        addr = default_controller(dut)
    print(f"DUT controller: {addr}")
    return addr


def cmd_bt(args) -> int:
    import json
    import time
    from ctt.bt import pair_once, pair_stress, prepare_acceptor
    act = args.bt_cmd

    if act == "list":
        with _bt_ctl(args, args.side) as ctl:
            for c in ctl.controllers():
                print(f"{c['address']}  {c['name']}{'  [default]' if c['default'] else ''}")
        return 0
    if act == "scan":
        with _bt_ctl(args, args.side) as ctl:
            ctl.set("power", True)
            for addr, name in sorted(ctl.scan(args.time).items()):
                print(f"{addr}  {name}")
        return 0
    if act == "info":
        with _bt_ctl(args, args.side) as ctl:
            info = ctl.info(args.addr)
        if info is None:
            print(f"{args.addr}: not known on {args.side}")
            return 1
        for k, v in info.items():
            print(f"{k:>16}: {', '.join(v) if isinstance(v, list) else v}")
        return 0
    if act == "unpair":
        with _bt_ctl(args, args.side) as ctl:
            print("removed" if ctl.remove(args.addr) else "not paired / unknown")
        return 0
    if act == "prepare":
        with _bt_ctl(args, args.side) as ctl:
            prepare_acceptor(ctl, args.capability, discoverable=not args.no_discoverable)
            print(f"{args.side}: powered, pairable"
                  f"{'' if args.no_discoverable else ', discoverable'}, agent {args.capability}")
            if args.hold:
                print(f"holding the agent for {args.hold:g}s (auto-accepting pairing) ...")
                time.sleep(args.hold)
        return 0

    # pair / pair-stress: host initiates, DUT optionally auto-accepts
    dut_ctl = None
    bm = None
    if args.dut_accept:
        from ctt import fw
        dut_ex = make_executor(args.dut, args.ssh_port, args.ssh_opt)
        mode = args.dut_accept
        if mode == "auto":
            st = fw.status(dut_ex) if not isinstance(dut_ex, LocalExecutor) else {}
            mode = "bm" if fw.active(st, "bt-daemon") else "bluetoothctl"
            for w in fw.warnings_for(st, set(), bt_pairing=True):
                if "default BlueZ agent" not in w:
                    print(color(f"warning: {w}", "33"), file=sys.stderr)
        if mode == "bm" and not args.addr:
            addr = fw.vendor_call(dut_ex, "GetLocalAddress").split('"')
            if len(addr) >= 4:
                args.addr = addr[3]
                print(f"DUT controller (bt-daemon): {args.addr}")
    peer = _bt_peer(args)
    try:
        if args.dut_accept and mode == "bm":
            bm = fw.VendorAcceptor(dut_ex).__enter__()
            print("DUT: bt-daemon pairable + discoverable, confirming pairing requests")
        elif args.dut_accept:
            dut_ctl = _bt_ctl(args, "dut")
            prepare_acceptor(dut_ctl, "NoInputNoOutput")
            print("DUT: agent registered, pairable + discoverable")
        with _bt_ctl(args, "host", pin=args.pin, passkey=args.passkey) as host:
            host.register_agent(args.capability)
            host.set("power", True)
            if act == "pair":
                a = pair_once(host, peer, args.scan_timeout, args.timeout, trust=not args.no_trust)
                print(f"{'OK  ' if a.ok else 'FAIL'} pair {peer}: {a.message} "
                      f"(discover {a.discover_s:.1f}s, pair {a.pair_s:.1f}s)")
                if host.prompts:
                    print("  prompts answered: " + "; ".join(host.prompts))
                return 0 if a.ok else 1

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
    finally:
        if dut_ctl:
            dut_ctl.close()
        if bm:
            bm.close()
            print(f"DUT: bt-daemon confirmed {bm.confirmed} pairing request(s)")


def _add_bt_parser(sub) -> None:
    from ctt.bt import CAPABILITIES
    bt = sub.add_parser("bt", allow_abbrev=False, help="Bluetooth controller / pairing (via bluetoothctl)")
    bsub = bt.add_subparsers(dest="bt_cmd", required=True)

    def common(p, side_default="host", addr: str | None = None):
        _add_dut_args(p)
        p.add_argument("--side", choices=("host", "dut"), default=side_default,
                       help=f"where to run bluetoothctl (default {side_default})")
        p.add_argument("--btctl", default=os.environ.get("CTT_BLUETOOTHCTL"),
                       help=argparse.SUPPRESS)  # alternate bluetoothctl command (tests)
        if addr == "required":
            p.add_argument("addr", help="peer address AA:BB:CC:DD:EE:FF")
        elif addr == "optional":
            p.add_argument("addr", nargs="?", default=None,
                           help="peer address (default: the DUT's controller, read via --dut)")
        p.set_defaults(func=cmd_bt)

    common(bsub.add_parser("list", allow_abbrev=False, help="list controllers"))
    p = bsub.add_parser("scan", allow_abbrev=False, help="discover nearby devices")
    common(p)
    p.add_argument("-t", "--time", type=float, default=10)
    common(bsub.add_parser("info", allow_abbrev=False, help="device properties"), addr="required")
    common(bsub.add_parser("unpair", allow_abbrev=False, help="remove a paired device"), addr="required")
    p = bsub.add_parser("prepare", allow_abbrev=False, help="power on + pairable + discoverable + agent")
    common(p, side_default="dut")
    p.add_argument("--capability", choices=CAPABILITIES, default="NoInputNoOutput")
    p.add_argument("--no-discoverable", action="store_true")
    p.add_argument("--hold", type=float, default=0,
                   help="keep the agent alive N seconds to accept incoming pairing")

    for name, helptext in (("pair", "pair host -> peer (default peer: the DUT)"),
                           ("pair-stress", "repeat remove/discover/pair N times")):
        p = bsub.add_parser(name, allow_abbrev=False, help=helptext)
        common(p, addr="optional")
        p.add_argument("--capability", choices=CAPABILITIES, default="NoInputNoOutput",
                       help="host agent IO capability")
        p.add_argument("--pin", default="0000", help="legacy PIN to answer with")
        p.add_argument("--passkey", default=None, help="passkey to answer with")
        p.add_argument("--no-trust", action="store_true")
        p.add_argument("--dut-accept", nargs="?", const="auto", default=None,
                       choices=("auto", "bm", "bluetoothctl"),
                       help="make the DUT accept the pairing: bm = through bt-daemon's "
                            "D-Bus API (vendor image), bluetoothctl = own BlueZ agent; "
                            "auto (default) picks bm when bt-daemon runs")
        p.add_argument("--scan-timeout", type=float, default=20)
        p.add_argument("--timeout", type=float, default=40, help="pairing timeout")
        if name == "pair-stress":
            p.add_argument("-n", "--count", type=int, default=10)
            p.add_argument("--connect", action="store_true", help="also connect after pairing")
            p.add_argument("--min-success-rate", type=float, default=None,
                           help="pass threshold 0..1 (default: every attempt must pass)")
            _add_out_args(p)


def cmd_usb(args) -> int:
    from ctt import usb
    act = args.usb_cmd
    if act == "gadget":
        dut = make_executor(args.dut, args.ssh_port, args.ssh_opt)
        r = usb.gadget(dut, args.action, ncm=args.ncm, keep_fw=args.keep_fw)
        print((r.stdout + r.stderr).strip())
        return r.returncode
    if act == "list":
        ex = LocalExecutor() if args.side == "host" else make_executor(args.dut, args.ssh_port,
                                                                       args.ssh_opt)
        devs = [d for d in usb.list_devices(ex) if args.all or not d["root_hub"]]
        for d in devs:
            sp = f"{d['speed_mbps']:g}M" if d["speed_mbps"] else "?"
            print(f"{d['port']:<10} {d['id']}  {sp:>6}  {d['manufacturer']} {d['product']}".rstrip())
        if not devs:
            print("(no USB devices)")
        return 0
    # storage
    dut = make_executor(args.dut, args.ssh_port, args.ssh_opt)
    agent = (args.dut_agent or args.host_agent or find_host_agent()) if isinstance(dut, LocalExecutor) \
        else (args.dut_agent or DEFAULT_DUT_AGENT)
    print(f"storage test {args.path} ({args.size}, block {args.block}) on {dut.name} ...")
    ev = usb.storage(dut, agent, args.path, args.size, args.block, direct=not args.no_direct)
    expect = {k: float(v) for k, v in _kv(args.expect, "--expect").items()}
    checks = []
    for key, val in expect.items():
        if not key.endswith("_min"):
            raise SystemExit(f"ctt usb storage: unknown expect key {key} (write_MBps_min, read_MBps_min)")
        metric = key[:-4]
        v = ev.get(metric)
        checks.append({"check": key, "value": v, "op": ">=", "limit": val,
                       "ok": v is not None and v >= val})
    if ev.get("event") == "error":
        verdict, error = "ERROR", ev.get("message")
    else:
        ok = bool(ev.get("ok")) and all(c["ok"] for c in checks)
        verdict, error = ("PASS" if ok else "FAIL"), ("" if ev.get("ok") else "data verification failed")
    for k in ("write_MBps", "read_MBps"):
        if k in ev:
            print(f"  {k[:-5]:<6} {ev[k]:9.2f} MB/s ({ev[k] * 8:8.1f} Mbit/s)"
                  f"{'' if ev.get(k[:-5] + '_direct') else '  [buffered]'}")
    if "verify_errors" in ev:
        print(f"  verify errors: {ev['verify_errors']}")
    for c in checks:
        if not c["ok"]:
            print(f"  check {c['check']}: {c['value']} not >= {c['limit']}")
    if error:
        print(f"  error: {error}")
    print(f"  => {color(verdict, VERDICT_COLOR[verdict])}")
    report = ReportWriter(args.out, "usb-storage")
    row = {"name": f"storage {args.path}", "transport": "storage", "mode": "rw", "verdict": verdict,
           **{k: ev.get(k) for k in ("write_MBps", "read_MBps", "verify_errors") if k in ev},
           "error": error or ""}
    report.add_record("storage", row, {"result": ev, "checks": checks})
    report.finish({"run": "usb-storage", "dut": dut.name, "ctt_version": __version__})
    print(f"report: {report.dir}")
    return 0 if verdict == "PASS" else 1


def cmd_fw(args) -> int:
    import json
    from ctt import fw
    dut = make_executor(args.dut, args.ssh_port, args.ssh_opt)
    if args.fw_cmd == "status":
        st = fw.status(dut)
        print(json.dumps(st, indent=2) if args.json else fw.format_status(st))
        return 0 if st else 1
    if args.fw_cmd == "wifi-mode":
        print(fw.wifi_set_mode(dut, args.mode))
        return 0
    if args.fw_cmd == "bt-unpair":
        print(fw.vendor_call(dut, "RemoveDevice", "s", args.addr))
        return 0
    raise SystemExit(f"ctt fw: unknown command {args.fw_cmd}")


def cmd_ui(args) -> int:
    from ctt.ui import serve
    if args.dut:
        os.environ["CTT_DUT"] = args.dut
    serve(args.host, args.port, args.out, open_browser=args.open)
    return 0


def cmd_report(args) -> int:
    from ctt.html_report import write_reports
    h, j = write_reports(args.run_dir)
    print(f"{h}\n{j}")
    return 0


def cmd_compare(args) -> int:
    from ctt.compare import compare, format_table
    rows = compare(args.baseline, args.new, args.tolerance)
    shown = rows if args.all else [r for r in rows if r["status"] != "ok"]
    print(format_table(shown) if shown else "no differences beyond tolerance")
    reg = [r for r in rows if r["status"] in ("regression", "missing")]
    print(f"\n{len(reg)} regression(s) (tolerance {args.tolerance:g}%)")
    return 1 if reg else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ctt", description="Connectivity Test Tool",
                                allow_abbrev=False)
    p.add_argument("--version", action="version", version=f"ctt {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", allow_abbrev=False, help="run a single test")
    _add_dut_args(r)
    _add_test_args(r)
    _add_out_args(r)
    r.set_defaults(func=cmd_run)

    pr = sub.add_parser("profile", allow_abbrev=False, help="run a YAML test profile")
    pr.add_argument("profile")
    pr.add_argument("--only", action="append", help="run tests whose name contains this")
    pr.add_argument("--list", action="store_true", help="list expanded tests and exit")
    pr.add_argument("-d", "--duration", default=None, help="override every test's duration")
    _add_dut_args(pr)
    _add_out_args(pr)
    pr.set_defaults(func=cmd_profile)

    st = sub.add_parser("selftest", allow_abbrev=False, help="all modes over TCP loopback on this machine")
    st.add_argument("-d", "--duration", default="2s")
    st.add_argument("--port", type=int, default=5299)
    st.add_argument("--ssh-port", type=int, default=None)
    st.add_argument("--ssh-opt", action="append", default=[])
    st.add_argument("--dut-agent", default=None)
    st.add_argument("--host-agent", default=None)
    _add_out_args(st)
    st.set_defaults(func=cmd_selftest)

    d = sub.add_parser("deploy", allow_abbrev=False, help="copy ctt-agent to the DUT")
    d.add_argument("--binary", default=None, help="default: build/dut/agent/ctt-agent")
    _add_dut_args(d)
    d.set_defaults(func=cmd_deploy)

    i = sub.add_parser("dut-info", allow_abbrev=False, help="inventory of the DUT's connectivity stack")
    _add_dut_args(i)
    i.set_defaults(func=cmd_dut_info)

    _add_bt_parser(sub)

    u = sub.add_parser("usb", allow_abbrev=False, help="USB gadget setup, inventory, storage test")
    usub = u.add_subparsers(dest="usb_cmd", required=True)
    g = usub.add_parser("gadget", allow_abbrev=False, help="create/remove the CTT gadget on the DUT")
    g.add_argument("action", choices=("up", "down", "status", "bind"))
    g.add_argument("--ncm", action="store_true", help="also add an NCM network function (usb0)")
    g.add_argument("--keep-fw", action="store_true",
                   help="fail instead of stopping usb-daemon (vendor image) to take the port")
    _add_dut_args(g)
    g.set_defaults(func=cmd_usb)
    lp = usub.add_parser("list", allow_abbrev=False, help="USB devices and negotiated speed")
    lp.add_argument("--side", choices=("host", "dut"), default="dut")
    lp.add_argument("--all", action="store_true", help="include root hubs")
    _add_dut_args(lp)
    lp.set_defaults(func=cmd_usb)
    sp = usub.add_parser("storage", allow_abbrev=False,
                         help="write/read throughput of a file on USB storage (DUT as USB host)")
    sp.add_argument("--path", required=True,
                    help="test file on the mounted device, e.g. /run/media/sda1/ctt.bin")
    sp.add_argument("--size", default="256M")
    sp.add_argument("--block", default="1M")
    sp.add_argument("--no-direct", action="store_true", help="buffered I/O instead of O_DIRECT")
    sp.add_argument("--expect", action="append", default=[], metavar="KEY=VALUE",
                    help="write_MBps_min=, read_MBps_min=")
    _add_dut_args(sp)
    _add_out_args(sp)
    sp.set_defaults(func=cmd_usb)

    f = sub.add_parser("fw", allow_abbrev=False,
                       help="state / control of the vendor connectivity daemons on the DUT")
    fsub = f.add_subparsers(dest="fw_cmd", required=True)
    fp = fsub.add_parser("status", allow_abbrev=False,
                         help="usb-daemon / bt-daemon / wifi-daemon state")
    fp.add_argument("--json", action="store_true")
    _add_dut_args(fp)
    fp.set_defaults(func=cmd_fw)
    fp = fsub.add_parser("wifi-mode", allow_abbrev=False, help="wifi-daemon SetMode")
    fp.add_argument("mode", choices=("sta", "ap", "p2p"))
    _add_dut_args(fp)
    fp.set_defaults(func=cmd_fw)
    fp = fsub.add_parser("bt-unpair", allow_abbrev=False,
                         help="remove a bond through bt-daemon (frees one of its 5 slots)")
    fp.add_argument("addr", help="address or BlueZ object path")
    _add_dut_args(fp)
    fp.set_defaults(func=cmd_fw)

    ui = sub.add_parser("ui", allow_abbrev=False, help="web interface (http://localhost:8765)")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--host", default="127.0.0.1",
                    help="bind address (default 127.0.0.1; the UI has no login)")
    ui.add_argument("--dut", default=None, help="DUT pre-filled in the page (default $CTT_DUT)")
    ui.add_argument("--open", action="store_true", help="open the page in the browser")
    _add_out_args(ui)
    ui.set_defaults(func=cmd_ui)

    rp = sub.add_parser("report", allow_abbrev=False, help="(re)build report.html + junit.xml")
    rp.add_argument("run_dir", help="out/<timestamp>_<name> directory")
    rp.set_defaults(func=cmd_report)

    cp = sub.add_parser("compare", allow_abbrev=False, help="compare a run against a baseline run")
    cp.add_argument("baseline", help="baseline run directory")
    cp.add_argument("new", help="new run directory")
    cp.add_argument("--tolerance", type=float, default=10.0, help="allowed change in %% (default 10)")
    cp.add_argument("--all", action="store_true", help="also show unchanged metrics")
    cp.set_defaults(func=cmd_compare)

    m = sub.add_parser("mfi", allow_abbrev=False,
                       help="MFi auth coprocessor tests over I2C (config-driven)")
    m.add_argument("config", help="MFi test config (see configs/mfi_cp.example.yaml)")
    m.add_argument("--only", action="append", help="run tests whose id/name contains this")
    m.add_argument("--repeat-scale", type=float, default=1.0,
                   help="multiply every test's repeat count (e.g. 0.1 for a quick run)")
    _add_dut_args(m)
    _add_out_args(m)
    m.set_defaults(func=cmd_mfi)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError, RuntimeError) as e:  # includes bt.BtError
        print(f"ctt: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nctt: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
