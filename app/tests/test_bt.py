"""Bluetooth tests: bluetoothctl driver (against a fake), BT runner logic, and
real kernel RFCOMM / packet transports where available.

    python3 -m unittest discover -s tests
"""

import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
sys.path.insert(0, str(ROOT / "app" / "src"))

from ctt.core.agent import find_host_agent  # noqa: E402
from ctt.bluetooth.bluetoothctl import (BluetoothCtl, BtError, default_controller, pair_once,  # noqa: E402
                    pair_stress, prepare_acceptor)
from ctt.core.executor import LocalExecutor  # noqa: E402
from ctt.testing.runner import Runner, TestSpec  # noqa: E402

FAKE = [sys.executable, str(ROOT / "app" / "tests" / "fakes" / "fake_bluetoothctl.py")]
DUT = "AA:BB:CC:DD:EE:01"


def fake_ctl(**env):
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        ctl = BluetoothCtl(LocalExecutor(), "fake", command=FAKE)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    ctl.wait_ready()
    return ctl


class BluetoothCtlTest(unittest.TestCase):
    def test_controller_and_prepare(self):
        with fake_ctl() as ctl:
            self.assertEqual(ctl.controllers(),
                             [{"address": "11:22:33:44:55:66", "name": "fake-ctrl",
                               "default": True}])
            self.assertEqual(default_controller(ctl), "11:22:33:44:55:66")
            prepare_acceptor(ctl, "NoInputNoOutput")

    def test_scan_info_pair_trust_remove(self):
        with fake_ctl(FAKE_BT_DEVICES=f"{DUT}=test-board,AA:BB:CC:DD:EE:02=Phone") as ctl:
            ctl.register_agent("DisplayYesNo")
            self.assertIsNone(ctl.info(DUT))
            found = ctl.scan(3, until_addr=DUT)
            self.assertEqual(found.get(DUT), "test-board")
            info = ctl.info(DUT)
            self.assertEqual(info["Name"], "test-board")
            self.assertEqual(info["Paired"], "no")
            self.assertEqual(len(info["UUIDs"]), 2)

            ok, msg = ctl.pair(DUT)
            self.assertTrue(ok, msg)
            self.assertEqual(ctl.prompts, ["Confirm passkey 123456 (yes/no)"])  # auto-answered
            ctl.trust(DUT)
            info = ctl.info(DUT)
            self.assertEqual((info["Paired"], info["Trusted"]), ("yes", "yes"))
            self.assertEqual(ctl.pair(DUT), (True, "already paired"))
            self.assertEqual(ctl.connect(DUT)[0], True)

            self.assertTrue(ctl.remove(DUT))
            self.assertFalse(ctl.remove(DUT))
            self.assertIsNone(ctl.info(DUT))

    def test_pair_failures(self):
        with fake_ctl(FAKE_BT_PAIR="fail") as ctl:
            a = pair_once(ctl, DUT, scan_timeout=3, pair_timeout=5)
            self.assertFalse(a.ok)
            self.assertIn("AuthenticationFailed", a.message)
        with fake_ctl(FAKE_BT_DEVICES="") as ctl:
            a = pair_once(ctl, DUT, scan_timeout=0.5, pair_timeout=5)
            self.assertFalse(a.ok)
            self.assertIn("not found", a.message)

    def test_pair_stress(self):
        with fake_ctl() as ctl:
            ctl.register_agent()
            seen = []
            res = pair_stress(ctl, DUT, 3, scan_timeout=3, connect=True, settle_s=0,
                              on_attempt=seen.append)
            self.assertEqual(res.success, 3)
            self.assertEqual([a.index for a in seen], [1, 2, 3])
            self.assertTrue(all(a.connect_ok for a in res.attempts))
            s = res.summary()
            self.assertEqual(s["success_rate"], 1.0)
            self.assertIn("pair_s_avg", s)
            json.dumps(res.to_dict())

    def test_timeout_and_dead_process(self):
        with fake_ctl() as ctl:
            with self.assertRaises(BtError):
                ctl.expect([r"never printed"], timeout=0.3)
        ctl = BluetoothCtl(LocalExecutor(), "dead", command=[sys.executable, "-c", "pass"])
        with self.assertRaises(BtError):
            ctl.wait_ready(timeout=5)
        ctl.close()

    def test_cli(self):
        from ctt.cli import main
        btctl = " ".join(FAKE)
        self.assertEqual(main(["bt", "list", "--btctl", btctl]), 0)
        self.assertEqual(main(["bt", "pair", DUT, "--btctl", btctl, "--scan-timeout", "3"]), 0)
        with tempfile.TemporaryDirectory() as d:
            rc = main(["bt", "pair-stress", DUT, "-n", "2", "--btctl", btctl, "--out", d,
                       "--scan-timeout", "3"])
            self.assertEqual(rc, 0)
            report = json.loads(next(Path(d).glob("*/result.json")).read_text())
            self.assertEqual(report["summary"]["success"], 2)


