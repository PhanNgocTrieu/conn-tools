"""Bluetooth app layers above the transport: settings, service, console menu."""

import builtins
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ctt.bluetooth.pairing import Device, PairingBackend  # noqa: E402
from ctt.btapp.service import BtTestService, ServiceError  # noqa: E402
from ctt.btapp.settings import AppSettings, SettingsStore  # noqa: E402


class FakePairing(PairingBackend):
    name = "fake"

    def __init__(self):
        self.paired_with = []

    def scan(self, seconds=10):
        return [Device("AA:BB:CC:DD:EE:01", "test-board"), Device("AA:BB:CC:DD:EE:02", "Phone")]

    def paired(self):
        return [Device(a, "", True) for a in self.paired_with]

    def pair(self, address, on_pin=None, timeout=40):
        if on_pin:
            on_pin("123456")
        self.paired_with.append(address)
        return True, "Pairing successful"

    def unpair(self, address):
        return True, "removed"


class FakeSshMixin:
    """PATH with a fake ssh (runs locally) and a fake board agent."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        bindir = Path(self.tmp.name)
        (bindir / "ssh").write_text(f"#!/bin/sh\nexec {sys.executable} {ROOT}/tests/fakes/fake_ssh.py \"$@\"\n")
        (bindir / "ssh").chmod(0o755)
        self.agent = bindir / "ctt-agent"
        self.agent.write_text(f"#!/bin/sh\nexec {sys.executable} {ROOT}/tests/fakes/fake_agent.py \"$@\"\n")
        self.agent.chmod(0o755)
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{bindir}:{self.old_path}"

    def tearDown(self):
        os.environ["PATH"] = self.old_path
        self.tmp.cleanup()


class SettingsTest(unittest.TestCase):
    def test_roundtrip_and_channels(self):
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "c.json")
            self.assertEqual(store.load(), AppSettings())
            s = AppSettings(board="root@1.2.3.4", bt_addr="aa:bb:cc:dd:ee:01", channel="7")
            store.save(s)
            (Path(d) / "c.json").write_text((Path(d) / "c.json").read_text()[:-2] + ', "old_key": 1}')
            loaded = store.load()
            self.assertEqual((loaded.board, loaded.address(), loaded.server_channel()),
                             ("root@1.2.3.4", "AA:BB:CC:DD:EE:01", 7))
        self.assertEqual(AppSettings(channel="auto").server_channel(), 0)
        self.assertEqual(AppSettings(channel="auto", no_ssh=True).server_channel(), 22)


class ServiceTest(FakeSshMixin, unittest.TestCase):
    def service(self, **kw):
        svc = BtTestService(AppSettings(**kw), FakePairing())
        return svc

    def test_no_ssh_target_needs_address(self):
        svc = self.service(no_ssh=True)
        with self.assertRaises(ServiceError):
            svc.server_target()
        svc.settings.bt_addr = "aa:bb:cc:dd:ee:01"
        t = svc.server_target()
        self.assertEqual((t.address, t.channel, t.pipe), ("AA:BB:CC:DD:EE:01", 22, None))

    def test_ssh_server_and_pipe_targets(self):
        svc = self.service(board="root@board")
        svc.board().agent = str(self.agent)
        svc.board().find_agent = lambda: "ctt-agent fake"
        t = svc.server_target()
        self.assertEqual((t.address, t.channel), ("00:11:22:33:44:55", 5))
        svc.settings.bt_addr = "AA:BB:CC:DD:EE:09"       # explicit address wins
        p = svc.pipe_target()
        self.assertEqual((p.address, p.channel), ("AA:BB:CC:DD:EE:09", 6))
        self.assertIsNotNone(p.pipe)
        p.pipe.close()
        svc.close()

    def test_missing_agent_is_reported(self):
        svc = self.service(board="root@board")
        svc.board().find_agent = lambda: None
        with self.assertRaisesRegex(ServiceError, "Deploy agent"):
            svc.server_target()

    def test_pair_without_board_acceptance(self):
        svc = self.service(no_ssh=True)
        pins = []
        self.assertEqual(svc.pair("aa:bb:cc:dd:ee:01", True, on_pin=pins.append),
                         (True, "Pairing successful"))
        self.assertEqual((pins, svc.pairing.paired_with), (["123456"], ["AA:BB:CC:DD:EE:01"]))


class ConsoleMenuTest(unittest.TestCase):
    def run_menu(self, keys):
        from ctt.btapp.console.menu import ConsoleMenu
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "c.json")
            menu = ConsoleMenu(store)
            menu.svc.pairing = FakePairing()
            it = iter(keys)

            def fake_input(prompt=""):
                try:
                    return next(it)
                except StopIteration:
                    raise EOFError from None
            old_input = builtins.input
            builtins.input = fake_input
            out = io.StringIO()
            try:
                with redirect_stdout(out):
                    menu.run()
            finally:
                builtins.input = old_input
            return out.getvalue(), store.load()

    def test_configure_scan_select_and_pair(self):
        keys = ["1", "", "n", "", "", "4", "y", "1", "6", "", "0"]   # link: Enter = rfcomm
        out, saved = self.run_menu(keys)
        self.assertIn("test-board", out)
        self.assertIn("Passkey: 123456", out)
        self.assertIn("OK  Pairing successful", out)
        self.assertTrue(saved.no_ssh)
        self.assertEqual(saved.bt_addr, "AA:BB:CC:DD:EE:01")

    def test_settings_reject_invalid_values(self):
        # SSH = "maybe" (asked again) -> y; board "y" -> root@b; address "Y" -> lower-case valid;
        # channel "99" -> auto
        keys = ["1", "bogus", "rfcomm", "maybe", "y", "y", "root@b", "Y", "aa:bb:cc:dd:ee:02", "99", "auto", "0"]
        out, saved = self.run_menu(keys)
        self.assertIn("expected one of: rfcomm, ble-gatt, ble-coc", out)
        self.assertIn("answer y or n", out)
        self.assertIn("expected user@ip", out)
        self.assertIn("expected AA:BB:CC:DD:EE:FF", out)
        self.assertIn("expected 'auto' or a number 1-30", out)
        self.assertEqual((saved.board, saved.bt_addr, saved.channel),
                         ("root@b", "AA:BB:CC:DD:EE:02", "auto"))

    def test_invalid_choice_and_eof(self):
        out, _ = self.run_menu(["x"])          # then end of input = Ctrl+D
        self.assertIn("Invalid choice", out)


if __name__ == "__main__":
    unittest.main()
