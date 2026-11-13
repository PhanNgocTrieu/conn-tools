"""UDP transport (with injected loss) and WiFi link parsing."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
sys.path.insert(0, str(ROOT / "app" / "src"))

from ctt.interfaces import wifi  # noqa: E402
from ctt.core.agent import find_host_agent  # noqa: E402
from ctt.core.executor import LocalExecutor  # noqa: E402
from ctt.testing.runner import Runner, TestSpec, evaluate, summarize  # noqa: E402

IW = """### wlan0
Connected to 1c:61:b4:aa:bb:cc (on wlan0)
	SSID: lab-5g
	freq: 5180
	RX: 123456 bytes (789 packets)
	signal: -47 dBm
	rx bitrate: 866.7 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 2
	tx bitrate: 585.0 MBit/s VHT-MCS 7 80MHz VHT-NSS 2
procwireless wlan0: 0000   63.  -47.  -256        0      0      0      0      0        0
### p2p0
Not connected.
"""
PROC_ONLY = """### wlan1
procwireless wlan1: 0000   45.  -65.  -256        0      0      0      0      0        0
"""


class WifiParseTest(unittest.TestCase):
    def test_iw(self):
        info = wifi.parse(IW)
        w = info["wlan0"]
        self.assertTrue(w["connected"])
        self.assertEqual((w["ssid"], w["signal_dbm"], w["band"]), ("lab-5g", -47, "5GHz"))
        self.assertEqual((w["rx_bitrate_mbps"], w["tx_bitrate_mbps"]), (866.7, 585.0))
        self.assertIn("VHT-MCS 9", w["rx_rate_info"])
        self.assertFalse(info["p2p0"]["connected"])
        self.assertEqual(wifi.pick(info), w)
        self.assertEqual(wifi.pick(info, "p2p0"), {"connected": False})

    def test_proc_fallback(self):
        w = wifi.parse(PROC_ONLY)["wlan1"]
        self.assertEqual((w["signal_dbm"], w["link_quality"]), (-65, 45.0))

    def test_collect_never_raises(self):
        self.assertIsInstance(wifi.collect(LocalExecutor()), dict)


class UdpMetricsTest(unittest.TestCase):
    def test_summarize_and_expect(self):
        res = {"lossy": True,
               "up": {"receiver": {"mbps": 9.9, "lost": 5, "crc_errors": 0, "loss_pct": 0.5,
                                   "jitter_us": 120.0}},
               "down": {"receiver": {"mbps": 9.8, "lost": 20, "crc_errors": 0, "loss_pct": 2.0,
                                     "jitter_us": 80.0}}}
        m = summarize(res)
        self.assertEqual((m["loss_pct"], m["jitter_us"]), (2.0, 120.0))
        by = {c["check"]: c["ok"] for c in evaluate(m, {"loss_pct_max": 1, "jitter_us_max": 200})}
        self.assertFalse(by["loss_pct_max"])
        self.assertTrue(by["jitter_us_max"])


class UdpIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            agent = find_host_agent()
        except FileNotFoundError:
            raise unittest.SkipTest("ctt-agent not built")
        local = LocalExecutor()
        cls.runner = Runner(local, local, agent, agent)

    def run_spec(self, **kw):
        d = {"transport": "udp", "duration": "1s", "interval": 0, "rate": "20M", **kw}
        d.setdefault("name", f"udp-{d.get('mode', 'tx')}")
        return self.runner.run(TestSpec.from_dict(d))

    def test_clean_link_all_modes(self):
        for i, mode in enumerate(("tx", "rx", "bidir", "echo")):
            r = self.run_spec(mode=mode, port=27000 + i, expect={"loss_pct_max": 0.5}
                              if mode != "echo" else {"echo_loss_pct_max": 0.5})
            self.assertEqual(r.verdict, "PASS", f"{mode}: {r.error} {r.checks}")
            if mode != "echo":
                self.assertTrue(r.result["lossy"])
                self.assertIn("jitter_us", r.metrics)

    def test_injected_loss_is_measured_and_survived(self):
        """20% of ALL datagrams (incl. START/STOP/STATS) dropped on both sides."""
        for i, mode in enumerate(("tx", "rx", "bidir", "echo")):
            r = self.run_spec(mode=mode, port=27100 + i, transport_opts={"drop": 0.2},
                              echo_interval_ms=5)
            self.assertNotEqual(r.verdict, "ERROR", f"{mode}: {r.error}")
            key = "echo_loss_pct" if mode == "echo" else "loss_pct"
            self.assertGreater(r.metrics[key], 5, f"{mode}: loss not measured {r.metrics}")
            if mode != "echo":
                self.assertLess(r.metrics["loss_pct"], 35)

    def test_loss_expectation_fails(self):
        r = self.run_spec(mode="tx", port=27200, transport_opts={"drop": 0.1},
                          expect={"loss_pct_max": 1})
        self.assertEqual(r.verdict, "FAIL")


if __name__ == "__main__":
    unittest.main()