class BtRunnerTargetTest(unittest.TestCase):
    """Target endpoint derivation for BT transports (no hardware needed)."""

    def setUp(self):
        local = LocalExecutor()
        self.runner = Runner(local, local, "agent", "agent")
        self.local = local

    def test_rfcomm_from_listening_event(self):
        spec = TestSpec(name="r", transport="rfcomm")
        self.assertEqual(self.runner._listen(spec), "/0")
        ev = {"event": "listening", "bdaddr": "00:1A:7D:DA:71:13", "channel": 5}
        self.assertEqual(self.runner._target(spec, self.local, ev), "00:1A:7D:DA:71:13/5")

    def test_rfcomm_unknown_address(self):
        spec = TestSpec(name="r", transport="rfcomm", port=3)
        self.assertEqual(self.runner._listen(spec), "/3")
        ev = {"bdaddr": "00:00:00:00:00:00", "channel": 3}
        with self.assertRaisesRegex(ValueError, "set 'bdaddr'"):
            self.runner._target(spec, self.local, ev)
        spec.bdaddr = "00:1A:7D:DA:71:13"
        self.assertEqual(self.runner._target(spec, self.local, ev), "00:1A:7D:DA:71:13/3")

    def test_l2cap_and_explicit(self):
        spec = TestSpec(name="l", transport="l2cap", port=0x1003)
        self.assertEqual(self.runner._listen(spec), "/4099")
        ev = {"bdaddr": "00:1A:7D:DA:71:13", "psm": 4099}
        self.assertEqual(self.runner._target(spec, self.local, ev), "00:1A:7D:DA:71:13/4099")
        spec.endpoint = "AA:BB:CC:DD:EE:FF/0x1001"
        self.assertEqual(self.runner._target(spec, self.local, ev), spec.endpoint)


def _have_rfcomm() -> bool:
    try:
        socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM).close()
        return True
    except (OSError, AttributeError):
        return False


class AgentTransportIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.agent = find_host_agent()
        except FileNotFoundError:
            raise unittest.SkipTest("ctt-agent not built")
        local = LocalExecutor()
        cls.runner = Runner(local, local, cls.agent, cls.agent)

    def test_unix_seqpacket_l2cap_like(self):
        """Full session over SOCK_SEQPACKET with an L2CAP-sized MTU."""
        for mode in ("tx", "rx", "bidir", "echo"):
            spec = TestSpec.from_dict({
                "name": f"seq-{mode}", "transport": "unix", "mode": mode, "duration": "1s",
                "port": 4242, "block": 990, "interval": 0,
                "transport_opts": {"type": "seqpacket", "mtu": 672},
                "expect": {"lost_max": 0}})
            r = self.runner.run(spec)
            self.assertEqual(r.verdict, "PASS", f"{mode}: {r.error}")

    @unittest.skipUnless(_have_rfcomm(), "kernel without AF_BLUETOOTH/RFCOMM")
    def test_rfcomm_server_without_adapter(self):
        # The server really listens on a kernel RFCOMM socket; with no adapter
        # the address is unknown -> clear error, no hang.
        spec = TestSpec(name="rf", transport="rfcomm", duration=1)
        r = self.runner.run(spec)
        self.assertEqual(r.verdict, "ERROR")
        if "bdaddr" not in r.error:  # a real adapter is attached
            self.skipTest(f"adapter present: {r.error}")
        self.assertIn("set 'bdaddr'", r.error)

        spec.bdaddr = "00:11:22:33:44:55"
        r = self.runner.run(spec)
        self.assertEqual(r.verdict, "ERROR")
        self.assertRegex(r.error, r"rfcomm connect 00:11:22:33:44:55/\d+")


if __name__ == "__main__":
    unittest.main()
