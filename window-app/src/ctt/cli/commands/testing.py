"""Throughput / latency tests: `ctt run`, `ctt profile`, `ctt selftest`."""

from __future__ import annotations

import os

from ctt import __version__
from ctt.cli.base import Command
from ctt.cli.options import (add_dut_args, add_out_args, add_test_args, dut_agent_path,
                             dut_executor, parse_kv, warn)
from ctt.core.agent import find_host_agent
from ctt.core.executor import LocalExecutor
from ctt.reporting.console import print_live_event, print_result, print_summary
from ctt.reporting.writer import ReportWriter
from ctt.testing.runner import MODES, Runner, TestSpec


class SpecSuite:
    """Runs a list of TestSpec against the DUT and writes one report directory."""

    def __init__(self, args):
        self.args = args
        self.dut = dut_executor(args)
        host_agent = args.host_agent or find_host_agent()
        if isinstance(self.dut, LocalExecutor):
            dut_agent = args.dut_agent or host_agent
        else:
            dut_agent = dut_agent_path(args, self.dut)
        quiet = getattr(args, "quiet", False)
        self.runner = Runner(self.dut, LocalExecutor(), dut_agent, host_agent,
                             on_event=lambda side, ev: print_live_event(side, ev, quiet))

    def firmware_preflight(self, transports: set[str]) -> None:
        """Warns when a test would collide with what runs on the board (board profile)."""
        if isinstance(self.dut, LocalExecutor) or os.environ.get("CTT_NO_FW_CHECK"):
            return
        from ctt.cli.options import board_profile
        from ctt.platform import board
        prof = board_profile(self.args)
        for w in board.warnings_for(board.status(self.dut, prof), transports, False, prof):
            warn(w)

    def run(self, run_name: str, specs: list[TestSpec]) -> int:
        self.firmware_preflight({s.transport for s in specs})
        report = ReportWriter(self.args.out, run_name)
        results = []
        groups: list[list[TestSpec]] = []      # consecutive streams of one group run together
        for spec in specs:
            if spec.group and groups and groups[-1][0].group == spec.group:
                groups[-1].append(spec)
            else:
                groups.append([spec])
        for i, group in enumerate(groups, 1):
            head = group[0]
            if len(group) > 1:
                print(f"\n[{i}/{len(groups)}] {head.group} ({head.protocol or 'parallel'}): "
                      f"{len(group)} streams at once, {head.duration:g}s, DUT={self.dut.name}")
                for s in group:
                    print(f"    - {s.name}: {s.transport} {s.mode}"
                          + (f" burst {s.burst} B @ {s.burst_rate:g} Hz" if s.burst else ""))
            else:
                print(f"\n[{i}/{len(groups)}] {head.name}: {head.transport} {head.mode} "
                      f"{head.duration:g}s (server on {head.server_side}, DUT={self.dut.name})")
            for r in self.runner.run_group(group):
                if len(group) > 1:
                    print(f"  {r.spec.name}:")
                print_result(r)
                report.add(r)
                results.append(r)
        report.finish({"run": run_name, "dut": self.dut.name, "ctt_version": __version__})
        print_summary(results, report.dir)
        return 0 if all(r.verdict == "PASS" for r in results) else 1


class RunCommand(Command):
    name = "run"
    help = "run a single test"

    def configure(self, p):
        add_dut_args(p)
        add_test_args(p)
        add_out_args(p)

    def run(self, args):
        d = {
            "name": args.name or f"{args.transport}-{args.mode}",
            "transport": args.transport, "mode": args.mode, "duration": args.duration,
            "block": args.block, "rate": args.rate, "verify": not args.no_verify,
            "burst": args.burst, "burst_rate": args.burst_rate,
            "count": args.count, "echo_interval_ms": args.echo_interval_ms,
            "interval": args.interval, "port": args.port, "bdaddr": args.bdaddr,
            "endpoint": args.endpoint, "target_addr": args.target_addr,
            "listen": args.listen, "server_side": args.server_side, "sndbuf": args.sndbuf,
            "rcvbuf": args.rcvbuf, "nodelay": args.nodelay, "timeout": args.timeout,
            "wifi_iface": args.wifi_iface,
            "transport_opts": parse_kv(args.opt, "--opt"),
            "expect": {k: float(v) for k, v in parse_kv(args.expect, "--expect").items()},
        }
        spec = TestSpec.from_dict(d)
        return SpecSuite(args).run(spec.name, [spec])


class ProfileCommand(Command):
    name = "profile"
    help = "run a YAML test profile"

    def configure(self, p):
        p.add_argument("profile")
        p.add_argument("--only", action="append", help="run tests whose name contains this")
        p.add_argument("--list", action="store_true", help="list expanded tests and exit")
        p.add_argument("-d", "--duration", default=None, help="override every test's duration")
        p.add_argument("--target-addr", default=None,
                       help="tcp/udp: IP the PC connects to when data runs on another interface than "
                            "SSH (board's usb0 for USB NCM, wlan0 for WiFi); streams keep their ports")
        add_dut_args(p)
        add_out_args(p)

    def run(self, args):
        from ctt.testing.profile import load_profile
        overrides = {}
        if args.duration:
            overrides["duration"] = args.duration
        if args.target_addr:
            overrides["target_addr"] = args.target_addr
        name, specs = load_profile(args.profile, overrides)
        if args.only:
            specs = [s for s in specs if any(o in s.name for o in args.only)]
        if args.list:
            for s in specs:
                load = f"burst={s.burst}B@{s.burst_rate:g}Hz" if s.burst else \
                    (f"rate={s.rate}" if s.rate else "")
                print(f"{s.name:<34} {s.transport:<5} {s.mode:<6} {s.duration:g}s "
                      f"block={s.block or 'default'} {load} {'[parallel]' if s.group else ''} "
                      f"expect={s.expect or '-'}")
            return 0
        return SpecSuite(args).run(name, specs)


class SelftestCommand(Command):
    name = "selftest"
    help = "all modes over TCP loopback on this machine"

    def configure(self, p):
        p.add_argument("-d", "--duration", default="2s")
        p.add_argument("--port", type=int, default=5299)
        p.add_argument("--ssh-port", type=int, default=None)
        p.add_argument("--ssh-opt", action="append", default=[])
        p.add_argument("--dut-agent", default=None)
        p.add_argument("--host-agent", default=None)
        add_out_args(p)

    def run(self, args):
        args.dut = "local"
        specs = [TestSpec.from_dict({"name": f"selftest-{m}", "mode": m, "duration": args.duration,
                                     "port": args.port, "interval": 0,
                                     "expect": {"lost_max": 0}})
                 for m in MODES]
        return SpecSuite(args).run("selftest", specs)
