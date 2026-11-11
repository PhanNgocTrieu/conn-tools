"""`ctt ui`: a local web interface over the ctt command line.

Every action in the page is a normal `ctt ...` command run as a subprocess
(the page shows the exact command), so the UI and the CLI always behave the
same. Only the Python standard library is used.

The server binds to 127.0.0.1 by default; WSL forwards localhost, so the page
opens in a Windows browser at http://localhost:<port>/.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import signal
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ctt import __version__
from ctt.agent import REPO_ROOT
from ctt.runner import MODES

STATIC = Path(__file__).resolve().parent / "ui_static"
# First words of a command the page may start. Anything else is refused.
ALLOWED = {"run", "profile", "selftest", "deploy", "dut-info", "bt", "usb", "mfi", "fw",
           "report", "compare"}
TRANSPORTS = ["tcp", "udp", "rfcomm", "l2cap", "usb", "unix"]
MAX_LINES = 20000


class Job:
    def __init__(self, jid: int, argv: list[str], out_root: Path):
        self.id = jid
        self.argv = argv
        self.lines: list[str] = []
        self.rc: int | None = None
        self.started = time.time()
        self.ended: float | None = None
        self.run_dir: str | None = None
        self._out_root = out_root
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen([sys.executable, "-m", "ctt", *argv], cwd=REPO_ROOT, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, text=True, bufsize=1,
                                     start_new_session=True)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            if len(self.lines) < MAX_LINES:
                self.lines.append(line)
            m = re.search(r"report: (\S+)", line)
            if m:
                self._set_run_dir(m.group(1))
        self.rc = self.proc.wait()
        self.ended = time.time()

    def _set_run_dir(self, path: str) -> None:
        try:
            self.run_dir = str(Path(path).resolve().relative_to(self._out_root.resolve()))
        except ValueError:
            self.run_dir = None

    @property
    def running(self) -> bool:
        return self.rc is None

    def stop(self) -> None:
        if not self.running:
            return
        try:  # SIGINT first: ctt stops the agents and still writes the report
            os.killpg(self.proc.pid, signal.SIGINT)
        except ProcessLookupError:
            return

        def hard_kill():
            time.sleep(8)
            if self.running:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        threading.Thread(target=hard_kill, daemon=True).start()

    def info(self, since: int = 0) -> dict:
        return {"id": self.id, "argv": self.argv, "running": self.running, "rc": self.rc,
                "started": self.started, "ended": self.ended, "run_dir": self.run_dir,
                "lines": self.lines[since:], "next": len(self.lines)}


class App:
    def __init__(self, out_root: Path):
        self.out_root = out_root
        self.jobs: dict[int, Job] = {}
        self.lock = threading.Lock()
        self.next_id = 1

    def start(self, argv: list[str]) -> Job:
        if not argv or argv[0] not in ALLOWED:
            raise ValueError(f"command not allowed: {argv[:1]}")
        if not all(isinstance(a, str) for a in argv):
            raise ValueError("arguments must be strings")
        with self.lock:
            busy = [j for j in self.jobs.values() if j.running]
            if busy:
                raise RuntimeError(f"job {busy[0].id} is still running "
                                   f"(ctt {' '.join(busy[0].argv)})")
            if argv[0] in ("run", "profile", "selftest", "mfi") or argv[:2] in (
                    ["bt", "pair-stress"], ["usb", "storage"]):
                if "--out" not in argv:
                    argv = [*argv, "--out", str(self.out_root)]
            job = Job(self.next_id, argv, self.out_root)
            self.jobs[job.id] = job
            self.next_id += 1
            for old in sorted(self.jobs)[:-50]:   # keep the last 50 jobs
                del self.jobs[old]
            return job

    def config(self) -> dict:
        def rel(p: Path) -> str:
            return str(p.relative_to(REPO_ROOT))
        configs = sorted((REPO_ROOT / "configs").glob("*.yaml"))
        return {
            "version": __version__,
            "dut": os.environ.get("CTT_DUT", ""),
            "profiles": [rel(p) for p in sorted((REPO_ROOT / "profiles").glob("*.yaml"))],
            "mfi_configs": [rel(p) for p in configs if "mfi" in p.name],
            "transports": TRANSPORTS,
            "modes": list(MODES),
            "out_root": str(self.out_root),
        }

    def runs(self) -> list[dict]:
        out = []
        if not self.out_root.is_dir():
            return out
        for d in sorted(self.out_root.iterdir(), reverse=True)[:200]:
            if not d.is_dir():
                continue
            item = {"dir": d.name, "html": (d / "report.html").is_file()}
            sj = d / "summary.json"
            rj = d / "result.json"
            try:
                if sj.is_file():
                    s = json.loads(sj.read_text())
                    item.update(run=s.get("run"), dut=s.get("dut"), passed=s.get("passed", 0),
                                failed=s.get("failed", 0), errors=s.get("errors", 0),
                                tests=[{"name": t.get("name"), "verdict": t.get("verdict")}
                                       for t in s.get("tests", [])])
                elif rj.is_file():
                    s = json.loads(rj.read_text())
                    summ = s.get("summary", {})
                    item.update(run=d.name.split("_", 1)[-1],
                                passed=summ.get("success"), failed=None, errors=None,
                                note=f"{summ.get('success')}/{summ.get('attempts')} "
                                     f"pairing OK" if summ else "")
            except (OSError, ValueError):
                item["note"] = "unreadable summary"
            out.append(item)
        return out


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"ctt-ui/{__version__}"

        def log_message(self, fmt, *args):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _file(self, path: Path) -> None:
            if not path.is_file():
                self._json({"error": "not found"}, 404)
                return
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/json", "application/javascript"):
                ctype += "; charset=utf-8"
            self._send(200, path.read_bytes(), ctype)

        def do_GET(self):
            u = urlparse(self.path)
            p = u.path
            if p in ("/", "/index.html"):
                return self._file(STATIC / "index.html")
            if p == "/api/config":
                return self._json(app.config())
            if p == "/api/runs":
                return self._json(app.runs())
            if p == "/api/jobs":
                return self._json([j.info(len(j.lines)) for j in app.jobs.values()])
            m = re.fullmatch(r"/api/jobs/(\d+)", p)
            if m:
                job = app.jobs.get(int(m.group(1)))
                if not job:
                    return self._json({"error": "no such job"}, 404)
                since = int(parse_qs(u.query).get("since", ["0"])[0])
                return self._json(job.info(since))
            if p.startswith("/out/"):
                root = app.out_root.resolve()
                target = (root / unquote(p[5:])).resolve()
                if root not in target.parents:
                    return self._json({"error": "forbidden"}, 403)
                return self._file(target)
            self._json({"error": "not found"}, 404)

        def do_POST(self):
            # Refuse cross-site form posts: the page always sends JSON.
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._json({"error": "JSON body required"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json({"error": "bad JSON"}, 400)
            p = urlparse(self.path).path
            if p == "/api/jobs":
                try:
                    job = app.start(list(body.get("argv") or []))
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
                except RuntimeError as e:
                    return self._json({"error": str(e)}, 409)
                return self._json(job.info())
            m = re.fullmatch(r"/api/jobs/(\d+)/stop", p)
            if m:
                job = app.jobs.get(int(m.group(1)))
                if not job:
                    return self._json({"error": "no such job"}, 404)
                job.stop()
                return self._json({"ok": True})
            self._json({"error": "not found"}, 404)

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8765, out_root: str | Path | None = None,
          open_browser: bool = False) -> None:
    app = App(Path(out_root or REPO_ROOT / "out"))
    app.out_root.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}/"
    print(f"ctt ui: {url}  (Ctrl+C to stop)")
    if host not in ("127.0.0.1", "localhost"):
        print("warning: the UI has no login; anyone who reaches this address can run tests",
              file=sys.stderr)
    if open_browser:
        _open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for j in app.jobs.values():
            j.stop()
        httpd.server_close()


def _open(url: str) -> None:
    for cmd in (["wslview", url], ["cmd.exe", "/c", "start", "", url], ["xdg-open", url]):
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            continue
