"""Self-contained HTML report (no external resources) and JUnit XML.

Both are generated from a run directory (summary.json + NN_*/result.json +
intervals.csv), so they can be rebuilt any time with `ctt report DIR`.
"""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

COLORS = {"up": "#2563eb", "down": "#16a34a", "rtt": "#9333ea"}


def load_run(run_dir: Path) -> tuple[dict, list[dict]]:
    summary = json.loads((run_dir / "summary.json").read_text())
    tests = []
    for i, row in enumerate(summary.get("tests", []), 1):
        tdirs = sorted(run_dir.glob(f"{i:02d}_*"))
        data, intervals = {}, []
        if tdirs:
            rj = tdirs[0] / "result.json"
            if rj.exists():
                data = json.loads(rj.read_text())
            ic = tdirs[0] / "intervals.csv"
            if ic.exists():
                with open(ic, newline="") as f:
                    intervals = list(csv.DictReader(f))
        tests.append({"row": row, "data": data, "intervals": intervals})
    return summary, tests


# ---- charts ---------------------------------------------------------------------

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def series_from_intervals(intervals: list[dict]) -> list[tuple[str, str, list[tuple[float, float]]]]:
    """Receiver-side throughput per direction (+ echo RTT), as (label, color, points)."""
    out = []
    for d in ("up", "down"):
        pts = [(_num(r["t_end"]), _num(r["mbps"])) for r in intervals
               if r.get("dir") == d and r.get("role") == "rx"]
        pts = [(x, y) for x, y in pts if x is not None and y is not None]
        if pts:
            label = "up (host→DUT) Mbps" if d == "up" else "down (DUT→host) Mbps"
            out.append((label, COLORS[d], sorted(pts)))
    return out


def rtt_series(intervals: list[dict]):
    pts = [(_num(r["t_end"]), _num(r.get("avg_us"))) for r in intervals if r.get("dir") == "rtt"]
    pts = [(x, y) for x, y in pts if x is not None and y is not None]
    return [("RTT avg µs", COLORS["rtt"], sorted(pts))] if pts else []


