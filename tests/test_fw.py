"""vendor firmware awareness: status parsing, collision warnings, WiFi AP link info."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctt.firmware import vendor_daemons as fw
from ctt.interfaces import wifi  # noqa: E402

STATUS = """svc.usb-daemon=active
svc.bt-daemon=active
svc.bluetooth=active
svc.wifi-daemon=active
svc.hostapd@ap=active
svc.wpa-supplicant=inactive
svc.audio-daemon=
wifi.mode=1
wifi.enabled=true
wifi.ap.Enabled=true
wifi.ap.SSID=AP-5G
wifi.ap.Channel=36
wifi.ap.Frequency=5180
wifi.ap.ClientCount=1
wifi.ap.Passphrase=secret123
wifi.p2p.Enabled=false
bt.Powered=true
bt.Address=00:11:22:33:44:55
bt.Name=test-board
bt.paired_raw=bsa(ssssnbbbbbb) true "ok, 5 devices" 5 "/org/bluez/hci0/dev_X" "X" "public" "P" 0 true true true false false false
usb.api=1
usb.role:/sys/bus/platform/devices/12340000.usb_dwc2/dwc2_drdmode=host
usb.udc=
usbconf.port.0.name=front
usbconf.port.0.udc=12340000.usb_dwc2
usbconf.port.0.carplay=enabled
mfi.lib=/usr/lib/libvendor-mfi.so.1
mfi.testapp=
"""


class FwTest(unittest.TestCase):
    def test_parse_status(self):
        st = fw.parse_status(STATUS)
        self.assertTrue(fw.has_vendor_firmware(st))
        self.assertNotIn("audio-daemon", st["services"])
        self.assertEqual(st["wifi"]["mode"], "AP")
        self.assertEqual(st["wifi"]["ap"]["SSID"], "AP-5G")
        self.assertEqual(st["bt"]["paired"], 5)
        self.assertEqual(st["usb"]["udc"], [])
        self.assertEqual(st["usb"]["ports"]["0"]["udc"], "12340000.usb_dwc2")
        text = fw.format_status(st)
        self.assertIn("ssid=AP-5G", text)
        self.assertIn("12340000.usb_dwc2=host", text)

    def test_warnings(self):
        st = fw.parse_status(STATUS)
        w = fw.warnings_for(st, {"usb"})
        self.assertEqual(len(w), 1)
        self.assertIn("ctt usb gadget up", w[0])
        w = fw.warnings_for(st, set(), bt_pairing=True)
        self.assertTrue(any("--dut-accept bm" in x for x in w))
        self.assertTrue(any("CapacityReached" in x for x in w))
        self.assertTrue(any("AP mode" in x for x in fw.warnings_for(st, {"tcp"})))
        self.assertEqual(fw.warnings_for({}, {"usb", "tcp"}, True), [])
        self.assertEqual(fw.warnings_for(fw.parse_status(""), {"usb", "tcp"}, True), [])

    def test_count_paired(self):
        self.assertEqual(fw._count_paired('bsa(ssssnbbbbbb) true "ok" 0'), 0)
        self.assertEqual(fw._count_paired('bsa(ssssnbbbbbb) false "say \\"x\\"" 2 ...'), 2)
        self.assertIsNone(fw._count_paired("Failed to get"))


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
