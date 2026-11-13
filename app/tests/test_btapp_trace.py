"""Live trace lines: PC side (interval_line) and board side (board_event_line, agent JSON)."""

import json
import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
sys.path.insert(0, str(ROOT / "app" / "src"))

from ctt.btapp.formatting import board_event_line, human_bytes, interval_line, result_lines  # noqa: E402
from ctt.protocol.ctp import CtpClient, Interval  # noqa: E402

AGENT = Path(os.environ.get("CTT_AGENT", ROOT / "build/host/agent/ctt-agent"))


class FormatTest(unittest.TestCase):
    def test_human_bytes(self):
        self.assertEqual([human_bytes(n) for n in (512, 51_480, 1_234_567)], ["512 B", "51.5 KB", "1.23 MB"])

    def test_interval_line_has_rate_and_totals(self):
        line = interval_line(Interval(3.0, up_mbps=0.4, down_mbps=0.5, up_bytes=150_480, up_frames=152,
                                      down_bytes=99_000, down_frames=100, lost=1))
        self.assertIn("PC->board  0.400 Mbit/s  sent 150.5 KB (152 fr)", line)
        self.assertIn("board->PC  0.500 Mbit/s  received 99.0 KB (100 fr)  lost 1", line)
        self.assertIn("replies 42", interval_line(Interval(2.0, rtt_ms=3.0, echo_count=42)))

    def test_board_event_line(self):
        ev = {"event": "interval", "role": "rx", "t_end": 2.0, "total_bytes": 101_000, "total_frames": 102,
              "mbps": 0.4, "total_lost": 0, "total_crc_errors": 0}
        self.assertEqual(board_event_line(ev), "  [board]   2.0s  received 101.0 KB (102 fr)  0.400 Mbit/s  lost 0  crc 0")
        self.assertIn("sent 101.0 KB", board_event_line({**ev, "role": "tx"}))
        self.assertIsNone(board_event_line({"event": "listening"}))
        self.assertIn("session failed: boom", board_event_line({"event": "session_end", "ok": False, "message": "boom"}))


@unittest.skipUnless(AGENT.exists(), f"host agent not built: {AGENT}")
class AgentTraceTest(unittest.TestCase):
    """The board agent reports what it received / sent so far, every interval, for every mode."""

    def test_server_trace_all_modes(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        srv = subprocess.Popen([str(AGENT), "server", "-t", "tcp", "-e", f"127.0.0.1:{port}", "-J", "-i", "0.5"],
                               stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(json.loads(srv.stdout.readline())["event"], "listening")
            sent = {}
            for mode in ("tx", "rx", "bidir", "echo"):
                s = None
                for _ in range(50):
                    try:
                        s = socket.create_connection(("127.0.0.1", port))
                        break
                    except OSError:
                        time.sleep(0.05)
                r = CtpClient(s).run(mode, 1.2, 64 if mode == "echo" else 990, 0 if mode == "echo" else 400_000,
                                     True, interval_s=0.5)
                s.close()
                sent[mode] = r
                self.assertIn("data: " if mode != "echo" else "RTT", "\n".join(result_lines(r)))
            srv.terminate()
            events = [json.loads(line) for line in srv.stdout]
        finally:
            srv.kill()
            srv.wait()
        sessions, cur = [], None
        for ev in events:
            if ev["event"] == "session_params":
                cur = {"params": ev, "intervals": [], "echo": []}
                sessions.append(cur)
            elif ev["event"] == "interval":
                cur["intervals"].append(ev)
            elif ev["event"] == "echo_progress":
                cur["echo"].append(ev)
            elif ev["event"] == "session_end":
                cur["end"] = ev
        self.assertEqual([s["params"]["mode"] for s in sessions], ["tx", "rx", "bidir", "echo"])
        for s in sessions:
            self.assertTrue(s["params"]["direction"])
        tx, rx, bidir, echo = sessions
        self.assertEqual({e["role"] for e in tx["intervals"]}, {"rx"})          # board receives
        self.assertEqual({e["role"] for e in rx["intervals"]}, {"tx"})          # board sends
        self.assertEqual({e["role"] for e in bidir["intervals"]}, {"rx", "tx"})
        last_rx = [e for e in tx["intervals"] if e["role"] == "rx"][-1]
        self.assertEqual(last_rx["total_bytes"], tx["end"]["rx"]["bytes"])     # running total == final count
        self.assertEqual(tx["end"]["rx"]["bytes"], sent["tx"].up_sent_bytes)   # board received all the PC sent
        self.assertGreaterEqual(len(echo["echo"]), 2)
        self.assertEqual(echo["echo"][-1]["total_replies"], echo["end"]["echoed"])


@unittest.skipUnless(AGENT.exists(), f"host agent not built: {AGENT}")
class PipeTextTest(unittest.TestCase):
    """`ctt-agent pipe --text`: a person on the board UART types text, sees text + hex + totals."""

    def test_text_mode_both_ways(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        p = subprocess.Popen([str(AGENT), "pipe", "--text", "-t", "tcp", "-e", f"127.0.0.1:{port}"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertIn("-- listening on tcp", p.stdout.readline())
            s = socket.create_connection(("127.0.0.1", port))
            self.assertIn("-- connected:", p.stdout.readline())
            s.sendall(b"hello board\n")
            self.assertIn('board rx 12 B (total rx 12 B): "hello board\\n"   hex 68 65', p.stdout.readline())
            s.sendall(bytes([0, 0xFF, 0x0D, 0x0A]))
            self.assertIn('board rx 4 B (total rx 16 B): "\\x00\\xff\\r\\n"   hex 00 ff 0d 0a', p.stdout.readline())
            p.stdin.write("hi PC\r\n/hex 01 02\n/hex zz\n")
            p.stdin.flush()
            self.assertIn('board tx 6 B (total tx 6 B): "hi PC\\n"', p.stdout.readline())
            self.assertIn("board tx 2 B (total tx 8 B)", p.stdout.readline())
            self.assertIn("-- not a hex string: /hex zz", p.stdout.readline())
            s.settimeout(5)
            got = b""
            while len(got) < 8:
                got += s.recv(64)
            self.assertEqual(got, b"hi PC\n\x01\x02")
            s.close()
            self.assertIn("-- link closed", p.stdout.readline())
        finally:
            p.kill()
            p.wait()


if __name__ == "__main__":
    unittest.main()
