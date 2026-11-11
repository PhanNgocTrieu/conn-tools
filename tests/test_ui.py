"""Web UI server: command allow-list, job lifecycle, run listing, static file safety."""

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctt import ui  # noqa: E402


class UiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        run = cls.out / "20260101-000000_demo"
        run.mkdir()
        (run / "summary.json").write_text(json.dumps(
            {"run": "demo", "dut": "local", "passed": 1, "failed": 1, "errors": 0,
             "tests": [{"name": "a", "verdict": "PASS"}, {"name": "b", "verdict": "FAIL"}]}))
        (run / "report.html").write_text("<html>ok</html>")
        cls.app = ui.App(cls.out)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), ui.make_handler(cls.app))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.status, r.read()

    def post(self, path, body, ctype="application/json"):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(),
                                     {"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_page_and_config(self):
        self.assertEqual(self.get("/")[0], 200)
        cfg = json.loads(self.get("/api/config")[1])
        self.assertIn("profiles/tcp_smoke.yaml", cfg["profiles"])
        self.assertIn("usb", cfg["transports"])

    def test_refuses_unknown_commands_and_form_posts(self):
        self.assertEqual(self.post("/api/jobs", {"argv": ["sh", "-c", "id"]})[0], 400)
        self.assertEqual(self.post("/api/jobs", {"argv": []})[0], 400)
        self.assertEqual(self.post("/api/jobs", {"argv": ["fw"]}, "text/plain")[0], 415)

    def test_runs_and_report_files(self):
        runs = json.loads(self.get("/api/runs")[1])
        self.assertEqual((runs[0]["passed"], runs[0]["failed"]), (1, 1))
        self.assertEqual(self.get("/out/20260101-000000_demo/report.html")[1], b"<html>ok</html>")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/out/..%2F..%2Fetc%2Fpasswd")
        self.assertEqual(cm.exception.code, 403)

    def test_job_runs_and_reports_exit_code(self):
        code, job = self.post("/api/jobs", {"argv": ["fw", "--help"]})
        self.assertEqual(code, 200)
        deadline = time.time() + 30
        while time.time() < deadline:
            info = json.loads(self.get(f"/api/jobs/{job['id']}")[1])
            if not info["running"]:
                break
            time.sleep(0.1)
        self.assertEqual(info["rc"], 0)
        self.assertTrue(any("wifi-mode" in line for line in info["lines"]))


if __name__ == "__main__":
    unittest.main()
