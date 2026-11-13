"""Board profile: loading, generic status parsing, warnings, profile pairing commands."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctt.core.executor import LocalExecutor  # noqa: E402
from ctt.interfaces import wifi  # noqa: E402
from ctt.platform import board  # noqa: E402

STATUS = """svc.bluetooth=active
svc.vendor-usbd=active
svc.vendor-wifid=inactive
svc.unknown=
bt.Address=00:11:22:33:44:55
bt.Powered=yes
bt.Name=test-board
bt.paired=5
usb.role:/sys/bus/platform/devices/12340000.usb_dwc2/dwc2_drdmode=host
usb.udc=
wifi.if.wlan0=AP
wifi.if.p2p-dev-wlan0=P2P-device
x.daemon-version=1.2.3
"""

PROFILE = {
    "name": "test-board",
    "services": ["bluetooth", "vendor-usbd", "vendor-wifid"],
    "bluetooth": {"max_bonds": 5, "pairing": {"setup": ["echo setup"],
                                              "confirm": "echo ctt-confirm; echo ctt-confirm; sleep 5",
                                              "stop": "true"}},
    "usb": {"gadget_stop_services": ["vendor-usbd"]},
    "conflicts": [{"service": "vendor-usbd", "affects": ["bt-pairing"], "message": "vendor-usbd also does BT"}],
    "commands": {"hello": "echo hello from the board"},
}


class StatusTest(unittest.TestCase):
    def test_parse_and_format(self):
        st = board.parse_status(STATUS)
        self.assertEqual(st["services"], {"bluetooth": "active", "vendor-usbd": "active", "vendor-wifid": "inactive"})
        self.assertEqual((st["bt"]["Address"], st["bt"]["paired"]), ("00:11:22:33:44:55", 5))
        self.assertEqual(st["wifi"]["mode"], "AP")
        self.assertEqual(st["usb"]["udc"], [])
        self.assertEqual(st["extra"]["daemon-version"], "1.2.3")
        text = board.format_status(st)
        self.assertIn("12340000.usb_dwc2=host", text)
        self.assertIn("wlan0=AP", text)

    def test_status_runs_on_any_linux(self):
        """The generic status script only uses standard tools and never fails."""
        st = board.status(LocalExecutor(), board.BoardProfile())
        self.assertIn("services", st)
        self.assertEqual(st["profile"], board.BoardProfile().name)


class ProfileTest(unittest.TestCase):
    def test_load_yaml_json_and_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            j = Path(d) / "b.json"
            j.write_text(json.dumps(PROFILE))
            p = board.load_profile(j)
            self.assertEqual((p.name, p.max_bonds, p.gadget_stop_services), ("test-board", 5, ["vendor-usbd"]))
            self.assertTrue(p.pairing.custom)
            y = Path(d) / "b.yaml"
            y.write_text("name: y\nservices: [a, b]\nbluetooth:\n  pairing:\n    setup: echo one\n")
            p = board.load_profile(y)
            self.assertEqual((p.services, p.pairing.setup), (["a", "b"], ["echo one"]))
            with self.assertRaises(ValueError):
                board.BoardProfile.from_dict({"conflicts": [{"service": "x", "affects": ["radio"]}]})
            with self.assertRaises(ValueError):
                board.load_profile(Path(d) / "missing.yaml")
        import os
        old = os.environ.get("CTT_BOARD_PROFILE")
        os.environ["CTT_BOARD_PROFILE"] = "none"
        try:
            self.assertEqual(board.load_profile().name, board.BoardProfile().name)
        finally:
            if old is None:
                os.environ.pop("CTT_BOARD_PROFILE")
            else:
                os.environ["CTT_BOARD_PROFILE"] = old
        # no explicit profile and nothing on disk: defaults; a board.yaml in the cwd is picked up
        old_cwd, old_env = os.getcwd(), os.environ.pop("CTT_BOARD_PROFILE", None)
        try:
            with tempfile.TemporaryDirectory() as d:
                os.chdir(d)
                Path(d, "board.yaml").write_text("name: found-in-cwd\n")
                self.assertEqual(board.load_profile().name, "found-in-cwd")
        finally:
            os.chdir(old_cwd)
            if old_env is not None:
                os.environ["CTT_BOARD_PROFILE"] = old_env
        plain = board.BoardProfile()
        self.assertFalse(plain.pairing.custom)
        self.assertEqual(plain.services, ["bluetooth"])

    def test_warnings(self):
        p = board.BoardProfile.from_dict(PROFILE)
        st = board.parse_status(STATUS)
        w = board.warnings_for(st, {"usb"}, profile=p)
        self.assertTrue(any("vendor-usbd" in x and "ctt usb gadget up" in x for x in w))
        w = board.warnings_for(st, set(), bt_pairing=True, profile=p)
        self.assertTrue(any("5 bonds (limit 5" in x for x in w))
        self.assertIn("vendor-usbd also does BT", w)
        self.assertTrue(any("AP mode" in x for x in board.warnings_for(st, {"tcp"}, profile=p)))
        plain = board.BoardProfile()
        self.assertEqual([x for x in board.warnings_for(st, {"usb"}, True, plain) if "AP" not in x], [])
        self.assertEqual(board.warnings_for({}, {"usb", "tcp"}, True, p), [])

    def test_commands_and_acceptor(self):
        p = board.BoardProfile.from_dict(PROFILE)
        ex = LocalExecutor()
        self.assertEqual(board.run_command(ex, p, "hello"), "hello from the board")
        with self.assertRaises(ValueError):
            board.run_command(ex, p, "nope")
        acc = board.ProfileAcceptor(ex, p.pairing)
        with acc:
            import time
            time.sleep(0.5)
        self.assertEqual(acc.confirmed, 2)


AP_LINK = """### wlan0
info Interface wlan0
info \ttype AP
info \tchannel 36 (5180 MHz), width: 80 MHz, center1: 5210 MHz
Not connected.
sta Station aa:bb:cc:dd:ee:01 (on wlan0)
sta \tsignal:  \t-62 [-64, -65] dBm
sta \ttx bitrate:\t433.3 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 1
sta \trx bitrate:\t390.0 MBit/s
sta Station aa:bb:cc:dd:ee:02 (on wlan0)
sta \tsignal:  \t-45 [-46, -47] dBm
"""


class WifiApTest(unittest.TestCase):
    def test_ap_parse_and_pick(self):
        info = wifi.parse(AP_LINK)
        w = info["wlan0"]
        self.assertEqual((w["mode"], w["channel"], w["width_mhz"], w["band"]), ("AP", 36, 80, "5GHz"))
        self.assertTrue(w["connected"])
        self.assertEqual(len(w["stations"]), 2)
        best = wifi.pick(info)
        self.assertEqual((best["station"], best["signal_dbm"]), ("aa:bb:cc:dd:ee:02", -45))
        one = wifi.pick(info, station="AA:BB:CC:DD:EE:01")
        self.assertEqual((one["signal_dbm"], one["tx_bitrate_mbps"], one["rx_bitrate_mbps"]),
                         (-62, 433.3, 390.0))

    def test_ap_without_clients(self):
        info = wifi.parse("### wlan0\ninfo \ttype AP\nNot connected.\n")
        self.assertFalse(info["wlan0"]["connected"])
        self.assertNotIn("station", wifi.pick(info))


if __name__ == "__main__":
    unittest.main()
