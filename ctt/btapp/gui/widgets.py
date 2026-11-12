"""Reusable widgets: log panel, throughput/RTT chart, big metric label."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Callable

from ctt.protocol.ctp import Interval

UP, DOWN, RTT = "#2563eb", "#16a34a", "#9333ea"


class LogPanel(ttk.LabelFrame):
    def __init__(self, master):
        super().__init__(master, text="Nhật ký", padding=4)
        self.box = tk.Text(self, height=7, state="disabled", wrap="word", font=("Consolas", 9))
        sb = ttk.Scrollbar(self, command=self.box.yview)
        self.box.configure(yscrollcommand=sb.set)
        self.box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.box.tag_configure("err", foreground="#c62828")
        self.box.tag_configure("ok", foreground="#17803d")
        self.box.tag_configure("board", foreground="#6b7280")

    def write(self, msg: str, tag: str = "") -> None:
        self.box.configure(state="normal")
        self.box.insert("end", f"{time.strftime('%H:%M:%S')}  {msg}\n", tag)
        self.box.see("end")
        self.box.configure(state="disabled")


class MetricLabel:
    def __init__(self, parent, row: int, caption: str, colour: str):
        ttk.Label(parent, text=caption, style="Cap.TLabel").grid(row=2 * row, column=0, sticky="w")
        self.label = ttk.Label(parent, text="–", style="Big.TLabel", foreground=colour)
        self.label.grid(row=2 * row + 1, column=0, sticky="w")

    def set(self, text: str) -> None:
        self.label.configure(text=text)


class ThroughputChart(tk.Canvas):
    """Live Mbit/s per direction (or RTT in echo mode) against time."""

    def __init__(self, master, planned_duration: Callable[[], float]):
        super().__init__(master, background="white", highlightthickness=1, highlightbackground="#dfe3ea")
        self.series: list[Interval] = []
        self._planned = planned_duration
        self.bind("<Configure>", lambda e: self.redraw())

    def reset(self) -> None:
        self.series = []
        self.redraw()

    def add(self, iv: Interval) -> None:
        self.series.append(iv)
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        w, h = max(self.winfo_width(), 200), max(self.winfo_height(), 120)
        pl, pr, pt, pb = 56, 16, 28, 30
        rtt_mode = any(i.rtt_ms is not None for i in self.series)
        lines = ([("RTT ms", RTT, lambda i: i.rtt_ms)] if rtt_mode else
                 [("PC → board", UP, lambda i: i.up_mbps), ("board → PC", DOWN, lambda i: i.down_mbps)])
        pts = {n: [(i.t, f(i)) for i in self.series if f(i) is not None] for n, _, f in lines}
        allv = [v for p in pts.values() for _, v in p]
        tmax = max([i.t for i in self.series] + [self._planned(), 1.0])
        vmax = max(allv + [0.1]) * 1.15
        self.create_text(pl, 12, text="RTT (ms)" if rtt_mode else "Throughput (Mbit/s)", anchor="w",
                         fill="#5d6778", font=("Segoe UI", 9))
        for k in range(5):
            y = pt + (h - pt - pb) * k / 4
            self.create_line(pl, y, w - pr, y, fill="#eef0f4")
            self.create_text(pl - 6, y, text=f"{vmax * (1 - k / 4):.2f}", anchor="e", fill="#5d6778",
                             font=("Segoe UI", 8))
        self.create_text(w - pr, h - 10, text=f"{tmax:.0f} s", anchor="e", fill="#5d6778",
                         font=("Segoe UI", 8))
        lx = pl + 150
        for name, colour, _ in lines:
            p = pts[name]
            if len(p) >= 2:
                xy = []
                for t, v in p:
                    xy += [pl + (w - pl - pr) * t / tmax, pt + (h - pt - pb) * (1 - v / vmax)]
                self.create_line(*xy, fill=colour, width=2)
            if p:
                self.create_line(lx, 12, lx + 14, 12, fill=colour, width=3)
                self.create_text(lx + 18, 12, text=name, anchor="w", fill="#1c2330", font=("Segoe UI", 9))
                lx += 120
