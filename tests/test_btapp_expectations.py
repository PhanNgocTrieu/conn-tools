"""Mbit/s / RTT expectations and the Bluetooth test suite (no Bluetooth needed)."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ctt.btapp.expectations import (DEFAULT_SUITE, SuiteRunner, all_ok, evaluate,  # noqa: E402
                                    load_suite, outcome_line, summary_lines, write_report)
from ctt.protocol.ctp import Result  # noqa: E402


class FakeService:
    """run_throughput returns canned results per mode."""

    def __init__(self, results: dict, fail_mode: str = ""):
        self.results = results
        self.fail_mode = fail_mode
        self.calls = []

    def run_throughput(self, params, on_interval=None, auto_deploy=False):
        self.calls.append((params.mode, params.block, params.duration_s))
        if params.mode == self.fail_mode:
            raise OSError("RFCOMM connect failed")
        return self.results[params.mode]

    def stop_throughput(self):
        return False


GOOD_WINDOWS = {   # values of the order measured on the Local Test setup (report.md)
    "tx": Result("tx", 990, 30, up_mbps=0.32),
    "rx": Result("rx", 990, 30, down_mbps=0.56),
    "bidir": Result("bidir", 990, 30, up_mbps=0.20, down_mbps=0.30),
    "echo": Result("echo", 64, 10, rtt_ms={"min": 10, "avg": 30, "p50": 28, "p95": 60, "p99": 90,
                                           "max": 120, "count": 300}),
}


class EvaluateTest(unittest.TestCase):
    def test_integrity_always_checked(self):
        self.assertTrue(all_ok(evaluate(Result("tx", 990, 1, up_mbps=0.1))))
        self.assertFalse(all_ok(evaluate(Result("tx", 990, 1, up_mbps=5, lost=1))))
        self.assertFalse(all_ok(evaluate(Result("tx", 990, 1, up_mbps=5, crc_errors=2))))
        self.assertFalse(all_ok(evaluate(Result("tx", 990, 1, up_mbps=5, out_of_order=1))))

    def test_mbps_minimum_and_rtt_maximum(self):
        r = Result("bidir", 990, 30, up_mbps=0.6, down_mbps=0.4)
        checks = evaluate(r, {"up_mbps_min": 0.5, "down_mbps_min": 0.5})
        self.assertEqual([c.ok for c in checks[3:]], [True, False])
        self.assertIn("board->PC 0.400 >= 0.5 Mbit/s", checks[4].text())
        echo = GOOD_WINDOWS["echo"]
        self.assertTrue(all_ok(evaluate(echo, {"rtt_p99_ms_max": 100})))
        self.assertFalse(all_ok(evaluate(echo, {"rtt_p99_ms_max": 50})))

    def test_missing_metric_fails_and_none_limit_is_skipped(self):
        r = Result("tx", 990, 30, up_mbps=0.3)
        self.assertFalse(all_ok(evaluate(r, {"down_mbps_min": 0.1})))
        self.assertTrue(all_ok(evaluate(r, {"down_mbps_min": None, "up_mbps_min": 0.25})))

    def test_unknown_key(self):
        with self.assertRaises(ValueError):
            evaluate(Result("tx", 990, 1), {"speed": 1})


class SuiteTest(unittest.TestCase):
    def run_suite(self, level, service):
        return SuiteRunner(service, pause_s=0).run(DEFAULT_SUITE.cases, level)

    def test_default_suite_shape(self):
        ids = [c.id for c in DEFAULT_SUITE.cases]
        self.assertEqual(ids, [f"BT-TP-0{i}" for i in range(1, 7)])
        for c in DEFAULT_SUITE.cases:
            self.assertEqual(set(c.expect), {"baseline", "target"}, c.id)

    def test_local_test_numbers_pass_baseline_fail_target(self):
        svc = FakeService(GOOD_WINDOWS)
        baseline = self.run_suite("baseline", svc)
        self.assertTrue(all(o.ok for o in baseline), summary_lines(baseline))
        self.assertEqual(len(svc.calls), 6)
        self.assertIn(("tx", 4096, 30), svc.calls)
        target = self.run_suite("target", FakeService(GOOD_WINDOWS))
        self.assertEqual([o.ok for o in target], [False, False, False, True, False, False])

    def test_measure_level_records_without_limits(self):
        slow = {**GOOD_WINDOWS, "tx": Result("tx", 990, 30, up_mbps=0.12, up_bytes=450_000, up_sent_bytes=450_000)}
        outcomes = self.run_suite("measure", FakeService(slow))
        self.assertEqual([o.verdict for o in outcomes], ["OK"] * 6)
        text = "\n".join(summary_lines(outcomes))
        self.assertIn("6/6 measured, data OK in 6", text)
        self.assertNotIn("PASS", text)
        self.assertIn("PC->board 0.120 Mbit/s", outcome_line(outcomes[0]))
        bad = {**GOOD_WINDOWS, "rx": Result("rx", 990, 30, down_mbps=0.5, lost=3)}
        self.assertIn("DATA ERR", [o.verdict for o in self.run_suite("measure", FakeService(bad))])
        err = self.run_suite("measure", FakeService(GOOD_WINDOWS, fail_mode="echo"))
        self.assertEqual(err[3].verdict, "ERROR")

    def test_connection_error_is_a_fail_not_a_crash(self):
        outcomes = self.run_suite("baseline", FakeService(GOOD_WINDOWS, fail_mode="rx"))
        self.assertEqual(sum(not o.ok for o in outcomes), 2)
        self.assertIn("RFCOMM connect failed", "\n".join(summary_lines(outcomes)))

    def test_select_and_report(self):
        cases = DEFAULT_SUITE.select(["bt-tp-01", "BT-TP-04"])
        self.assertEqual([c.id for c in cases], ["BT-TP-01", "BT-TP-04"])
        with self.assertRaises(ValueError):
            DEFAULT_SUITE.select(["BT-TP-99"])
        outcomes = SuiteRunner(FakeService(GOOD_WINDOWS), pause_s=0).run(cases, "target")
        with tempfile.TemporaryDirectory() as d:
            path = write_report(outcomes, DEFAULT_SUITE, "target", d)
            data = json.loads(path.read_text())
            self.assertEqual((data["passed"], data["total"]), (1, 2))
            self.assertEqual(data["cases"][0]["verdict"], "FAIL")
            csv_text = path.with_suffix(".csv").read_text()
            self.assertIn("BT-TP-01", csv_text)
            self.assertIn("PC->board 0.320 >= 1 Mbit/s", csv_text)

    def test_dump_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "suite.json"
            data = json.loads(DEFAULT_SUITE.to_json())
            data["cases"][0]["expect"]["target"]["up_mbps_min"] = 0.3
            p.write_text(json.dumps(data))
            suite = load_suite(p)
            self.assertEqual(suite.cases[0].expect_for("target"), {"up_mbps_min": 0.3})
            data["cases"][0]["expect"]["target"] = {"bogus": 1}
            p.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_suite(p)


class ConsoleSuiteCommandTest(unittest.TestCase):
    def test_list_and_dump(self):
        from ctt.btapp.__main__ import main
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["suite", "--list", "-l", "target"], default="console"), 0)
        self.assertIn("BT-TP-02  rx", out.getvalue())
        self.assertIn("target: down_mbps_min 1.0", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            main(["suite", "--dump"], default="console")
        self.assertEqual(len(json.loads(out.getvalue())["cases"]), 6)


if __name__ == "__main__":
    unittest.main()
