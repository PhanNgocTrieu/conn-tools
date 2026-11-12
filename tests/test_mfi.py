"""MFi / I2C tests against the simulated bus (no hardware)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ctt.interfaces import mfi  # noqa: E402
from ctt.core.agent import find_host_agent  # noqa: E402
from ctt.core.executor import LocalExecutor  # noqa: E402


class ConfigTest(unittest.TestCase):
    def test_parse_and_validate(self):
        cfg = mfi.parse_config({"bus": 2, "address": "0x11", "tests": [
            {"id": "X", "steps": ["probe"], "repeat": 3, "expect": {"max_ms": 5}}]})
        self.assertEqual((cfg.bus, cfg.address, cfg.tests[0].repeat), ("2", 0x11, 3))
        argv = mfi.agent_argv("agent", cfg, cfg.tests[0], repeat_scale=2)
        self.assertEqual(argv[argv.index("--repeat") + 1], "6")
        self.assertEqual(argv[argv.index("--addr") + 1], "0x11")
        with self.assertRaises(ValueError):
            mfi.parse_config({"bus": 1, "address": 16, "tests": [{"steps": []}]})
        with self.assertRaises(ValueError):
            mfi.parse_config({"bus": 1, "address": 16,
                              "tests": [{"steps": ["probe"], "expect": {"speed": 1}}]})
        with self.assertRaises(ValueError):
            mfi.parse_config({"address": 16, "tests": []})

    def test_example_config_loads(self):
        cfg = mfi.load_config(ROOT / "configs" / "mfi_cp.example.yaml")
        self.assertEqual([t.id for t in cfg.tests], ["MFI-01", "MFI-02", "MFI-03", "MFI-04", "MFI-05"])


class AgentI2cTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.agent = find_host_agent()
        except FileNotFoundError:
            raise unittest.SkipTest("ctt-agent not built")
        cls.ex = LocalExecutor()

    def run_cfg(self, doc):
        cfg = mfi.parse_config(doc)
        return [mfi.run_test(self.ex, self.agent, cfg, t) for t in cfg.tests]

    def test_example_all_pass(self):
        cfg = mfi.load_config(ROOT / "configs" / "mfi_cp.example.yaml")
        for t in cfg.tests:
            r = mfi.run_test(self.ex, self.agent, cfg, t)
            self.assertEqual(r.verdict, "PASS", f"{t.id}: {r.error} {r.checks}")
        # stress test really retried through simulated NACKs and stayed correct
        self.assertGreater(r.metrics["retries"], 0)
        self.assertEqual(r.metrics["success_rate"], 1.0)

    def test_mismatch_and_threshold_fail(self):
        bus = "fake,r00=05"
        res = self.run_cfg({"bus": bus, "address": 0x10, "tests": [
            {"id": "A", "steps": ["v=r:0x00:1:06"], "repeat": 3},
            {"id": "B", "steps": ["v=r:0x00:1:05"], "repeat": 3,
             "expect": {"step_max_ms": {"v": 0.000001}}}]})
        self.assertEqual(res[0].verdict, "FAIL")
        self.assertIn("expected 06, got 05", res[0].error)
        self.assertEqual(res[0].metrics["success_rate"], 0.0)
        self.assertEqual(res[1].verdict, "FAIL")
        self.assertFalse(res[1].checks[-1]["ok"])

    def test_no_device_and_no_bus(self):
        # wrong address -> NACK on every retry
        r = self.run_cfg({"bus": "fake", "address": 0x22, "retries": 2,
                          "tests": [{"steps": ["probe"]}]})[0]
        self.assertEqual(r.verdict, "FAIL")
        self.assertIn("NACK", r.error)
        # missing adapter -> ERROR with a hint
        r = self.run_cfg({"bus": "/dev/i2c-99", "address": 0x10,
                          "tests": [{"steps": ["probe"]}]})[0]
        self.assertEqual(r.verdict, "ERROR")
        self.assertIn("/dev/i2c-99", r.error)

    def test_poll_timeout(self):
        r = self.run_cfg({"bus": "fake,r40=00", "address": 0x10, "retry_delay_ms": 0.2,
                          "tests": [{"steps": ["done=p:0x40:0xff:0x01:20"]}]})[0]
        self.assertEqual(r.verdict, "FAIL")
        self.assertIn("poll timeout", r.error)

    def test_cli(self):
        from ctt.cli import main
        with tempfile.TemporaryDirectory() as d:
            rc = main(["mfi", str(ROOT / "configs" / "mfi_cp.example.yaml"), "--out", d, "-q",
                       "--repeat-scale", "0.2"])
            self.assertEqual(rc, 0)
            summary = json.loads(next(Path(d).glob("*/summary.json")).read_text())
            self.assertEqual(summary["passed"], 5)
            self.assertEqual(summary["tests"][0]["transport"], "i2c")


if __name__ == "__main__":
    unittest.main()
