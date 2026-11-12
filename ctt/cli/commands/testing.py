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
        """Warns when a test would collide with the vendor firmware daemons on the DUT."""
        if isinstance(self.dut, LocalExecutor) or os.environ.get("CTT_NO_FW_CHECK"):
            return
        from ctt.firmware import vendor_daemons as fw
        for w in fw.warnings_for(fw.status(self.dut), transports, False):
            warn(w)

    def run(self, run_name: str, specs: list[TestSpec]) -> int:
        self.firmware_preflight({s.transport for s in specs})
        report = ReportWriter(self.args.out, run_name)
        results = []
        for i, spec in enumerate(specs, 1):
            print(f"\n[{i}/{len(specs)}] {spec.name}: {spec.transport} {spec.mode} "
                  f"{spec.duration:g}s (server on {spec.server_side}, DUT={self.dut.name})")
            r = self.runner.run(spec)
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
            "count": args.count, "echo_interval_ms": args.echo_interval_ms,
            "interval": args.interval, "port": args.port, "bdaddr": args.bdaddr,
            "endpoint": args.endpoint,
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
        add_dut_args(p)
        add_out_args(p)

    def run(self, args):
        from ctt.testing.profile import load_profile
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
