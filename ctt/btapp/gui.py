"""Desktop window of the CTT Bluetooth test app (Tkinter, Windows and WSLg)."""

from __future__ import annotations

import csv
import json
import queue
import threading
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ctt import __version__
from ctt.btapp import pairing
from ctt.btapp.board import SERVICE_CHANNEL, Board, bundled_agent, default_config_path
from ctt.btapp.ctp_client import MODE_LABELS, CtpClient, CtpError, Interval, bluetooth_supported, open_rfcomm

UP, DOWN, RTT = "#2563eb", "#16a34a", "#9333ea"
PC_TX, PC_RX, BOARD_TX = "#1d4ed8", "#15803d", "#b45309"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"CTT Bluetooth Test {__version__}")
        root.geometry("1100x760")
        root.minsize(900, 620)
        self.q: queue.Queue = queue.Queue()
        self.cfg_path = default_config_path()
        self.cfg = self._load_cfg()
        self.board: Board | None = None
        self.pair_backend = pairing.backend()
        self.client: CtpClient | None = None
        self.running = False
        self.series: list[Interval] = []
        self.history: list[dict] = []
        self.term_sock = None
        self.board_pipe = None
        self.term_bytes = {"pc_tx": 0, "pc_rx": 0}

        self._style()
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._drain)
        self.log(f"Bluetooth trên PC: {self.pair_backend.name}"
                 f"{'' if bluetooth_supported() else ' (Python này không có socket RFCOMM!)'}")
        self.bg(self.pair_backend.available, self._backend_status)

    # ---- infrastructure ----------------------------------------------------------

    def _style(self):
        s = ttk.Style()
        if "vista" in s.theme_names():
            s.theme_use("vista")
        elif "clam" in s.theme_names():
            s.theme_use("clam")
        s.configure("Big.TLabel", font=("Segoe UI", 20, "bold"))
        s.configure("Cap.TLabel", foreground="#5d6778")
        s.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))

    def _load_cfg(self) -> dict:
        try:
            return json.loads(self.cfg_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save_cfg(self):
        self.cfg.update(board=self.v_board.get().strip(), no_ssh=self.v_nossh.get(),
                        bt_addr=self.v_addr.get().strip(), channel=self.v_chan.get().strip(),
                        block=self.v_block.get().strip(), duration=self.v_dur.get().strip())
        try:
            self.cfg_path.parent.mkdir(parents=True, exist_ok=True)
            self.cfg_path.write_text(json.dumps(self.cfg, indent=2))
        except OSError:
            pass

    def bg(self, fn, done=None, fail=None):
        """Runs fn() in a worker thread; done(result) / fail(exc) on the UI thread."""
        def work():
            try:
                r = fn()
            except Exception as e:  # noqa: BLE001
                self.q.put(("call", fail or self._fail, (e,)))
                return
            if done:
                self.q.put(("call", done, (r,)))
        threading.Thread(target=work, daemon=True).start()

    def ui(self, fn, *args):
        """Schedules fn(*args) on the UI thread (from any thread)."""
        self.q.put(("call", fn, args))

    def _drain(self):
        try:
            while True:
                kind, fn, args = self.q.get_nowait()
                if kind == "call":
                    try:
                        fn(*args)
                    except Exception:  # noqa: BLE001
                        self.log(traceback.format_exc(), "err")
        except queue.Empty:
            pass
        self.root.after(50, self._drain)

    def _fail(self, e: Exception):
        self.log(f"Lỗi: {e}", "err")
        self.set_status(f"Lỗi: {e}")

    def log(self, msg: str, tag: str = ""):
        t = time.strftime("%H:%M:%S")
        self.logbox.configure(state="normal")
        self.logbox.insert("end", f"{t}  {msg}\n", tag)
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def set_status(self, msg: str):
        self.status.configure(text=msg)

    # ---- layout --------------------------------------------------------------------

    def _build(self):
        top = ttk.Frame(self.root, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="CTT Bluetooth Test", font=("Segoe UI", 13, "bold")).pack(side="left")
        self.status = ttk.Label(top, text="Sẵn sàng", style="Cap.TLabel")
        self.status.pack(side="right")

        paned = ttk.PanedWindow(self.root, orient="vertical")
        paned.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        nb = ttk.Notebook(paned)
        self.nb = nb
        paned.add(nb, weight=4)
        self._tab_connect(nb)
        self._tab_throughput(nb)
        self._tab_terminal(nb)

        logf = ttk.LabelFrame(paned, text="Nhật ký", padding=4)
        paned.add(logf, weight=1)
        self.logbox = tk.Text(logf, height=7, state="disabled", wrap="word", font=("Consolas", 9))
        sb = ttk.Scrollbar(logf, command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=sb.set)
        self.logbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.logbox.tag_configure("err", foreground="#c62828")
        self.logbox.tag_configure("ok", foreground="#17803d")
        self.logbox.tag_configure("board", foreground="#6b7280")

    # ---- tab 1: connection -------------------------------------------------------------

    def _tab_connect(self, nb):
        f = ttk.Frame(nb, padding=12)
        nb.add(f, text="  1. Kết nối  ")
        f.columnconfigure(0, weight=1)

        bf = ttk.LabelFrame(f, text="Board board (SSH, dùng để chạy agent trên board)", padding=10)
        bf.grid(row=0, column=0, sticky="ew")
        self.v_board = tk.StringVar(value=self.cfg.get("board", "root@192.168.1.10"))
        self.v_nossh = tk.BooleanVar(value=self.cfg.get("no_ssh", False))
        ttk.Label(bf, text="SSH").grid(row=0, column=0, sticky="w")
        ttk.Entry(bf, textvariable=self.v_board, width=30).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Button(bf, text="Kiểm tra board", command=self.on_check).grid(row=0, column=2, padx=3)
        ttk.Button(bf, text="Deploy agent", command=self.on_deploy).grid(row=0, column=3, padx=3)
        ttk.Checkbutton(bf, text=f"Không dùng SSH (agent chạy sẵn dạng service, channel {SERVICE_CHANNEL})",
                        variable=self.v_nossh).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self.board_info = ttk.Label(bf, text="Chưa kiểm tra", style="Cap.TLabel")
        self.board_info.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))

        pf = ttk.LabelFrame(f, text="Bluetooth trên PC (TP-Link dongle): tìm và pair với board", padding=10)
        pf.grid(row=1, column=0, sticky="nsew", pady=10)
        f.rowconfigure(1, weight=1)
        pf.columnconfigure(0, weight=1)
        pf.rowconfigure(1, weight=1)
        bar = ttk.Frame(pf)
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Button(bar, text="Scan", command=self.on_scan).pack(side="left")
        ttk.Button(bar, text="Thiết bị đã pair", command=self.on_paired).pack(side="left", padx=4)
        ttk.Button(bar, text="Pair", style="Accent.TButton", command=self.on_pair).pack(side="left", padx=4)
        ttk.Button(bar, text="Bỏ pair", command=self.on_unpair).pack(side="left")
        self.v_accept = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Board tự chấp nhận pairing (qua SSH)",
                        variable=self.v_accept).pack(side="left", padx=12)
        self.backend_lbl = ttk.Label(bar, text="", style="Cap.TLabel")
        self.backend_lbl.pack(side="right")

        self.devtree = ttk.Treeview(pf, columns=("name", "addr", "paired"), show="headings", height=8)
        for c, t, w in (("name", "Tên", 260), ("addr", "Địa chỉ", 160), ("paired", "Đã pair", 80)):
            self.devtree.heading(c, text=t)
            self.devtree.column(c, width=w, anchor="w")
        self.devtree.grid(row=1, column=0, sticky="nsew", pady=6)
        self.devtree.bind("<<TreeviewSelect>>", self._on_dev_select)

        tf = ttk.LabelFrame(f, text="Đích kết nối", padding=10)
        tf.grid(row=2, column=0, sticky="ew")
        self.v_addr = tk.StringVar(value=self.cfg.get("bt_addr", ""))
        self.v_chan = tk.StringVar(value=self.cfg.get("channel", "auto"))
        ttk.Label(tf, text="Địa chỉ BT của board").grid(row=0, column=0, sticky="w")
        ttk.Entry(tf, textvariable=self.v_addr, width=22).grid(row=0, column=1, padx=6, sticky="w")
        ttk.Label(tf, text="RFCOMM channel").grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Entry(tf, textvariable=self.v_chan, width=8).grid(row=0, column=3, padx=6, sticky="w")
        ttk.Label(tf, text="auto = lấy từ agent trên board qua SSH", style="Cap.TLabel").grid(
            row=0, column=4, sticky="w")

    def _backend_status(self, r):
        ok, msg = r
        self.backend_lbl.configure(text=f"{self.pair_backend.name}: {'OK' if ok else msg}")
        if not ok:
            self.log(f"Bluetooth PC: {msg}", "err")

    def _get_board(self) -> Board:
        target = self.v_board.get().strip()
        if not target:
            raise RuntimeError("nhập SSH của board, vd root@192.168.1.10")
        if not self.board or self.board.target != target:
            if self.board:
                self.board.close()
            self.board = Board(target)
        return self.board

    def on_check(self):
        self.set_status("Đang kiểm tra board…")

        def work():
            return self._get_board().check()

        def done(info):
            fw = info.get("fw", {})
            svc = fw.get("services", {})
            parts = [f"{info['hostname'] or '?'} ({info['arch']})",
                     f"agent: {info['agent'] or 'CHƯA CÓ → bấm Deploy agent'}",
                     f"BT: {info.get('bt_address') or '?'}",
                     f"bt-daemon: {svc.get('bt-daemon', '-')}"]
            if isinstance(fw.get("bt", {}).get("paired"), int):
                parts.append(f"bond: {fw['bt']['paired']}/5")
            self.board_info.configure(text="   ·   ".join(parts))
            if info.get("bt_address") and not self.v_addr.get().strip():
                self.v_addr.set(info["bt_address"])
            self.log("Board OK: " + " · ".join(parts), "ok")
            self.set_status("Board OK")
        self.bg(work, done)

    def on_deploy(self):
        agent = bundled_agent()
        if not agent:
            p = filedialog.askopenfilename(title="Chọn file ctt-agent (aarch64)")
            if not p:
                return
            agent = Path(p)
        self.set_status("Đang copy agent lên board…")
        self.bg(lambda: self._get_board().deploy(agent),
                lambda v: (self.log(f"Deploy OK: {v}", "ok"), self.set_status("Deploy OK")))

    def on_scan(self):
        self.set_status("Đang scan Bluetooth (khoảng 10–15 s)…")
        self.devtree.delete(*self.devtree.get_children())
        self.bg(lambda: self.pair_backend.scan(10), self._show_devices)

    def on_paired(self):
        self.bg(self.pair_backend.paired, self._show_devices)

    def _show_devices(self, devs):
        self.devtree.delete(*self.devtree.get_children())
        for d in devs:
            self.devtree.insert("", "end", values=(d.name or "(không tên)", d.address,
                                                   "có" if d.paired else ""))
        self.set_status(f"{len(devs)} thiết bị")
        self.log(f"Tìm thấy {len(devs)} thiết bị")

    def _on_dev_select(self, _e):
        sel = self.devtree.selection()
        if sel:
            self.v_addr.set(self.devtree.item(sel[0])["values"][1])

    def on_pair(self):
        addr = self.v_addr.get().strip().upper()
        if not addr:
            return messagebox.showinfo("Pair", "Chọn board trong danh sách hoặc nhập địa chỉ BT.")
        use_ssh = self.v_accept.get() and not self.v_nossh.get()
        self.set_status(f"Đang pair {addr}…")

        def work():
            acc = None
            if use_ssh:
                acc = self._get_board().acceptor()
                acc.__enter__()
                self.ui(self.log, f"Board: pairable + discoverable ({type(acc).__name__})")
            try:
                return self.pair_backend.pair(
                    addr, on_pin=lambda pin: self.ui(self.log, f"Mã xác nhận (numeric comparison): {pin}"))
            finally:
                if acc:
                    acc.close()

        def done(r):
            ok, msg = r
            self.log(f"Pair {addr}: {msg}", "ok" if ok else "err")
            self.set_status(msg)
            if ok:
                self.on_paired()
        self.bg(work, done)

    def on_unpair(self):
        addr = self.v_addr.get().strip().upper()
        if not addr or not messagebox.askyesno("Bỏ pair", f"Bỏ pair {addr} trên PC?"):
            return
        self.bg(lambda: self.pair_backend.unpair(addr),
                lambda r: (self.log(f"Bỏ pair {addr}: {r[1]}", "ok" if r[0] else "err"), self.on_paired()))

    # ---- tab 2: throughput -------------------------------------------------------------

    def _tab_throughput(self, nb):
        f = ttk.Frame(nb, padding=12)
        nb.add(f, text="  2. Throughput  ")
        f.columnconfigure(1, weight=1)
        f.rowconfigure(0, weight=1)

        left = ttk.Frame(f)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        mf = ttk.LabelFrame(left, text="Chiều đo", padding=8)
        mf.pack(fill="x")
        self.v_mode = tk.StringVar(value="tx")
        for m in ("tx", "rx", "bidir", "echo"):
            ttk.Radiobutton(mf, text=MODE_LABELS[m], value=m, variable=self.v_mode).pack(anchor="w")

        of = ttk.LabelFrame(left, text="Thông số", padding=8)
        of.pack(fill="x", pady=8)
        self.v_dur = tk.StringVar(value=self.cfg.get("duration", "10"))
        self.v_block = tk.StringVar(value=self.cfg.get("block", "990"))
        self.v_rate = tk.StringVar(value="")
        self.v_verify = tk.BooleanVar(value=True)
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
        self.r_up = self._metric(rf, 0, "PC → board", UP)
        self.r_down = self._metric(rf, 1, "board → PC", DOWN)
        self.r_rtt = self._metric(rf, 2, "RTT trung bình", RTT)
        self.r_err = ttk.Label(rf, text="mất gói 0 · lỗi CRC 0", style="Cap.TLabel")
        self.r_err.grid(row=6, column=0, sticky="w", pady=(4, 0))

        right = ttk.Frame(f)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=3)
        right.rowconfigure(1, weight=2)
        right.columnconfigure(0, weight=1)
        self.chart = tk.Canvas(right, background="white", highlightthickness=1,
                               highlightbackground="#dfe3ea")
        self.chart.grid(row=0, column=0, sticky="nsew")
        self.chart.bind("<Configure>", lambda e: self._draw_chart())

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
        ttk.Button(hb, text="Xoá lịch sử", command=self._clear_hist).pack(side="left", padx=4)

    def _metric(self, parent, row, caption, colour):
        ttk.Label(parent, text=caption, style="Cap.TLabel").grid(row=2 * row, column=0, sticky="w")
        lbl = ttk.Label(parent, text="–", style="Big.TLabel", foreground=colour)
        lbl.grid(row=2 * row + 1, column=0, sticky="w")
        return lbl

    def _target(self) -> tuple[str, int]:
        """(board address, channel), starting the board server over SSH when needed."""
        addr = self.v_addr.get().strip().upper()
        chan = self.v_chan.get().strip().lower()
        if self.v_nossh.get():
            if not addr:
                raise RuntimeError("nhập địa chỉ BT của board (tab Kết nối)")
            return addr, SERVICE_CHANNEL if chan in ("", "auto") else int(chan)
        board = self._get_board()
        if not board.find_agent():
            raise RuntimeError("board chưa có ctt-agent: tab Kết nối → Deploy agent")
        ch = 0 if chan in ("", "auto") else int(chan)
        b_addr, b_ch = board.start_server(ch, on_event=self._board_event)
        self.ui(self.log, f"Board: server RFCOMM {b_addr} channel {b_ch}", "board")
        return addr or b_addr, b_ch

    def _board_event(self, ev: dict):
        e = ev.get("event")
        if e == "session_end":
            self.ui(self.log, "Board: " + ("phiên đo kết thúc" if ev.get("ok") else
                                           f"phiên lỗi: {ev.get('message')}"), "board")
        elif e in ("error", "warning"):
            self.ui(self.log, f"Board {e}: {ev.get('message')}", "board")
        elif e == "log" and ev.get("message"):
            self.ui(self.log, f"Board: {ev['message']}", "board")

    def on_start(self):
        if self.running:
            return
        try:
            mode = self.v_mode.get()
            dur = float(self.v_dur.get())
            block = int(self.v_block.get())
            rate = int(float(self.v_rate.get()) * 1e6) if self.v_rate.get().strip() else 0
        except ValueError:
            return messagebox.showerror("Thông số", "Thời gian, block và giới hạn phải là số.")
        self._save_cfg()
        self.running = True
        self.series = []
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        for lbl in (self.r_up, self.r_down, self.r_rtt):
            lbl.configure(text="–")
        self._draw_chart()
        self.set_status(f"Đang đo {MODE_LABELS[mode]}…")
        verify = self.v_verify.get()

        def work():
            addr, ch = self._target()
            self.ui(self.log, f"Kết nối RFCOMM {addr} channel {ch}…")
            sock = open_rfcomm(addr, ch)
            try:
                self.client = CtpClient(sock)
                return self.client.run(mode, dur, block, rate, verify,
                                       on_interval=lambda iv: self.ui(self._on_interval, iv))
            finally:
                self.client = None
                sock.close()

        self.bg(work, self._on_result, self._on_run_fail)

    def on_stop(self):
        if self.client:
            self.client.stop()
            self.log("Đang dừng…")

    def _on_interval(self, iv: Interval):
        self.series.append(iv)
        if iv.up_mbps is not None:
            self.r_up.configure(text=f"{iv.up_mbps:.2f} Mbit/s")
        if iv.down_mbps is not None:
            self.r_down.configure(text=f"{iv.down_mbps:.2f} Mbit/s")
        if iv.rtt_ms is not None:
            self.r_rtt.configure(text=f"{iv.rtt_ms:.1f} ms")
        self.r_err.configure(text=f"mất gói {iv.lost} · lỗi CRC {iv.crc_errors}")
        self._draw_chart()

    def _end_run(self):
        self.running = False
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    def _on_run_fail(self, e):
        self._end_run()
        self._fail(e)

    def _on_result(self, r):
        self._end_run()
        fmt = lambda v: "–" if v is None else f"{v:.3f}"  # noqa: E731
        self.r_up.configure(text="–" if r.up_mbps is None else f"{r.up_mbps:.2f} Mbit/s")
        self.r_down.configure(text="–" if r.down_mbps is None else f"{r.down_mbps:.2f} Mbit/s")
        rtt = r.rtt_ms.get("avg") if r.rtt_ms else None
        self.r_rtt.configure(text="–" if rtt is None else f"{rtt:.1f} ms")
        self.r_err.configure(text=f"mất gói {r.lost} · lỗi CRC {r.crc_errors}")
        ok = r.lost == 0 and r.crc_errors == 0 and r.out_of_order == 0
        row = {"time": time.strftime("%H:%M:%S"), "mode": MODE_LABELS[r.mode], "up": fmt(r.up_mbps),
               "down": fmt(r.down_mbps), "rtt": "–" if rtt is None else f"{rtt:.2f}",
               "err": f"{r.lost}/{r.crc_errors}", "result": "PASS" if ok else "FAIL"}
        self.history.append({**row, "detail": r.to_dict(), "board_addr": self.v_addr.get()})
        self.hist.insert("", 0, values=list(row.values()), tags=() if ok else ("bad",))
        msg = f"Kết quả {MODE_LABELS[r.mode]}: up {fmt(r.up_mbps)} · down {fmt(r.down_mbps)} Mbit/s"
        if r.rtt_ms:
            msg += (f" · RTT avg {r.rtt_ms['avg']:.2f} p99 {r.rtt_ms['p99']:.2f} "
                    f"max {r.rtt_ms['max']:.2f} ms ({r.rtt_ms['count']} gói)")
        msg += f" · mất {r.lost} · CRC {r.crc_errors}"
        self.log(msg, "ok" if ok else "err")
        self.set_status("Đo xong: " + row["result"])

    def _draw_chart(self):
        c = self.chart
        c.delete("all")
        w, h = max(c.winfo_width(), 200), max(c.winfo_height(), 120)
        pl, pr, pt, pb = 56, 16, 28, 30
        rtt_mode = any(i.rtt_ms is not None for i in self.series)
        lines = ([("RTT ms", RTT, lambda i: i.rtt_ms)] if rtt_mode else
                 [("PC → board", UP, lambda i: i.up_mbps), ("board → PC", DOWN, lambda i: i.down_mbps)])
        pts = {n: [(i.t, f(i)) for i in self.series if f(i) is not None] for n, _, f in lines}
        allv = [v for p in pts.values() for _, v in p]
        try:
            planned = float(self.v_dur.get())
        except ValueError:
            planned = 10.0
        tmax = max([i.t for i in self.series] + [planned, 1.0])
        vmax = max(allv + [0.1]) * 1.15
        c.create_text(pl, 12, text="RTT (ms)" if rtt_mode else "Throughput (Mbit/s)", anchor="w",
                      fill="#5d6778", font=("Segoe UI", 9))
        for k in range(5):
            y = pt + (h - pt - pb) * k / 4
            c.create_line(pl, y, w - pr, y, fill="#eef0f4")
            c.create_text(pl - 6, y, text=f"{vmax * (1 - k / 4):.2f}", anchor="e", fill="#5d6778",
                          font=("Segoe UI", 8))
        c.create_text(w - pr, h - 10, text=f"{tmax:.0f} s", anchor="e", fill="#5d6778", font=("Segoe UI", 8))
        lx = pl + 150
        for name, colour, _ in lines:
            p = pts[name]
            if len(p) >= 2:
                xy = []
                for t, v in p:
                    xy += [pl + (w - pl - pr) * t / tmax, pt + (h - pt - pb) * (1 - v / vmax)]
                c.create_line(*xy, fill=colour, width=2)
            if p:
                c.create_line(lx, 12, lx + 14, 12, fill=colour, width=3)
                c.create_text(lx + 18, 12, text=name, anchor="w", fill="#1c2330", font=("Segoe UI", 9))
                lx += 120

    def on_export(self):
        if not self.history:
            return messagebox.showinfo("Lưu", "Chưa có kết quả.")
        p = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=time.strftime("ctt-bt-%Y%m%d-%H%M%S.csv"),
                                         filetypes=[("CSV", "*.csv")])
        if not p:
            return
        keys = ["time", "mode", "up", "down", "rtt", "err", "result", "board_addr"]
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(self.history)
        Path(p).with_suffix(".json").write_text(json.dumps(self.history, indent=2, ensure_ascii=False))
        self.log(f"Đã lưu {p} (+ .json)", "ok")

    def _clear_hist(self):
        self.history.clear()
        self.hist.delete(*self.hist.get_children())

    # ---- tab 3: terminal --------------------------------------------------------------

    def _tab_terminal(self, nb):
        f = ttk.Frame(nb, padding=12)
        nb.add(f, text="  3. Terminal  ")
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(1, weight=1)

        bar = ttk.Frame(f)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.btn_term = ttk.Button(bar, text="Kết nối", style="Accent.TButton", command=self.on_term_toggle)
        self.btn_term.pack(side="left")
        self.v_fmt = tk.StringVar(value="text")
        ttk.Label(bar, text="  Dạng:").pack(side="left")
        ttk.Radiobutton(bar, text="Chữ", value="text", variable=self.v_fmt).pack(side="left")
        ttk.Radiobutton(bar, text="Hex", value="hex", variable=self.v_fmt).pack(side="left")
        ttk.Label(bar, text="  Kết thúc dòng:").pack(side="left")
        self.v_eol = tk.StringVar(value="\\n")
        ttk.Combobox(bar, textvariable=self.v_eol, values=["(không)", "\\n", "\\r\\n"], width=7,
                     state="readonly").pack(side="left")
        self.term_info = ttk.Label(bar, text="Chưa kết nối", style="Cap.TLabel")
        self.term_info.pack(side="right")

        self.pc_box, self.v_pc_in = self._term_pane(f, 0, "PC (máy tính)", "Gửi từ PC → board", self.on_pc_send)
        self.bd_box, self.v_bd_in = self._term_pane(f, 1, "Board board (qua SSH)", "Board gửi → PC",
                                                    self.on_board_send)
        for box in (self.pc_box, self.bd_box):
            box.tag_configure("pc_tx", foreground=PC_TX)
            box.tag_configure("pc_rx", foreground=PC_RX)
            box.tag_configure("bd_tx", foreground=BOARD_TX)
            box.tag_configure("meta", foreground="#6b7280")
        ttk.Label(f, text="Có SSH: board chạy `ctt-agent pipe`, nên thấy được cả dữ liệu board nhận và gõ "
                          "được dữ liệu board gửi. Không dùng SSH: PC nối vào channel đã nhập (server RFCOMM "
                          "bất kỳ trên board), chỉ dùng được khung PC.",
                  style="Cap.TLabel", wraplength=1000).grid(row=3, column=0, columnspan=2, sticky="w",
                                                            pady=(6, 0))

    def _term_pane(self, parent, col, title, send_text, cmd):
        lf = ttk.LabelFrame(parent, text=title, padding=6)
        lf.grid(row=1, column=col, sticky="nsew", padx=(0, 6) if col == 0 else (6, 0))
        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)
        box = tk.Text(lf, state="disabled", wrap="char", font=("Consolas", 10))
        box.grid(row=0, column=0, columnspan=2, sticky="nsew")
        v = tk.StringVar()
        e = ttk.Entry(lf, textvariable=v)
        e.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        e.bind("<Return>", lambda _e: cmd())
        ttk.Button(lf, text=send_text, command=cmd).grid(row=1, column=1, padx=(6, 0), pady=(6, 0))
        return box, v

    def _term_write(self, box, label, data: bytes, tag):
        if self.v_fmt.get() == "hex":
            shown = data.hex(" ")
        else:
            shown = data.decode("utf-8", errors="replace").replace("\r", "\\r").replace("\n", "\\n")
        box.configure(state="normal")
        box.insert("end", f"{time.strftime('%H:%M:%S')} {label} [{len(data)} B] ", "meta")
        box.insert("end", shown + "\n", tag)
        box.see("end")
        box.configure(state="disabled")

    def _term_meta(self, box, msg):
        box.configure(state="normal")
        box.insert("end", f"{time.strftime('%H:%M:%S')} {msg}\n", "meta")
        box.see("end")
        box.configure(state="disabled")

    def _payload(self, text: str) -> bytes:
        if self.v_fmt.get() == "hex":
            return bytes.fromhex(text.replace(" ", ""))
        eol = {"(không)": "", "\\n": "\n", "\\r\\n": "\r\n"}[self.v_eol.get()]
        return (text + eol).encode("utf-8")

    def on_term_toggle(self):
        if self.term_sock:
            return self._term_close("Đã ngắt kết nối")
        self._save_cfg()
        use_ssh = not self.v_nossh.get()
        self.btn_term.configure(state="disabled")
        self.set_status("Đang mở terminal…")

        def work():
            addr = self.v_addr.get().strip().upper()
            chan = self.v_chan.get().strip().lower()
            if use_ssh:
                board = self._get_board()
                if not board.find_agent():
                    raise RuntimeError("board chưa có ctt-agent: tab Kết nối → Deploy agent")
                board.stop_server()   # the server may hold the channel
                pipe = board.start_pipe(0 if chan in ("", "auto") else int(chan),
                                        on_event=lambda ev: self.ui(self._on_pipe_event, ev))
                b_addr, ch = pipe.endpoint
                addr = addr or b_addr
            else:
                pipe = None
                if not addr:
                    raise RuntimeError("nhập địa chỉ BT của board")
                ch = SERVICE_CHANNEL if chan in ("", "auto") else int(chan)
            try:
                sock = open_rfcomm(addr, ch)
            except Exception:
                if pipe:
                    pipe.close()
                raise
            return sock, pipe, addr, ch

        def done(r):
            self.term_sock, self.board_pipe, addr, ch = r
            self.btn_term.configure(text="Ngắt kết nối", state="normal")
            self.term_info.configure(text=f"Đã kết nối {addr} channel {ch}")
            self._term_meta(self.pc_box, f"— kết nối tới {addr} / channel {ch}")
            if not self.board_pipe:
                self._term_meta(self.bd_box, "— (không có SSH: không xem được phía board)")
            self.set_status("Terminal đã kết nối")
            threading.Thread(target=self._pc_reader, args=(self.term_sock,), daemon=True).start()

        def fail(e):
            self.btn_term.configure(state="normal")
            self._fail(e)
        self.bg(work, done, fail)

    def _pc_reader(self, sock):
        try:
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                self.ui(self._term_write, self.pc_box, "board → PC", data, "pc_rx")
        except OSError:
            pass
        if self.term_sock is sock:
            self.ui(self._term_close, "Board đã đóng kết nối")

    def _on_pipe_event(self, ev):
        e = ev.get("event")
        if e == "rx":
            self._term_write(self.bd_box, "board nhận", ev.get("data", b""), "pc_tx")
        elif e == "connected":
            self._term_meta(self.bd_box, f"— PC đã kết nối: {ev.get('peer')}")
        elif e == "listening":
            self._term_meta(self.bd_box, f"— board đang chờ trên channel {ev.get('channel')}")
        elif e in ("closed", "error"):
            self._term_meta(self.bd_box, f"— {ev.get('reason') or ev.get('message')}")

    def on_pc_send(self):
        if not self.term_sock:
            return self.log("Terminal chưa kết nối", "err")
        try:
            data = self._payload(self.v_pc_in.get())
        except ValueError:
            return messagebox.showerror("Hex", "Chuỗi hex không hợp lệ")
        try:
            self.term_sock.sendall(data)
        except OSError as e:
            return self._term_close(f"Gửi lỗi: {e}")
        self._term_write(self.pc_box, "PC → board", data, "pc_tx")
        self.v_pc_in.set("")

    def on_board_send(self):
        if not self.board_pipe:
            return self.log("Cần SSH tới board để board gửi dữ liệu", "err")
        try:
            data = self._payload(self.v_bd_in.get())
        except ValueError:
            return messagebox.showerror("Hex", "Chuỗi hex không hợp lệ")
        pipe = self.board_pipe

        def sent(_r):
            self._term_write(self.bd_box, "board gửi", data, "bd_tx")
            self.v_bd_in.set("")
        self.bg(lambda: pipe.send(data), sent)

    def _term_close(self, why: str):
        sock, pipe = self.term_sock, self.board_pipe
        self.term_sock = self.board_pipe = None
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        if pipe:
            threading.Thread(target=pipe.close, daemon=True).start()
        self.btn_term.configure(text="Kết nối", state="normal")
        self.term_info.configure(text="Chưa kết nối")
        self._term_meta(self.pc_box, f"— {why}")
        self.set_status(why)

    # ---- shutdown --------------------------------------------------------------------

    def _on_close(self):
        self._save_cfg()
        if self.client:
            self.client.stop()
        if self.term_sock:
            self._term_close("đóng app")
        board = self.board
        if board:
            threading.Thread(target=board.close, daemon=True).start()
        self.root.after(300, self.root.destroy)


def _smoke(root: tk.Tk, target: str) -> None:
    """CI check (CTT_BT_SMOKE): window built; optional screenshot; then exit."""
    def finish():
        if target.lower().endswith(".png"):
            try:
                from PIL import ImageGrab
                root.update()
                x, y = root.winfo_rootx(), root.winfo_rooty()
                ImageGrab.grab((x, y, x + root.winfo_width(), y + root.winfo_height())).save(target)
            except Exception as e:  # noqa: BLE001 - screenshot is optional
                print(f"smoke: no screenshot ({e})")
        root.destroy()
    root.after(3000, finish)


def main() -> int:
    import os
    root = tk.Tk()
    App(root)
    if os.environ.get("CTT_BT_SMOKE"):
        _smoke(root, os.environ["CTT_BT_SMOKE"])
    root.mainloop()
    return 0
