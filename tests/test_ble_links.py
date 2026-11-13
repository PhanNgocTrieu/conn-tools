"""BLE links without a radio.

GattAgentTest: private dbus-daemon + tests/fakes/fake_bluez.py + `ctt-agent server -t gatt`.
  The fake BlueZ plays the PC: AcquireNotify / AcquireWrite give the fds, a SocketBridge turns them
  into a socket and CtpClient runs every mode over it - the same path a real PC takes through bleak.
BinaryPipeTest: `ctt-agent pipe --binary` + SocketBridge (the Linux BLE L2CAP CoC path), over TCP.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ctt.protocol.ctp import CtpClient  # noqa: E402
from ctt.protocol.links import LINKS, SocketBridge, capabilities  # noqa: E402

AGENT = Path(os.environ.get("CTT_AGENT", ROOT / "build/host/agent/ctt-agent"))


def _have_fake_bluez() -> str:
    if not AGENT.exists():
        return f"host agent not built: {AGENT}"
    if not shutil.which("dbus-daemon"):
        return "dbus-daemon not installed"
    try:
        import dbus  # noqa: F401
        from gi.repository import GLib  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return f"dbus-python / gi missing: {e}"
    return ""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class CapabilityTest(unittest.TestCase):
    def test_every_link_is_reported_with_a_reason(self):
        caps = capabilities()
        self.assertEqual([c.link for c in caps], list(LINKS))
        for c in caps:
            self.assertTrue(c.why)
        if not sys.platform.startswith("linux"):
            self.assertFalse(next(c for c in caps if c.link == "ble-coc").ok)


@unittest.skipIf(_have_fake_bluez(), _have_fake_bluez())
class GattAgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        conf = Path(cls.tmp.name) / "bus.conf"
        sock = Path(cls.tmp.name) / "bus"
        conf.write_text(f"""<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><type>session</type><listen>unix:path={sock}</listen><auth>EXTERNAL</auth>
