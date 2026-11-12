"""Report tools: `ctt report`, `ctt compare`."""

from __future__ import annotations

from ctt.cli.base import Command


class ReportCommand(Command):
    name = "report"
    help = "(re)build report.html + junit.xml"

    def configure(self, p):
        p.add_argument("run_dir", help="out/<timestamp>_<name> directory")

    def run(self, args):
        from ctt.reporting.html import write_reports
        h, j = write_reports(args.run_dir)
        print(f"{h}\n{j}")
        return 0


class CompareCommand(Command):
    name = "compare"
    help = "compare a run against a baseline run"

    def configure(self, p):
        p.add_argument("baseline", help="baseline run directory")
        p.add_argument("new", help="new run directory")
        p.add_argument("--tolerance", type=float, default=10.0, help="allowed change in %% (default 10)")
        p.add_argument("--all", action="store_true", help="also show unchanged metrics")

    def run(self, args):
        from ctt.reporting.compare import compare, format_table
        rows = compare(args.baseline, args.new, args.tolerance)
        shown = rows if args.all else [r for r in rows if r["status"] != "ok"]
        print(format_table(shown) if shown else "no differences beyond tolerance")
        reg = [r for r in rows if r["status"] in ("regression", "missing")]
        print(f"\n{len(reg)} regression(s) (tolerance {args.tolerance:g}%)")
        return 1 if reg else 0
