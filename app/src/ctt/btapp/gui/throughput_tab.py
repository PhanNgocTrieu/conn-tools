"""Tab 2: throughput / latency test with live chart and result history."""

from __future__ import annotations

import csv
import json
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ctt.btapp.expectations import all_ok, evaluate
from ctt.btapp.formatting import board_event_line, human_bytes, interval_line, mbps
from ctt.btapp.gui.context import AppContext
from ctt.btapp.gui.widgets import DOWN, RTT, UP, MetricLabel, ThroughputChart
from ctt.btapp.service import ThroughputParams
from ctt.protocol.ctp import MODE_LABELS, Interval, Result


class ThroughputTab(ttk.Frame):
    title = "  2. Throughput  "

    def __init__(self, master, ctx: AppContext):
        super().__init__(master, padding=12)
        self.ctx = ctx
        self.running = False
        self.history: list[dict] = []
        self.v_mode = tk.StringVar(value="tx")
        self.v_dur = tk.StringVar(value=ctx.settings.duration)
        self.v_block = tk.StringVar(value=ctx.settings.block)
        self.v_rate = tk.StringVar(value="")
        self.v_min = tk.StringVar(value="")
        self.v_verify = tk.BooleanVar(value=True)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self._controls().grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        self._results().grid(row=0, column=1, sticky="nsew")

    def apply_to(self, settings) -> None:
        settings.block = self.v_block.get().strip()
        settings.duration = self.v_dur.get().strip()

    def planned_duration(self) -> float:
        try:
            return float(self.v_dur.get())
        except ValueError:
            return 10.0

    # ---- layout --------------------------------------------------------------------------

    def _controls(self):
        left = ttk.Frame(self)
        mf = ttk.LabelFrame(left, text="Direction", padding=8)
        mf.pack(fill="x")
        for m in ("tx", "rx", "bidir", "echo"):
            ttk.Radiobutton(mf, text=MODE_LABELS[m], value=m, variable=self.v_mode).pack(anchor="w")

        of = ttk.LabelFrame(left, text="Parameters", padding=8)
        of.pack(fill="x", pady=8)
        for r, (lbl, var, hint) in enumerate((("Duration (s)", self.v_dur, ""),
                                              ("Block (byte)", self.v_block, "RFCOMM: 990 ≈ 1 frame · BLE: 244 ≈ 1 packet"),
                                              ("Rate limit Mbit/s", self.v_rate, "empty = unlimited"),
                                              ("Min Mbit/s (PASS)", self.v_min,
                                               "empty = only measure (result OK / DATA ERR)"))):
            ttk.Label(of, text=lbl).grid(row=2 * r, column=0, sticky="w")
            ttk.Entry(of, textvariable=var, width=10).grid(row=2 * r, column=1, sticky="w", padx=4)
            if hint:
                ttk.Label(of, text=hint, style="Cap.TLabel").grid(row=2 * r + 1, column=0, columnspan=2,
                                                                 sticky="w")
        ttk.Checkbutton(of, text="Verify CRC of every block", variable=self.v_verify).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.btn_start = ttk.Button(left, text="▶  Start", style="Accent.TButton", command=self.on_start)
        self.btn_start.pack(fill="x", ipady=4)
        self.btn_stop = ttk.Button(left, text="■  Stop", command=self.on_stop, state="disabled")
        self.btn_stop.pack(fill="x", pady=4)

        rf = ttk.LabelFrame(left, text="Result", padding=8)
        rf.pack(fill="x", pady=8)
        self.r_up = MetricLabel(rf, 0, "PC → board", UP)
        self.r_down = MetricLabel(rf, 1, "board → PC", DOWN)
        self.r_rtt = MetricLabel(rf, 2, "RTT average", RTT)
        self.r_err = ttk.Label(rf, text="lost 0 · CRC errors 0", style="Cap.TLabel")
        self.r_err.grid(row=6, column=0, sticky="w", pady=(4, 0))
        return left

    def _results(self):
        right = ttk.Frame(self)
        right.rowconfigure(0, weight=3)
        right.rowconfigure(1, weight=2)
        right.columnconfigure(0, weight=1)
        self.chart = ThroughputChart(right, self.planned_duration)
        self.chart.grid(row=0, column=0, sticky="nsew")

        hf = ttk.Frame(right)
        hf.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        hf.rowconfigure(0, weight=1)
        hf.columnconfigure(0, weight=1)
        cols = (("time", "Time", 70), ("mode", "Direction", 90), ("up", "PC→board Mbit/s", 120),
                ("down", "board→PC Mbit/s", 120), ("rtt", "RTT ms", 80), ("err", "lost/CRC", 70),
                ("result", "Result", 80))
        self.hist = ttk.Treeview(hf, columns=[c[0] for c in cols], show="headings", height=5)
        for c, t, w in cols:
            self.hist.heading(c, text=t)
            self.hist.column(c, width=w, anchor="w")
        self.hist.grid(row=0, column=0, sticky="nsew")
        self.hist.tag_configure("bad", foreground="#c62828")
        hb = ttk.Frame(hf)
        hb.grid(row=1, column=0, sticky="ew", pady=4)
        ttk.Button(hb, text="Save results (CSV + JSON)…", command=self.on_export).pack(side="left")
        ttk.Button(hb, text="Clear history", command=self.clear_history).pack(side="left", padx=4)
        return right

    # ---- run -------------------------------------------------------------------------------

    def _params(self) -> ThroughputParams | None:
        try:
            return ThroughputParams(
                mode=self.v_mode.get(), duration_s=float(self.v_dur.get()), block=int(self.v_block.get()),
                rate_bps=int(float(self.v_rate.get()) * 1e6) if self.v_rate.get().strip() else 0,
                verify=self.v_verify.get())
            if self.v_min.get().strip():
                float(self.v_min.get())
        except ValueError:
            messagebox.showerror("Parameters", "Duration, block, rate limit and min Mbit/s must be numbers.")
            return None

    def _verdict(self, ok: bool) -> str:
        """Min Mbit/s empty = only measuring: OK / DATA ERR (lost, CRC). With a minimum: PASS / FAIL."""
        if self.v_min.get().strip():
            return "PASS" if ok else "FAIL"
        return "OK" if ok else "DATA ERR"

    def _expect(self, r: Result) -> dict:
        try:
            limit = float(self.v_min.get()) if self.v_min.get().strip() else None
        except ValueError:
            limit = None
        if limit is None:
            return {}
        return {"up_mbps_min": limit if r.up_mbps is not None else None,
                "down_mbps_min": limit if r.down_mbps is not None else None}

    def on_start(self):
        if self.running:
            return
        params = self._params()
        if not params:
            return
        self.ctx.sync_settings()
        self._set_running(True)
        for m in (self.r_up, self.r_down, self.r_rtt):
            m.set("–")
        self.chart.reset()
        self.ctx.set_status(f"Measuring {MODE_LABELS[params.mode]}…")
        ui = self.ctx.dispatcher.ui

        def connected(t):
            if self.ctx.settings.uses_ssh:
                ui(self.ctx.log, f"Board: server {t.describe()}", "board")
            ui(self.ctx.log, f"Connect {t.describe()}…")

        self.ctx.dispatcher.background(
            lambda: self.ctx.service.run_throughput(
                params, on_interval=lambda iv: ui(self._on_interval, iv),
                on_board_event=lambda ev: ui(self._on_board_event, ev), on_connect=connected),
            self._on_result, self._on_fail)

    def on_stop(self):
        if self.ctx.service.stop_throughput():
            self.ctx.log("Stopping…")

    def _set_running(self, running: bool) -> None:
        self.running = running
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")

    def _on_board_event(self, ev: dict):
        """Board agent trace (SSH mode): received / sent so far, once per second."""
        if ev.get("event") == "log" and ev.get("message"):
            self.ctx.log(f"Board: {ev['message']}", "board")
            return
        line = board_event_line(ev)
        if line:
            self.ctx.log(line.strip(), "board")

    def _on_interval(self, iv: Interval):
        self.chart.add(iv)
        if iv.up_mbps is not None:
            self.r_up.set(f"{iv.up_mbps:.2f} Mbit/s")
        if iv.down_mbps is not None:
            self.r_down.set(f"{iv.down_mbps:.2f} Mbit/s")
        if iv.rtt_ms is not None:
            self.r_rtt.set(f"{iv.rtt_ms:.1f} ms")
        totals = []
        if iv.up_mbps is not None:
            totals.append(f"sent {human_bytes(iv.up_bytes)} ({iv.up_frames} fr)")
        if iv.down_mbps is not None:
            totals.append(f"received {human_bytes(iv.down_bytes)} ({iv.down_frames} fr)")
        if iv.rtt_ms is not None:
            totals.append(f"{iv.echo_count} echo replies")
        self.r_err.configure(text=" · ".join(totals + [f"lost {iv.lost}", f"CRC errors {iv.crc_errors}"]))
        if iv.rtt_ms is None:
            self.ctx.log(interval_line(iv).strip())

    def _on_fail(self, e):
        self._set_running(False)
        self.ctx.fail(e)

    def _on_result(self, r: Result):
        self._set_running(False)
        self.r_up.set("–" if r.up_mbps is None else f"{r.up_mbps:.2f} Mbit/s")
        self.r_down.set("–" if r.down_mbps is None else f"{r.down_mbps:.2f} Mbit/s")
        rtt = r.rtt_ms.get("avg") if r.rtt_ms else None
        self.r_rtt.set("–" if rtt is None else f"{rtt:.1f} ms")
        self.r_err.configure(text=f"lost {r.lost} · CRC errors {r.crc_errors}")
        checks = evaluate(r, self._expect(r))
        ok = all_ok(checks)
        row = {"time": time.strftime("%H:%M:%S"), "mode": MODE_LABELS[r.mode], "up": mbps(r.up_mbps),
               "down": mbps(r.down_mbps), "rtt": "–" if rtt is None else f"{rtt:.2f}",
               "err": f"{r.lost}/{r.crc_errors}", "result": self._verdict(ok)}
        self.history.append({**row, "detail": r.to_dict(), "board_addr": self.ctx.settings.bt_addr})
        self.hist.insert("", 0, values=list(row.values()), tags=() if ok else ("bad",))
        msg = f"Result {MODE_LABELS[r.mode]}: up {mbps(r.up_mbps)} · down {mbps(r.down_mbps)} Mbit/s"
        if r.rtt_ms:
            msg += (f" · RTT avg {r.rtt_ms['avg']:.2f} p99 {r.rtt_ms['p99']:.2f} "
                    f"max {r.rtt_ms['max']:.2f} ms ({r.rtt_ms['count']} blocks)")
        msg += f" · lost {r.lost} · CRC {r.crc_errors}"
        failed = [c.text() for c in checks if not c.ok]
        if failed:
            msg += " · FAILED: " + "; ".join(failed)
        self.ctx.log(msg, "ok" if ok else "err")
        self.ctx.set_status("Done: " + row["result"])

    # ---- history ----------------------------------------------------------------------------

    def on_export(self):
        if not self.history:
            return messagebox.showinfo("Save", "No results yet.")
        p = filedialog.asksaveasfilename(defaultextension=".csv",
                                         initialfile=time.strftime("ctt-agent-%Y%m%d-%H%M%S.csv"),
                                         filetypes=[("CSV", "*.csv")])
        if not p:
            return
        keys = ["time", "mode", "up", "down", "rtt", "err", "result", "board_addr"]
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(self.history)
        Path(p).with_suffix(".json").write_text(json.dumps(self.history, indent=2, ensure_ascii=False))
        self.ctx.log(f"Saved {p} (+ .json)", "ok")

    def clear_history(self):
        self.history.clear()
        self.hist.delete(*self.hist.get_children())
