"""Traffic models (v3): burst streams, parallel groups, the USB / WiFi protocol catalogue."""

import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
sys.path.insert(0, str(ROOT / "window-app" / "src"))

from ctt.core.executor import LocalExecutor  # noqa: E402
from ctt.testing.profile import expand, load_profile  # noqa: E402
from ctt.testing.runner import Runner, TestSpec, evaluate, summarize  # noqa: E402

AGENT = Path(os.environ.get("CTT_AGENT", ROOT / "build/ctt-agent-linux/ctt-agent"))
CATALOGUE = ROOT / "config" / "profiles"


class SpecTest(unittest.TestCase):
    def test_burst_fields(self):
        s = TestSpec.from_dict({"name": "v", "mode": "tx", "burst": "16K", "burst_rate": 60})
        self.assertEqual((s.burst, s.burst_rate), (16384, 60.0))
        for bad in ({"burst": "1K"},                                   # rate missing
                    {"burst": "1K", "burst_rate": 10, "rate": "1M"},   # both models
                    {"burst": "1K", "burst_rate": 10, "mode": "echo"},
                    {"burst": "1K", "burst_rate": 0}):
            with self.assertRaises(ValueError, msg=bad):
                TestSpec.from_dict({"name": "x", "mode": "tx", **bad})

    def test_burst_metrics_and_expectations(self):
        result = {"up": {"receiver": {"mbps": 8.1, "bytes": 1, "bursts": {
                      "count": 600, "late": 3, "on_time_pct": 99.5, "max_gap_ms": 70.0, "jitter_ms": 1.2}}},
                  "down": {"receiver": {"mbps": 0.2, "bytes": 1, "bursts": {
                      "count": 300, "late": 0, "on_time_pct": 100.0, "max_gap_ms": 21.0, "jitter_ms": 0.1}}}}
        m = summarize(result)
        self.assertEqual((m["burst_on_time_pct"], m["burst_late"], m["burst_max_gap_ms"]), (99.5, 3, 70.0))
        checks = {c["check"]: c["ok"] for c in evaluate(m, {"burst_on_time_pct_min": 99,
                                                             "burst_max_gap_ms_max": 50})}
        self.assertTrue(checks["burst_on_time_pct_min"])
        self.assertFalse(checks["burst_max_gap_ms_max"])

    def test_shared_expectations_only_apply_where_they_can(self):
        echo = TestSpec.from_dict({"name": "lat", "transport": "udp", "mode": "echo",
                                   "expect": {"loss_pct_max": 1, "burst_on_time_pct_min": 99,
                                              "rtt_p99_us_max": 100000, "up_mbps_min": 1}})
        keys = {c["check"] for c in evaluate({"rtt_p99_us": 70.0}, echo.expect, echo)}
        self.assertEqual(keys, {"crc_errors_max", "rtt_p99_us_max"})
        rx = TestSpec.from_dict({"name": "v", "mode": "rx", "expect": {"down_mbps_min": 5, "up_mbps_min": 5}})
        keys = {c["check"] for c in evaluate({"down_mbps": 6.0, "crc_errors": 0}, rx.expect, rx)}
        self.assertEqual(keys, {"crc_errors_max", "down_mbps_min"})
        # without a spec (old callers) every expectation is checked, missing metric = FAIL
        self.assertFalse(all(c["ok"] for c in evaluate({}, {"up_mbps_min": 1})))


class ParallelProfileTest(unittest.TestCase):
    def test_streams_get_group_and_ports(self):
        _, specs = expand({"defaults": {"duration": "5s", "expect": {"lost_max": 0}},
                           "tests": [{"name": "solo", "mode": "tx"},
                                     {"name": "cp", "protocol": "CarPlay", "parallel": [
                                         {"name": "video", "mode": "tx", "burst": "20K", "burst_rate": 60,
                                          "expect": {"burst_on_time_pct_min": 99}},
                                         {"name": "mic", "transport": "udp", "mode": "rx",
                                          "burst": 640, "burst_rate": 50},
                                         {"name": "ctl", "mode": "echo", "port": 6000}]}]})
        self.assertEqual([s.name for s in specs], ["solo", "cp/video", "cp/mic", "cp/ctl"])
        self.assertEqual([s.group for s in specs], [None, "cp", "cp", "cp"])
        self.assertEqual([s.port for s in specs[1:]], [5201, 5202, 6000])
        self.assertEqual(specs[1].protocol, "CarPlay")
        self.assertEqual(specs[1].expect, {"lost_max": 0, "burst_on_time_pct_min": 99})   # merged

    def test_usb_bulk_cannot_run_in_parallel(self):
        with self.assertRaises(ValueError):
            expand({"tests": [{"name": "aa", "parallel": [{"transport": "usb", "mode": "tx"},
                                                           {"transport": "usb", "mode": "rx"}]}]})

    def test_catalogue_covers_the_protocols(self):
        names = {p.stem for p in (CATALOGUE / "usb").glob("*.yaml")} | \
                {p.stem for p in (CATALOGUE / "wifi").glob("*.yaml")}
        for n in ("carplay-wired", "androidauto-wired", "uac-audio", "uvc-video", "usb-classes",
                  "ncm-network", "carplay-wireless", "androidauto-wireless", "miracast", "voip-call",
                  "media-streaming", "file-transfer", "interactive-latency"):
            self.assertIn(n, names)
        for p in sorted(CATALOGUE.rglob("*.yaml")):
            _, specs = load_profile(p)
            self.assertTrue(specs, p)


@unittest.skipUnless(AGENT.exists(), f"host agent not built: {AGENT}")
class EndToEndTest(unittest.TestCase):
    def test_parallel_burst_streams_over_loopback(self):
        _, specs = expand({"defaults": {"duration": "2s", "port": None},
                           "tests": [{"name": "g", "parallel": [
                               {"name": "video", "mode": "tx", "block": 1400, "burst": "16K", "burst_rate": 60,
                                "expect": {"burst_on_time_pct_min": 95}},
                               {"name": "audio", "transport": "udp", "mode": "rx", "block": 960,
                                "burst": 1920, "burst_rate": 100, "expect": {"loss_pct_max": 1}},
                               {"name": "ctl", "mode": "echo", "block": 64}]}]},
                          {"port": None})
        for s in specs:      # private ports so parallel test runs do not collide
            s.port += 1100
        runner = Runner(LocalExecutor(), LocalExecutor(), str(AGENT), str(AGENT))
        t0 = time.monotonic()
        results = runner.run_group(specs)
        wall = time.monotonic() - t0
        for r in results:
            self.assertEqual(r.verdict, "PASS", (r.spec.name, r.error, r.checks))
        video, audio, _ = results
        self.assertAlmostEqual(video.metrics["up_bursts"], 120, delta=3)
        blocks = -(-16384 // 1400)      # a burst is rounded up to whole blocks: 12 x 1400 B
        self.assertAlmostEqual(video.metrics["up_mbps"], blocks * 1400 * 8 * 60 / 1e6, delta=0.5)
        self.assertAlmostEqual(audio.metrics["down_bursts"], 200, delta=4)
        self.assertLess(wall, sum(r.elapsed_s for r in results))    # really ran at the same time


if __name__ == "__main__":
    unittest.main()
