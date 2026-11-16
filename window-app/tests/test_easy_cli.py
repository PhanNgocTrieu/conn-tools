"""The easy CLI layer: saved settings, catalogue lookup, `ctt setup` / `ctt test`."""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]   # repository root
sys.path.insert(0, str(ROOT / "window-app" / "src"))

from ctt.cli import settings  # noqa: E402
from ctt.cli.app import build_parser, main  # noqa: E402
from ctt.testing import catalog  # noqa: E402


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = {"CTT_CONFIG": os.path.join(self.tmp.name, "settings.yaml")}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        os.environ.pop("CTT_DUT", None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def parse(self, *argv):
        args = build_parser().parse_args(list(argv))
        settings.apply(args)
        return args

    def test_roundtrip(self):
        settings.save({"dut": "root@10.0.0.2", "wifi_addr": "10.1.1.1", "bogus": "x"})
        self.assertEqual(settings.load(), {"dut": "root@10.0.0.2", "wifi_addr": "10.1.1.1"})
        self.assertEqual(settings.data_addr("wifi"), "10.1.1.1")
        self.assertIsNone(settings.data_addr("usb"))

    def test_precedence(self):
        settings.save({"dut": "root@10.0.0.2", "dut_agent": "/usr/bin/ctt-agent", "ssh_port": "2222"})
        a = self.parse("board", "status")
        self.assertEqual((a.dut, a.dut_agent, a.ssh_port), ("root@10.0.0.2", "/usr/bin/ctt-agent", 2222))
        os.environ["CTT_DUT"] = "root@10.0.0.9"                       # env beats the file
        self.assertEqual(self.parse("board", "status").dut, "root@10.0.0.9")
        a = self.parse("board", "status", "--dut", "local")           # option beats everything
        self.assertEqual(a.dut, "local")
        self.assertIsNone(a.dut_agent)                                # board settings skipped

    def test_commands_without_board_untouched(self):
        settings.save({"dut": "root@10.0.0.2", "dut_agent": "/usr/bin/ctt-agent"})
        a = self.parse("selftest")
        self.assertIsNone(a.dut_agent)
        self.assertFalse(hasattr(a, "dut"))

    def test_setup_and_test_list(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["setup", "dut=root@10.0.0.2", "usb_addr=192.168.7.2"]), 0)
            self.assertEqual(settings.load()["usb_addr"], "192.168.7.2")
            self.assertEqual(main(["setup", "usb_addr="]), 0)
            self.assertNotIn("usb_addr", settings.load())
            self.assertEqual(main(["test"]), 0)
            self.assertEqual(main(["test", "voip-call", "--list"]), 0)
        self.assertIn("wifi/carplay-wireless", out.getvalue())
        self.assertIn("call-latency", out.getvalue())

    def test_run_accepts_target_addr(self):
        self.assertEqual(self.parse("run", "--target-addr", "10.1.1.1").target_addr, "10.1.1.1")


class CatalogTest(unittest.TestCase):
    def test_lookup(self):
        keys = {e.key for e in catalog.entries()}
        self.assertTrue({"wifi/carplay-wireless", "usb/carplay-wired", "tcp_smoke"} <= keys)
        self.assertEqual(catalog.find("voip-call").key, "wifi/voip-call")
        self.assertEqual(catalog.find("wifi/voip_call").key, "wifi/voip-call")
        self.assertEqual(catalog.find("tcp-smoke").key, "tcp_smoke")
        self.assertTrue(all(e.title for e in catalog.entries()))
        with self.assertRaisesRegex(ValueError, "several"):
            catalog.find("carplay")
        with self.assertRaisesRegex(ValueError, "no test"):
            catalog.find("does-not-exist")


if __name__ == "__main__":
    unittest.main()
