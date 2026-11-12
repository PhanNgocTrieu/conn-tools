"""Python unit + integration tests:  python3 -m unittest discover -s tests"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctt.core import units  # noqa: E402
from ctt.core.agent import find_host_agent  # noqa: E402
from ctt.core.executor import LocalExecutor, SshExecutor, make_executor  # noqa: E402
from ctt.testing.profile import expand  # noqa: E402
from ctt.testing.runner import Runner, TestSpec, evaluate, summarize  # noqa: E402


class UnitsTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(units.parse_size("64K"), 65536)
        self.assertEqual(units.parse_size(990), 990)
        self.assertEqual(units.parse_rate("1.5Mbps"), 1_500_000)
        self.assertEqual(units.parse_seconds("500ms"), 0.5)
        self.assertEqual(units.parse_seconds("2m"), 120)
        with self.assertRaises(ValueError):
            units.parse_size("12X")


class SpecTest(unittest.TestCase):
    def test_from_dict(self):
        s = TestSpec.from_dict({"name": "a", "mode": "bidir", "block": "4K", "duration": "3s",
                                "rate": "2M"})
        self.assertEqual((s.block, s.duration, s.rate), (4096, 3.0, 2_000_000))
        self.assertEqual(TestSpec.from_dict({"timeout": "20s"}).timeout, 20.0)
        with self.assertRaises(ValueError):
            TestSpec.from_dict({"mode": "sideways"})
        with self.assertRaises(ValueError):
            TestSpec.from_dict({"bogus": 1})
        with self.assertRaises(ValueError):
            TestSpec.from_dict({"expect": {"speed": 1}})

    def test_profile_matrix(self):
        doc = {"name": "p", "defaults": {"duration": "2s", "expect": {"lost_max": 0}},
               "tests": [{"name": "t", "mode": "tx", "expect": {"up_mbps_min": 5},
                          "matrix": {"block": ["1K", "2K"], "rate": ["1M", "2M"]}},
                         {"mode": "echo"}]}
        name, specs = expand(doc)
        self.assertEqual(name, "p")
        self.assertEqual(len(specs), 5)
        self.assertEqual(specs[0].name, "t-block=1K-rate=1M")
        self.assertEqual(specs[0].expect, {"lost_max": 0, "up_mbps_min": 5})
        self.assertEqual(specs[3].block, 2048)
        self.assertEqual(specs[4].name, "test2")
        _, specs = expand(doc, {"duration": "1s"})
        self.assertTrue(all(s.duration == 1.0 for s in specs))

    def test_profile_nested_matrix(self):
        doc = {"defaults": {"transport": "rfcomm", "transport_opts": {"adapter": "hci0"}},
               "tests": [{"name": "sec", "matrix": {"transport_opts.security": ["low", "high"]}}]}
        _, specs = expand(doc)
        self.assertEqual([s.name for s in specs], ["sec-security=low", "sec-security=high"])
        self.assertEqual(specs[1].transport_opts, {"adapter": "hci0", "security": "high"})

    def test_shipped_profiles_load(self):
        from ctt.testing.profile import load_profile
        for path in sorted((Path(__file__).resolve().parent.parent / "profiles").glob("*.yaml")):
            name, specs = load_profile(path)
            self.assertTrue(specs, path.name)


class VerdictTest(unittest.TestCase):
    RESULT = {"up": {"sender": {"mbps": 10.0},
                     "receiver": {"mbps": 9.5, "bytes": 100, "lost": 0, "crc_errors": 0}},
              "rtt": {"count": 3, "avg_us": 100.0, "p99_us": 150.0}}

    def test_summarize_and_evaluate(self):
        m = summarize(self.RESULT)
        self.assertEqual(m["up_mbps"], 9.5)
        self.assertEqual(m["lost"], 0)
        checks = evaluate(m, {"up_mbps_min": 9, "rtt_p99_us_max": 100})
        by = {c["check"]: c["ok"] for c in checks}
        self.assertTrue(by["up_mbps_min"])
        self.assertFalse(by["rtt_p99_us_max"])
        self.assertTrue(by["crc_errors_max"])  # default check
        # missing metric fails the check instead of passing silently
        self.assertFalse(evaluate(m, {"down_mbps_min": 1})[-1]["ok"])


class ExecutorTest(unittest.TestCase):
    def test_make(self):
        self.assertIsInstance(make_executor("local"), LocalExecutor)
        e = make_executor("root@192.168.1.50", 2222)
        self.assertIsInstance(e, SshExecutor)
        self.assertEqual(e.address, "192.168.1.50")
        self.assertIn("2222", e.opts)


class LoopbackIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.agent = find_host_agent()
        except FileNotFoundError:
            raise unittest.SkipTest("ctt-agent not built")

    def test_all_modes(self):
        local = LocalExecutor()
        runner = Runner(local, local, self.agent, self.agent)
        for i, mode in enumerate(("tx", "rx", "bidir", "echo")):
            spec = TestSpec.from_dict({"name": mode, "mode": mode, "duration": "1s",
                                       "interval": "0.5s", "port": 26000 + i,
                                       "expect": {"lost_max": 0}})
            r = runner.run(spec)
            self.assertEqual(r.verdict, "PASS", f"{mode}: {r.error} {r.checks}")
            json.dumps(r.to_dict())  # serialisable
            if mode in ("tx", "bidir"):
                self.assertGreater(r.metrics["up_mbps"], 0)
            if mode in ("rx", "bidir"):
                self.assertGreater(r.metrics["down_mbps"], 0)
            if mode == "echo":
                self.assertGreater(r.metrics["rtt_count"], 10)

    def test_server_missing_reports_error(self):
        local = LocalExecutor()
        runner = Runner(local, local, "/nonexistent/ctt-agent", self.agent)
        r = runner.run(TestSpec(name="x", duration=1))
        self.assertEqual(r.verdict, "ERROR")
        self.assertTrue(r.error)

    def test_cli_no_abbreviations(self):
        from ctt.cli import build_parser
        with self.assertRaises(SystemExit):  # must not be read as --dut-agent
            build_parser().parse_args(["run", "--dut-a", "x"])
        args = build_parser().parse_args(["run", "--timeout", "30s"])
        self.assertEqual(args.timeout, "30s")

    def test_cli_selftest(self):
        from ctt.cli import main
        with tempfile.TemporaryDirectory() as d:
            rc = main(["selftest", "-d", "1", "--port", "26100", "--out", d, "-q"])
            self.assertEqual(rc, 0)
            out = list(Path(d).glob("*/summary.json"))
            self.assertEqual(len(out), 1)
            summary = json.loads(out[0].read_text())
            self.assertEqual(summary["passed"], 4)


if __name__ == "__main__":
    unittest.main()