<policy context="default"><allow send_destination="*" eavesdrop="true"/><allow eavesdrop="true"/>
<allow own="*"/></policy></busconfig>""")
        cls.daemon = subprocess.Popen(["dbus-daemon", "--config-file", str(conf), "--nofork", "--print-address"],
                                      stdout=subprocess.PIPE, text=True)
        cls.addr = cls.daemon.stdout.readline().strip()
        cls.env = {**os.environ, "DBUS_SYSTEM_BUS_ADDRESS": f"unix:path={sock}"}
        cls.bluez = subprocess.Popen([sys.executable, str(ROOT / "tests/fakes/fake_bluez.py")], env=cls.env,
                                     stdout=subprocess.PIPE, text=True)
        assert cls.bluez.stdout.readline().strip() == "ready"
        cls.agent = subprocess.Popen([str(AGENT), "server", "-t", "gatt", "-o", "name=CTT-test", "-J", "-i", "0.5"],
                                     env=cls.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        cls.listening = json.loads(cls.agent.stdout.readline())
        cls.events: list = []

        def collect():
            for line in cls.agent.stdout:
                cls.events.append(json.loads(line))
        threading.Thread(target=collect, daemon=True).start()
        import dbus
        cls.bus = dbus.bus.BusConnection(f"unix:path={sock}")
        cls.fake = dbus.Interface(cls.bus.get_object("org.bluez", "/test"), "test.FakeBluez")

    @classmethod
    def tearDownClass(cls):
        for p in (cls.agent, cls.bluez, cls.daemon):
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        cls.tmp.cleanup()

    def connect(self, mtu=247):
        """What bleak does on the PC: enable notify, write without response -> one CTP socket."""
        nfd, wfd, mtu = self.fake.Connect(mtu)
        notify = socket.socket(fileno=nfd.take())
        write = socket.socket(fileno=wfd.take())
        bridge = SocketBridge(lambda d: write.send(d), mtu - 3, "test-gatt")

        def rx():
            while True:
                try:
                    data = notify.recv(1024)
                except OSError:
                    break
                if not data:
                    break
                self.assertLessEqual(len(data), mtu - 3)    # one notification = at most MTU-3 bytes
                bridge.feed(data)
            bridge.link_closed()
        threading.Thread(target=rx, daemon=True).start()

        def close():
            bridge.close()
            notify.close()
            write.close()
        return bridge.app, close

    def test_registration_and_advertisement(self):
        self.assertEqual(self.listening["event"], "listening")
        self.assertEqual(self.listening["service_uuid"], "a0c70001-8f5e-4d3a-9c2b-1f0e5d4c3b2a")
        self.assertTrue(self.listening["advertising"])
        self.assertEqual(self.listening["bdaddr"], "AA:BB:CC:00:11:22")
        adv = self.fake.Advert()
        self.assertEqual(str(adv["LocalName"]), "CTT-test")
        self.assertIn("a0c70001-8f5e-4d3a-9c2b-1f0e5d4c3b2a", [str(u) for u in adv["ServiceUUIDs"]])
        self.assertTrue(str(self.fake.Info()).startswith("ctt-agent "))

    def test_all_modes_over_gatt(self):
        for mode, block in (("tx", 990), ("rx", 990), ("bidir", 244), ("echo", 64)):
            with self.subTest(mode=mode):
                sock, close = self.connect()
                try:
                    r = CtpClient(sock, timeout=10).run(mode, 1.0, block, 0 if mode == "echo" else 200_000, True,
                                                        interval_s=0.5)
                finally:
                    close()
                self.assertEqual((r.lost, r.crc_errors, r.out_of_order), (0, 0, 0), mode)
                if mode in ("tx", "bidir"):
                    self.assertGreater(r.up_bytes, 0)
                    self.assertEqual(r.up_bytes, r.up_sent_bytes)      # board received all the PC sent
                if mode in ("rx", "bidir"):
                    self.assertGreater(r.down_bytes, 0)
                if mode == "echo":
                    self.assertGreater(r.rtt_ms["count"], 10)
                time.sleep(0.3)
        ends = [e for e in self.events if e["event"] == "session_end"]
        self.assertGreaterEqual(len(ends), 4)
        self.assertTrue(all(e["ok"] for e in ends), ends)
        starts = [e for e in self.events if e["event"] == "session_start"]
        self.assertEqual(starts[0]["peer"], "11:22:33:44:55:66")


@unittest.skipUnless(AGENT.exists(), f"host agent not built: {AGENT}")
class BinaryPipeTest(unittest.TestCase):
    def test_ctp_through_binary_pipe(self):
        port = free_port()
        srv = subprocess.Popen([str(AGENT), "server", "-t", "tcp", "-e", f"127.0.0.1:{port}", "-J"],
                               stdout=subprocess.PIPE, text=True)
        try:
            srv.stdout.readline()
            pipe = subprocess.Popen([str(AGENT), "pipe", "--binary", "-t", "tcp", "--connect", f"127.0.0.1:{port}"],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            self.assertEqual(json.loads(pipe.stderr.readline())["event"], "connected")
            bridge = SocketBridge(pipe.stdin.write, 4096, "test-pipe")

            def reader():
                while True:
                    d = os.read(pipe.stdout.fileno(), 65536)
                    if not d:
                        break
                    bridge.feed(d)
                bridge.link_closed()
            threading.Thread(target=reader, daemon=True).start()
            r = CtpClient(bridge.app, timeout=10).run("bidir", 1.0, 990, 400_000, True)
            self.assertEqual((r.lost, r.crc_errors), (0, 0))
            self.assertEqual(r.up_bytes, r.up_sent_bytes)
            self.assertGreater(r.down_bytes, 0)
            bridge.close()
            pipe.stdin.close()
            pipe.wait(5)
        finally:
            srv.kill()
            srv.wait()


if __name__ == "__main__":
    unittest.main()
