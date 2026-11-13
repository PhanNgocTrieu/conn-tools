"""Bluetooth app: Python CTP client against the C++ agent, raw pipe relay,
BlueZ pairing backend (fake bluetoothctl), board event parsing."""

import json
import os
import socket
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ctt.core.agent import find_host_agent  # noqa: E402
from ctt.bluetooth import board, pairing  # noqa: E402
from ctt.protocol.ctp import CtpClient, CtpError, _Pattern  # noqa: E402

FAKE = f"{sys.executable} {ROOT / 'tests' / 'fakes' / 'fake_bluetoothctl.py'}"


def agent_or_skip():
    try:
        return find_host_agent()
    except FileNotFoundError:
        raise unittest.SkipTest("ctt-agent not built")


class CtpClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = agent_or_skip()
        cls.srv = subprocess.Popen([cls.agent, "server", "-t", "tcp", "-e", "127.0.0.1:0", "-J",
                                    "-i", "0"], stdout=subprocess.PIPE, text=True)
        ev = json.loads(cls.srv.stdout.readline())
        cls.port = int(ev["endpoint"].rsplit(":", 1)[1])

    @classmethod
    def tearDownClass(cls):
        cls.srv.terminate()
        cls.srv.wait(5)

    def session_end(self):
        while True:
            ev = json.loads(self.srv.stdout.readline())
            if ev["event"] == "session_end":
                return ev

    def run_mode(self, mode, **kw):
        with socket.create_connection(("127.0.0.1", self.port)) as s:
            ivs = []
            r = CtpClient(s).run(mode, duration_s=0.8, block=990, interval_s=0.2,
                                 on_interval=ivs.append, **kw)
        self.assertTrue(self.session_end()["ok"])
        self.assertEqual((r.lost, r.crc_errors, r.out_of_order), (0, 0, 0))
        self.assertTrue(ivs)
        return r

    def test_tx_measured_by_board(self):
        r = self.run_mode("tx", rate_bps=8_000_000)
        self.assertAlmostEqual(r.up_mbps, 8.0, delta=1.5)
        self.assertEqual(r.up_bytes % 990, 0)
        self.assertIsNone(r.down_mbps)

    def test_rx_and_bidir(self):
        self.assertGreater(self.run_mode("rx").down_mbps, 1)
        r = self.run_mode("bidir")
        self.assertGreater(r.up_mbps, 1)
        self.assertGreater(r.down_mbps, 1)

    def test_echo(self):
        r = self.run_mode("echo", echo_interval_ms=1)
        self.assertGreater(r.rtt_ms["count"], 10)
        self.assertEqual(r.echoed, r.rtt_ms["count"])
        self.assertLessEqual(r.rtt_ms["min"], r.rtt_ms["p50"])

    def test_not_a_ctp_peer(self):
        a, b = socket.socketpair()
        b.sendall(b"ACK_OK: RX=1 bytes" + b"\0" * 32)
        with self.assertRaisesRegex(CtpError, "not a ctt-agent"):
            CtpClient(a, timeout=2).run("tx", duration_s=0.1)
        a.close()
        b.close()

    def test_pattern_crc(self):
        import zlib
        p = _Pattern(100)
        data, crc = p.get(5)
        self.assertEqual(zlib.crc32(data), crc)


class PipeTest(unittest.TestCase):
    def test_relay_both_ways(self):
        agent = agent_or_skip()
        name = f"@ctt-btapp-{os.getpid()}"
        srv = subprocess.Popen([agent, "pipe", "-t", "unix", "-e", name], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True)
        self.assertEqual(json.loads(srv.stdout.readline())["event"], "listening")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect("\0" + name[1:])
            self.assertEqual(json.loads(srv.stdout.readline())["event"], "connected")
            s.sendall(b"hello board")
            ev = json.loads(srv.stdout.readline())
            self.assertEqual((ev["event"], bytes.fromhex(ev["hex"])), ("rx", b"hello board"))
            srv.stdin.write(b"hi PC\n".hex() + "\n")
            srv.stdin.flush()
            self.assertEqual(json.loads(srv.stdout.readline())["event"], "tx")
            self.assertEqual(s.recv(100), b"hi PC\n")
            srv.stdin.write("zz\n")
            srv.stdin.flush()
            self.assertEqual(json.loads(srv.stdout.readline())["event"], "error")
        self.assertEqual(json.loads(srv.stdout.readline())["event"], "closed")
        srv.stdin.close()
        self.assertEqual(srv.wait(5), 0)


class BluezPairingTest(unittest.TestCase):
    def setUp(self):
        os.environ["CTT_BLUETOOTHCTL"] = FAKE
        os.environ["FAKE_BT_DEVICES"] = "AA:BB:CC:DD:EE:01=test-board,AA:BB:CC:DD:EE:02=Phone"

    def tearDown(self):
        os.environ.pop("CTT_BLUETOOTHCTL", None)
        os.environ.pop("FAKE_BT_DEVICES", None)

    def test_scan_and_pair(self):
        be = pairing.BluezPairing()
        self.assertEqual(be.available(), (True, ""))
        devs = be.scan(0.3)
        self.assertIn(("AA:BB:CC:DD:EE:01", "test-board"), [(d.address, d.name) for d in devs])
        pins = []
        ok, msg = be.pair("aa:bb:cc:dd:ee:01", on_pin=pins.append, timeout=10)
        self.assertTrue(ok, msg)


class BoardHelpersTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(board._parse('{"event":"listening","channel":3}\n')["channel"], 3)
        self.assertEqual(board._parse("Warning: Permanently added\n"),
                         {"event": "log", "message": "Warning: Permanently added"})

    def test_windows_address_format(self):
        self.assertEqual(pairing._int_to_addr(pairing._addr_to_int("00:1A:7D:DA:71:13")),
                         "00:1A:7D:DA:71:13")


if __name__ == "__main__":
    unittest.main()


class BoardSshTest(unittest.TestCase):
    """Board plumbing over a fake ssh that runs commands locally."""

    def setUp(self):
        import tempfile
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

    def test_server_and_pipe(self):
        import threading
        b = board.Board("root@board")
        b.agent = str(self.agent)
        self.assertEqual(b.start_server(0), ("00:11:22:33:44:55", 5))
        self.assertEqual(b.start_server(0), ("00:11:22:33:44:55", 5))   # reused while running
        b.stop_server()
        self.assertIsNone(b.server_endpoint)

        got = []
        seen = threading.Event()

        def on_event(ev):
            got.append(ev)
            if ev.get("event") == "rx":
                seen.set()
        pipe = b.start_pipe(0, on_event=on_event)
        self.assertEqual(pipe.endpoint, ("00:11:22:33:44:55", 6))
        pipe.send(b"abc")
        self.assertTrue(seen.wait(5))
        rx = [e for e in got if e.get("event") == "rx"][0]
        self.assertEqual(rx["data"], b"cba")
        pipe.close()