def svg_chart(series, width=640, height=180, unit="") -> str:
    if not series:
        return ""
    pad_l, pad_r, pad_t, pad_b = 52, 12, 10, 26
    xs = [x for _, _, pts in series for x, _ in pts]
    ys = [y for _, _, pts in series for _, y in pts]
    x0, x1 = 0.0, max(xs) or 1.0
    y1 = max(ys) * 1.1 if max(ys) > 0 else 1.0
    w, h = width - pad_l - pad_r, height - pad_t - pad_b

    def sx(x):
        return pad_l + (x - x0) / (x1 - x0) * w

    def sy(y):
        return pad_t + h - y / y1 * h

    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img">']
    for i in range(5):
        yv = y1 * i / 4
        yy = sy(yv)
        parts.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{pad_l + w}" y2="{yy:.1f}" class="grid"/>')
        parts.append(f'<text x="{pad_l - 6}" y="{yy + 4:.1f}" class="tick" text-anchor="end">'
                     f'{yv:.3g}</text>')
    for i in range(5):
        xv = x0 + (x1 - x0) * i / 4
        parts.append(f'<text x="{sx(xv):.1f}" y="{height - 8}" class="tick" text-anchor="middle">'
                     f'{xv:.3g}s</text>')
    for label, color, pts in series:
        if len(pts) == 1:
            x, y = pts[0]
            parts.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="3" fill="{color}"/>')
        else:
            d = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in pts)
            parts.append(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="2"/>')
    parts.append("</svg>")
    legend = "".join(f'<span class="lg"><i style="background:{c}"></i>{html.escape(l)}</span>'
                     for l, c, _ in series)
    return f'<div class="legend">{legend}</div>' + "".join(parts)


# ---- html -------------------------------------------------------------------------

CSS = """
:root{--bg:#fff;--fg:#1f2937;--muted:#6b7280;--line:#e5e7eb;--card:#f9fafb;
--pass:#15803d;--fail:#b91c1c;--err:#b45309}
@media (prefers-color-scheme:dark){:root{--bg:#111827;--fg:#e5e7eb;--muted:#9ca3af;
--line:#374151;--card:#1f2937;--pass:#4ade80;--fail:#f87171;--err:#fbbf24}}
body{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0 auto;
max-width:1100px;padding:16px}
h1{font-size:22px;margin:4px 0}h2{font-size:17px;margin:28px 0 8px}h3{font-size:15px;margin:0 0 6px}
.meta{color:var(--muted)}.kpis{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 16px}
.kpi b{display:block;font-size:22px}.table-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;white-space:nowrap}
td.n{text-align:right}th{color:var(--muted);font-weight:600}
.PASS{color:var(--pass);font-weight:700}.FAIL{color:var(--fail);font-weight:700}
.ERROR{color:var(--err);font-weight:700}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:12px 0}
.chart{width:100%;height:auto;max-height:220px}.grid{stroke:var(--line)}.tick{fill:var(--muted);font-size:11px}
.legend{font-size:12px;color:var(--muted)}.lg{margin-right:14px}
.lg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.err{color:var(--fail)}details{margin-top:6px}pre{white-space:pre-wrap;font-size:12px;color:var(--muted)}
a{color:inherit}
"""

COLS = [("up_mbps", "Up Mbps"), ("down_mbps", "Down Mbps"), ("rtt_avg_us", "RTT avg µs"),
        ("rtt_p99_us", "RTT p99 µs"), ("loss_pct", "Loss %"), ("jitter_us", "Jitter µs"),
        ("lost", "Lost"), ("crc_errors", "CRC"), ("success_rate", "Success"),
        ("wifi_signal_dbm", "Signal dBm")]


def _fmt(v):
    if v is None or v == "":
        return "–"
    if isinstance(v, float):
        return f"{v:.3f}" if abs(v) < 1000 else f"{v:.1f}"
    return html.escape(str(v))


def render_html(run_dir: Path) -> str:
    summary, tests = load_run(run_dir)
    rows = [t["row"] for t in tests]
    cols = [(k, l) for k, l in COLS if any(r.get(k) not in (None, "") for r in rows)]
    title = f"CTT report – {summary.get('run', run_dir.name)}"
    out = [f"<!doctype html><html lang='vi'><head><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>",
           f"<h1>{html.escape(title)}</h1>",
           f"<div class='meta'>DUT: {html.escape(str(summary.get('dut', '?')))} · "
           f"ctt {html.escape(str(summary.get('ctt_version', '?')))} · {html.escape(run_dir.name)}</div>",
           "<div class='kpis'>"]
    for k, label in (("passed", "PASS"), ("failed", "FAIL"), ("errors", "ERROR")):
        out.append(f"<div class='kpi'><b class='{label}'>{summary.get(k, 0)}</b>{label}</div>")
    out.append(f"<div class='kpi'><b>{len(rows)}</b>tests</div></div>")

    out.append("<div class='table-wrap'><table><tr><th>Test</th><th>Transport</th><th>Mode</th>"
               + "".join(f"<th>{l}</th>" for _, l in cols) + "<th>Verdict</th></tr>")
    for i, r in enumerate(rows, 1):
        out.append(f"<tr><td><a href='#t{i}'>{html.escape(str(r.get('name')))}</a></td>"
                   f"<td>{_fmt(r.get('transport'))}</td><td>{_fmt(r.get('mode'))}</td>"
                   + "".join(f"<td class='n'>{_fmt(r.get(k))}</td>" for k, _ in cols)
                   + f"<td class='{r.get('verdict')}'>{r.get('verdict')}</td></tr>")
    out.append("</table></div><h2>Details</h2>")

    for i, t in enumerate(tests, 1):
        r, data = t["row"], t["data"]
        out.append(f"<div class='card' id='t{i}'><h3>{html.escape(str(r.get('name')))} "
                   f"<span class='{r.get('verdict')}'>{r.get('verdict')}</span></h3>")
        chart = svg_chart(series_from_intervals(t["intervals"])) + svg_chart(rtt_series(t["intervals"]))
        if chart:
            out.append(chart)
        if r.get("error"):
            out.append(f"<div class='err'>{html.escape(str(r['error']))}</div>")
        checks = data.get("checks") or []
        if checks:
            out.append("<table><tr><th>Check</th><th>Value</th><th>Limit</th><th></th></tr>")
            for c in checks:
                ok = "PASS" if c.get("ok") else "FAIL"
                out.append(f"<tr><td>{html.escape(str(c.get('check')))}</td>"
                           f"<td class='n'>{_fmt(c.get('value'))}</td>"
                           f"<td>{html.escape(str(c.get('op', '')))} {_fmt(c.get('limit'))}</td>"
                           f"<td class='{ok}'>{ok}</td></tr>")
            out.append("</table>")
        extra = {k: data.get(k) for k in ("target", "wifi") if data.get(k)}
        if data.get("result", {}) and isinstance(data["result"], dict) and data["result"].get("link"):
            extra["link"] = data["result"]["link"]
        if extra:
            out.append("<details><summary>Link / environment</summary><pre>"
                       + html.escape(json.dumps(extra, indent=2, ensure_ascii=False)) + "</pre></details>")
        out.append("</div>")
    out.append("</body></html>")
    return "\n".join(out)


def render_junit(run_dir: Path) -> str:
    summary, tests = load_run(run_dir)
    rows = [t["row"] for t in tests]
    suite = xml_escape(str(summary.get("run", "ctt")))
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<testsuite name="{suite}" tests="{len(rows)}" failures="{summary.get("failed", 0)}" '
           f'errors="{summary.get("errors", 0)}">']
    for t in tests:
        r = t["row"]
        name = xml_escape(str(r.get("name")), {'"': "&quot;"})
        cls = xml_escape(f"ctt.{r.get('transport', 'x')}.{r.get('mode', 'x')}", {'"': "&quot;"})
        dur = r.get("duration_s") or 0
        out.append(f'  <testcase classname="{cls}" name="{name}" time="{dur}">')
        failed = [c for c in (t["data"].get("checks") or []) if not c.get("ok")]
        msg = "; ".join(f"{c.get('check')}: {c.get('value')} not {c.get('op', '')} {c.get('limit')}"
                        for c in failed)
        if r.get("verdict") == "FAIL":
            out.append(f'    <failure message="{xml_escape(msg or str(r.get("error", "")), {chr(34): "&quot;"})}"/>')
        elif r.get("verdict") == "ERROR":
            out.append(f'    <error message="{xml_escape(str(r.get("error", "")), {chr(34): "&quot;"})}"/>')
        metrics = {k: v for k, v in r.items() if k not in ("name", "error", "verdict")}
        out.append(f"    <system-out>{xml_escape(json.dumps(metrics))}</system-out>")
        out.append("  </testcase>")
    out.append("</testsuite>")
    return "\n".join(out)


def write_reports(run_dir: str | Path) -> tuple[Path, Path]:
    run_dir = Path(run_dir)
    h = run_dir / "report.html"
    j = run_dir / "junit.xml"
    h.write_text(render_html(run_dir), encoding="utf-8")
    j.write_text(render_junit(run_dir), encoding="utf-8")
    return h, j
