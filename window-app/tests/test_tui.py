"""Console app (ctt.tui): navigation, validation, and a local test run driven through the menus."""

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
from ctt.tui import app, ui  # noqa: E402

AGENT = Path(os.environ.get("CTT_AGENT", ROOT / "build/ctt-agent-linux/ctt-agent"))


def drive(answers: list[str]) -> str:
    """Runs the app with these answers typed in; returns what it printed."""
    feed = iter(answers)

    def fake_input(prompt=""):
        print(prompt, end="")
        try:
            return next(feed)
        except StopIteration:
            raise EOFError from None
    out = io.StringIO()
    with mock.patch("builtins.input", fake_input), contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(out):
        try:
            app.main()
        except SystemExit:
            pass
    return out.getvalue()


class ValidationTest(unittest.TestCase):
    def test_ip_and_board(self):
        self.assertIsNone(ui.check_ip("192.168.1.10"))
        self.assertIsNone(ui.check_ip("fe80::1"))
        self.assertIsNone(ui.check_ip("board.local"))
        self.assertIsNotNone(ui.check_ip("192.168.1"))
        self.assertIsNotNone(ui.check_ip("999.1.1.1"))
        self.assertIsNone(ui.check_board("root@10.0.0.2"))
        self.assertIsNotNone(ui.check_board("@10.0.0.2"))
        self.assertEqual(ui.board_target("10.0.0.2"), "root@10.0.0.2")
        self.assertEqual(ui.board_target("me@10.0.0.2"), "me@10.0.0.2")
        self.assertIsNone(ui.check_duration("10s"))
        self.assertIsNotNone(ui.check_duration("ten"))


class MenuTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "CTT_CONFIG": os.path.join(self.tmp.name, "settings.yaml"),
            "CTT_REPORTS": os.path.join(self.tmp.name, "reports")})
        self.env.start()
        os.environ.pop("CTT_DUT", None)
        import ctt.cli.options
        import ctt.paths
        where = Path(self.tmp.name) / "reports"
        self.reports = [mock.patch.object(ctt.paths, "REPORTS", where),
                        mock.patch.object(ctt.cli.options, "REPORTS", where)]   # `--out` default
        for p in self.reports:
            p.start()

    def tearDown(self):
        for p in self.reports:
            p.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_welcome_menus_and_exit(self):
        out = drive(["", "1", "0", "2", "0", "3", "0", "6", "1", "", "0", "0"])
        self.assertIn("Welcome", out)
        for title in ("[ WiFi Test ]", "[ USB Test ]", "[ Bluetooth Test ]", "[ Settings ]"):
            self.assertIn(title, out)
        self.assertIn("bye", out)
        self.assertEqual(settings.load()["dut"], "local")

    def test_bad_choice_and_bad_ip_are_asked_again(self):
        out = drive(["", "9", "4", "1", "300.1.1.1", "local", "", "0", "0"])
        self.assertIn("enter a number 0-6", out)
        self.assertIn("is not an IP address", out)

    def test_board_actions_ask_for_the_board(self):
        out = drive(["", "1", "2", "local", "", "0", "0"])   # WiFi -> Scan with no board
        self.assertIn("this needs the board's IP", out)

    @unittest.skipUnless(AGENT.is_file(), "host agent not built (scripts/build.sh linux)")
    def test_quick_test_runs(self):
        with mock.patch.dict(os.environ, {"CTT_AGENT": str(AGENT)}):
            out = drive(["", "1", "3", "1", "4", "1s", "", "0", "0"])  # WiFi quick TCP latency
        self.assertIn("$ ctt run -t tcp -m echo -d 1s", out)
        self.assertIn("1/1 passed", out)


if __name__ == "__main__":
    unittest.main()
