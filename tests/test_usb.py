"""USB: inventory parsing, storage throughput (real file I/O), runner rules."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ctt.interfaces import usb  # noqa: E402
from ctt.core.agent import find_host_agent  # noqa: E402
from ctt.core.executor import LocalExecutor  # noqa: E402
from ctt.testing.runner import Runner, TestSpec  # noqa: E402

LIST = """usb1|1d6b|0002|480|09|Linux 6.1 ehci_hcd|EHCI Host Controller
1-1|0781|5581|480|00|SanDisk|Ultra
1-2|1d6b|0104|5000|00|CTT|CTT board test gadget
garbage line
"""


class UsbTest(unittest.TestCase):
    def test_parse_list(self):
        devs = usb.parse_list(LIST)
        self.assertEqual(len(devs), 3)
        self.assertTrue(devs[0]["root_hub"])
        self.assertEqual((devs[1]["id"], devs[1]["speed_mbps"]), ("0781:5581", 480.0))
        self.assertEqual(devs[2]["product"], "CTT board test gadget")

    def test_gadget_script_syntax(self):
        subprocess.run(["sh", "-n", str(usb.GADGET_SCRIPT)], check=True)

    def test_usb_needs_server_on_dut(self):
        with self.assertRaisesRegex(ValueError, "server_side=dut"):
            TestSpec.from_dict({"transport": "usb", "server_side": "host"})
        spec = TestSpec.from_dict({"transport": "usb"})
        local = LocalExecutor()
        r = Runner(local, local, "agent", "agent")
        self.assertEqual(r._listen(spec), "/dev/ffs-ctt")
        self.assertEqual(r._target(spec, local, {}), "1d6b:0104")


class StorageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.agent = find_host_agent()
        except FileNotFoundError:
            raise unittest.SkipTest("ctt-agent not built")

    def test_storage_roundtrip(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as d:
            path = str(Path(d) / "t.bin")
            ev = usb.storage(LocalExecutor(), self.agent, path, size="8M", block="256K")
            self.assertTrue(ev["ok"], ev)
            self.assertEqual(ev["verify_errors"], 0)
            self.assertGreater(ev["write_MBps"], 0)
            self.assertFalse(Path(path).exists())  # deleted unless --keep

    def test_storage_detects_corruption(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as d:
            path = Path(d) / "t.bin"
            a = self.agent
            subprocess.run([a, "storage", "--path", str(path), "--size", "1M", "--block", "64K",
                            "--write-only", "--keep"], check=True, capture_output=True)
            data = bytearray(path.read_bytes())
            data[70000] ^= 0xFF  # flip a byte in block 1
            path.write_bytes(data)
            r = subprocess.run([a, "storage", "--json", "--path", str(path), "--size", "1M",
                                "--block", "64K", "--read-only"], capture_output=True, text=True)
            ev = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertEqual(r.returncode, 1)
            self.assertEqual(ev["verify_errors"], 1)

    def test_cli_storage(self):
        from ctt.cli import main
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as d:
            rc = main(["usb", "storage", "--dut", "local", "--path", f"{d}/x.bin", "--size", "4M",
                       "--out", d, "--expect", "write_MBps_min=0.001"])
            self.assertEqual(rc, 0)
            rc = main(["usb", "storage", "--dut", "local", "--path", f"{d}/x.bin", "--size", "4M",
                       "--out", d, "--expect", "read_MBps_min=1e12"])
            self.assertEqual(rc, 1)

    def test_usb_transport_errors_are_clear(self):
        r = subprocess.run([self.agent, "client", "-t", "usb", "-e", "dead:beef", "-d", "1"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not found", r.stderr)
        r = subprocess.run([self.agent, "server", "-t", "usb", "-e", "/nonexistent", "--once"],
                           capture_output=True, text=True)
        self.assertIn("FunctionFS mounted?", r.stderr)


if __name__ == "__main__":
    unittest.main()
