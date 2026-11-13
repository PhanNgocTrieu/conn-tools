"""HTML / JUnit report generation and baseline comparison."""

import json
import sys
import tempfile
import unittest
import xml.dom.minidom
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
sys.path.insert(0, str(ROOT / "app" / "src"))

from ctt.reporting.compare import compare  # noqa: E402
from ctt.reporting.html import render_html, render_junit, svg_chart, write_reports  # noqa: E402
from ctt.reporting.writer import ReportWriter  # noqa: E402


def make_run(root: Path, name: str, rows: list[dict], intervals=None) -> Path:
    w = ReportWriter(root, name)
    for r in rows:
        data = {"checks": [{"check": "up_mbps_min", "value": r.get("up_mbps"), "op": ">=",
                            "limit": 5, "ok": r["verdict"] == "PASS"}]}
        w.add_record(r["name"], r, data, intervals)
    w.finish({"run": name, "dut": "local", "ctt_version": "test"})
    return w.dir


ROWS = [
    {"name": "up", "transport": "tcp", "mode": "tx", "verdict": "PASS", "up_mbps": 100.0,
     "lost": 0, "crc_errors": 0, "error": ""},
    {"name": "rtt", "transport": "tcp", "mode": "echo", "verdict": "PASS", "rtt_avg_us": 100.0,
     "lost": 0, "crc_errors": 0, "error": ""},
    {"name": "udp", "transport": "udp", "mode": "tx", "verdict": "FAIL", "up_mbps": 9.0,
     "loss_pct": 3.0, "lost": 30, "crc_errors": 0, "error": "loss <too high> & \"quoted\""},
]
INTERVALS = [{"side": "server", "dir": "up", "role": "rx", "t_start": "0", "t_end": str(t),
              "mbps": str(90 + t)} for t in (1, 2, 3)]


class ReportTest(unittest.TestCase):
    def test_html_and_junit(self):
        with tempfile.TemporaryDirectory() as d:
            run = make_run(Path(d), "demo", ROWS, INTERVALS)
            self.assertTrue((run / "report.html").exists())  # written by finish()
            page = render_html(run)
            self.assertIn("<polyline", page)
            self.assertIn("loss &lt;too high&gt;", page)  # escaped
            self.assertNotIn("http://", page.replace("http://www.w3", ""))
            doc = xml.dom.minidom.parseString(render_junit(run))
            suite = doc.documentElement
            self.assertEqual(suite.getAttribute("tests"), "3")
            self.assertEqual(suite.getAttribute("failures"), "1")
            self.assertEqual(len(doc.getElementsByTagName("failure")), 1)
            h, j = write_reports(run)
            self.assertTrue(h.exists() and j.exists())

    def test_svg_chart_edge_cases(self):
        self.assertEqual(svg_chart([]), "")
        self.assertIn("<circle", svg_chart([("one point", "#000", [(1.0, 5.0)])]))
        self.assertIn("<polyline", svg_chart([("zeros", "#000", [(1.0, 0.0), (2.0, 0.0)])]))


class CompareTest(unittest.TestCase):
    def test_regressions(self):
        new_rows = [dict(ROWS[0], up_mbps=80.0),               # -20% throughput
                    dict(ROWS[1], rtt_avg_us=105.0),           # +5%: within tolerance
                    dict(ROWS[2], loss_pct=3.05, lost=31),     # loss tiny delta: ok
                    {"name": "extra", "transport": "tcp", "mode": "tx", "verdict": "PASS"}]
        with tempfile.TemporaryDirectory() as d:
            base = make_run(Path(d) / "a", "base", ROWS)
            new = make_run(Path(d) / "b", "new", new_rows)
            rows = compare(base, new, tolerance_pct=10)
            st = {(r["test"], r["metric"]): r["status"] for r in rows}
            self.assertEqual(st[("up", "up_mbps")], "regression")
            self.assertEqual(st[("rtt", "rtt_avg_us")], "ok")
            self.assertEqual(st[("udp", "loss_pct")], "ok")
            self.assertEqual(st[("extra", "-")], "new")

            # zero-expected counters regress on any increase; verdict flips too
            bad = [dict(ROWS[0], lost=1, verdict="FAIL")] + ROWS[1:]
            new2 = make_run(Path(d) / "c", "bad", bad)
            st2 = {(r["test"], r["metric"]): r["status"] for r in compare(base, new2)}
            self.assertEqual(st2[("up", "lost")], "regression")
            self.assertEqual(st2[("up", "verdict")], "regression")

    def test_cli(self):
        from ctt.cli import main
        with tempfile.TemporaryDirectory() as d:
            base = make_run(Path(d) / "a", "base", ROWS)
            self.assertEqual(main(["compare", str(base), str(base)]), 0)
            self.assertEqual(main(["report", str(base)]), 0)
            summary = json.loads((base / "summary.json").read_text())
            self.assertEqual(summary["failed"], 1)


if __name__ == "__main__":
    unittest.main()
