"""Tab 2: throughput / latency test with live chart and result history."""

from __future__ import annotations

import csv
import json
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ctt.btapp.formatting import mbps, passed
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
        mf = ttk.LabelFrame(left, text="Chiều đo", padding=8)
        mf.pack(fill="x")
        for m in ("tx", "rx", "bidir", "echo"):
            ttk.Radiobutton(mf, text=MODE_LABELS[m], value=m, variable=self.v_mode).pack(anchor="w")

        of = ttk.LabelFrame(left, text="Thông số", padding=8)
        of.pack(fill="x", pady=8)
        for r, (lbl, var, hint) in enumerate((("Thời gian (s)", self.v_dur, ""),
                                              ("Block (byte)", self.v_block, "990 ≈ 1 gói RFCOMM"),
                                              ("Giới hạn Mbit/s", self.v_rate, "trống = tối đa"))):
            ttk.Label(of, text=lbl).grid(row=2 * r, column=0, sticky="w")
            ttk.Entry(of, textvariable=var, width=10).grid(row=2 * r, column=1, sticky="w", padx=4)
            if hint:
                ttk.Label(of, text=hint, style="Cap.TLabel").grid(row=2 * r + 1, column=0, columnspan=2,
                                                                 sticky="w")
        ttk.Checkbutton(of, text="Kiểm tra CRC từng gói", variable=self.v_verify).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.btn_start = ttk.Button(left, text="▶  Bắt đầu đo", style="Accent.TButton", command=self.on_start)
        self.btn_start.pack(fill="x", ipady=4)
        self.btn_stop = ttk.Button(left, text="■  Dừng", command=self.on_stop, state="disabled")
        self.btn_stop.pack(fill="x", pady=4)

        rf = ttk.LabelFrame(left, text="Kết quả", padding=8)
        rf.pack(fill="x", pady=8)
        self.r_up = MetricLabel(rf, 0, "PC → board", UP)
        self.r_down = MetricLabel(rf, 1, "board → PC", DOWN)
        self.r_rtt = MetricLabel(rf, 2, "RTT trung bình", RTT)
        self.r_err = ttk.Label(rf, text="mất gói 0 · lỗi CRC 0", style="Cap.TLabel")
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
        cols = (("time", "Lúc", 70), ("mode", "Chiều", 90), ("up", "PC→board Mbit/s", 120),
                ("down", "board→PC Mbit/s", 120), ("rtt", "RTT ms", 80), ("err", "mất/CRC", 70),
                ("result", "Kết quả", 80))
        self.hist = ttk.Treeview(hf, columns=[c[0] for c in cols], show="headings", height=5)
        for c, t, w in cols:
            self.hist.heading(c, text=t)
            self.hist.column(c, width=w, anchor="w")
        self.hist.grid(row=0, column=0, sticky="nsew")
        self.hist.tag_configure("bad", foreground="#c62828")
        hb = ttk.Frame(hf)
        hb.grid(row=1, column=0, sticky="ew", pady=4)
        ttk.Button(hb, text="Lưu kết quả (CSV + JSON)…", command=self.on_export).pack(side="left")
        ttk.Button(hb, text="Xoá lịch sử", command=self.clear_history).pack(side="left", padx=4)
        return right

    # ---- run -------------------------------------------------------------------------------

    def _params(self) -> ThroughputParams | None:
        try:
            return ThroughputParams(
                mode=self.v_mode.get(), duration_s=float(self.v_dur.get()), block=int(self.v_block.get()),
                rate_bps=int(float(self.v_rate.get()) * 1e6) if self.v_rate.get().strip() else 0,
                verify=self.v_verify.get())
        except ValueError:
            messagebox.showerror("Thông số", "Thời gian, block và giới hạn phải là số.")
            return None

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
        self.ctx.set_status(f"Đang đo {MODE_LABELS[params.mode]}…")
        ui = self.ctx.dispatcher.ui

        def connected(t):
            if self.ctx.settings.uses_ssh:
                ui(self.ctx.log, f"Board: server RFCOMM {t.address} channel {t.channel}", "board")
            ui(self.ctx.log, f"Kết nối RFCOMM {t.address} channel {t.channel}…")

        self.ctx.dispatcher.background(
            lambda: self.ctx.service.run_throughput(
                params, on_interval=lambda iv: ui(self._on_interval, iv),
                on_board_event=lambda ev: ui(self._on_board_event, ev), on_connect=connected),
            self._on_result, self._on_fail)

    def on_stop(self):
        if self.ctx.service.stop_throughput():
            self.ctx.log("Đang dừng…")

    def _set_running(self, running: bool) -> None:
        self.running = running
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")

    def _on_board_event(self, ev: dict):
        e = ev.get("event")
        if e == "session_end":
            self.ctx.log("Board: " + ("phiên đo kết thúc" if ev.get("ok") else
                                      f"phiên lỗi: {ev.get('message')}"), "board")
        elif e in ("error", "warning"):
            self.ctx.log(f"Board {e}: {ev.get('message')}", "board")
        elif e == "log" and ev.get("message"):
            self.ctx.log(f"Board: {ev['message']}", "board")

    def _on_interval(self, iv: Interval):
        self.chart.add(iv)
        if iv.up_mbps is not None:
            self.r_up.set(f"{iv.up_mbps:.2f} Mbit/s")
        if iv.down_mbps is not None:
            self.r_down.set(f"{iv.down_mbps:.2f} Mbit/s")
        if iv.rtt_ms is not None:
            self.r_rtt.set(f"{iv.rtt_ms:.1f} ms")
        self.r_err.configure(text=f"mất gói {iv.lost} · lỗi CRC {iv.crc_errors}")

    def _on_fail(self, e):
        self._set_running(False)
        self.ctx.fail(e)

    def _on_result(self, r: Result):
        self._set_running(False)
        self.r_up.set("–" if r.up_mbps is None else f"{r.up_mbps:.2f} Mbit/s")
        self.r_down.set("–" if r.down_mbps is None else f"{r.down_mbps:.2f} Mbit/s")
        rtt = r.rtt_ms.get("avg") if r.rtt_ms else None
        self.r_rtt.set("–" if rtt is None else f"{rtt:.1f} ms")
        self.r_err.configure(text=f"mất gói {r.lost} · lỗi CRC {r.crc_errors}")
        ok = passed(r) and r.out_of_order == 0
        row = {"time": time.strftime("%H:%M:%S"), "mode": MODE_LABELS[r.mode], "up": mbps(r.up_mbps),
               "down": mbps(r.down_mbps), "rtt": "–" if rtt is None else f"{rtt:.2f}",
               "err": f"{r.lost}/{r.crc_errors}", "result": "PASS" if ok else "FAIL"}
        self.history.append({**row, "detail": r.to_dict(), "board_addr": self.ctx.settings.bt_addr})
        self.hist.insert("", 0, values=list(row.values()), tags=() if ok else ("bad",))
        msg = f"Kết quả {MODE_LABELS[r.mode]}: up {mbps(r.up_mbps)} · down {mbps(r.down_mbps)} Mbit/s"
        if r.rtt_ms:
            msg += (f" · RTT avg {r.rtt_ms['avg']:.2f} p99 {r.rtt_ms['p99']:.2f} "
                    f"max {r.rtt_ms['max']:.2f} ms ({r.rtt_ms['count']} gói)")
        msg += f" · mất {r.lost} · CRC {r.crc_errors}"
        self.ctx.log(msg, "ok" if ok else "err")
        self.ctx.set_status("Đo xong: " + row["result"])

    # ---- history ----------------------------------------------------------------------------

    def on_export(self):
        if not self.history:
            return messagebox.showinfo("Lưu", "Chưa có kết quả.")
        p = filedialog.asksaveasfilename(defaultextension=".csv",
                                         initialfile=time.strftime("ctt-bt-%Y%m%d-%H%M%S.csv"),
                                         filetypes=[("CSV", "*.csv")])
        if not p:
            return
        keys = ["time", "mode", "up", "down", "rtt", "err", "result", "board_addr"]
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(self.history)
        Path(p).with_suffix(".json").write_text(json.dumps(self.history, indent=2, ensure_ascii=False))
        self.ctx.log(f"Đã lưu {p} (+ .json)", "ok")

    def clear_history(self):
        self.history.clear()
        self.hist.delete(*self.hist.get_children())
