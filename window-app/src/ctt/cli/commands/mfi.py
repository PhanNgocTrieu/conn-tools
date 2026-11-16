"""`ctt mfi`: MFi auth coprocessor tests over I2C."""

from __future__ import annotations

from pathlib import Path

from ctt import __version__
from ctt.cli.base import Command
from ctt.cli.options import add_dut_args, add_out_args, dut_agent_path, dut_executor
from ctt.reporting.console import VERDICT_COLOR, color
from ctt.reporting.writer import ReportWriter


class MfiCommand(Command):
    name = "mfi"
    help = "MFi auth coprocessor tests over I2C (config-driven)"

    def configure(self, p):
        p.add_argument("config", help="MFi test config (see config/i2c/mfi_cp.example.yaml)")
        p.add_argument("--only", action="append", help="run tests whose id/name contains this")
        p.add_argument("--repeat-scale", type=float, default=1.0,
                       help="multiply every test's repeat count (e.g. 0.1 for a quick run)")
        add_dut_args(p)
        add_out_args(p)

    def run(self, args):
        from ctt.interfaces import mfi
        cfg = mfi.load_config(args.config)
        tests = cfg.tests
        if args.only:
            tests = [t for t in tests if any(o in t.id or o in t.name for o in args.only)]
        dut = dut_executor(args)
        agent = dut_agent_path(args, dut)
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
