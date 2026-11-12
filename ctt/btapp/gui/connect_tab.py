"""Tab 1: board over SSH, PC-side discovery and pairing, connection target."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ctt.bluetooth.board import SERVICE_CHANNEL, bundled_agent
from ctt.btapp.gui.context import AppContext


class ConnectTab(ttk.Frame):
    title = "  1. Kết nối  "

    def __init__(self, master, ctx: AppContext):
        super().__init__(master, padding=12)
        self.ctx = ctx
        s = ctx.settings
        self.v_board = tk.StringVar(value=s.board)
        self.v_nossh = tk.BooleanVar(value=s.no_ssh)
        self.v_accept = tk.BooleanVar(value=True)
        self.v_addr = tk.StringVar(value=s.bt_addr)
        self.v_chan = tk.StringVar(value=s.channel)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._board_frame().grid(row=0, column=0, sticky="ew")
        self._pairing_frame().grid(row=1, column=0, sticky="nsew", pady=10)
        self._target_frame().grid(row=2, column=0, sticky="ew")

    def apply_to(self, settings) -> None:
        settings.board = self.v_board.get().strip()
        settings.no_ssh = self.v_nossh.get()
        settings.bt_addr = self.v_addr.get().strip()
        settings.channel = self.v_chan.get().strip()

    # ---- layout ------------------------------------------------------------------------

    def _board_frame(self):
        bf = ttk.LabelFrame(self, text="Board board (SSH, dùng để chạy agent trên board)", padding=10)
        ttk.Label(bf, text="SSH").grid(row=0, column=0, sticky="w")
        ttk.Entry(bf, textvariable=self.v_board, width=30).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Button(bf, text="Kiểm tra board", command=self.on_check).grid(row=0, column=2, padx=3)
        ttk.Button(bf, text="Deploy agent", command=self.on_deploy).grid(row=0, column=3, padx=3)
        ttk.Checkbutton(bf, text=f"Không dùng SSH (agent chạy sẵn dạng service, channel {SERVICE_CHANNEL})",
                        variable=self.v_nossh).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self.board_info = ttk.Label(bf, text="Chưa kiểm tra", style="Cap.TLabel")
        self.board_info.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        return bf

    def _pairing_frame(self):
        pf = ttk.LabelFrame(self, text="Bluetooth trên PC (USB dongle): tìm và pair với board", padding=10)
        pf.columnconfigure(0, weight=1)
        pf.rowconfigure(1, weight=1)
        bar = ttk.Frame(pf)
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Button(bar, text="Scan", command=self.on_scan).pack(side="left")
        ttk.Button(bar, text="Thiết bị đã pair", command=self.on_paired).pack(side="left", padx=4)
        ttk.Button(bar, text="Pair", style="Accent.TButton", command=self.on_pair).pack(side="left", padx=4)
        ttk.Button(bar, text="Bỏ pair", command=self.on_unpair).pack(side="left")
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
        return pf

    def _target_frame(self):
        tf = ttk.LabelFrame(self, text="Đích kết nối", padding=10)
        ttk.Label(tf, text="Địa chỉ BT của board").grid(row=0, column=0, sticky="w")
        ttk.Entry(tf, textvariable=self.v_addr, width=22).grid(row=0, column=1, padx=6, sticky="w")
        ttk.Label(tf, text="RFCOMM channel").grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Entry(tf, textvariable=self.v_chan, width=8).grid(row=0, column=3, padx=6, sticky="w")
        ttk.Label(tf, text="auto = lấy từ agent trên board qua SSH", style="Cap.TLabel").grid(
            row=0, column=4, sticky="w")
        return tf

    # ---- actions -------------------------------------------------------------------------

    def show_backend_status(self, r) -> None:
        ok, msg = r
        self.backend_lbl.configure(text=f"{self.ctx.service.pairing.name}: {'OK' if ok else msg}")
        if not ok:
            self.ctx.log(f"Bluetooth PC: {msg}", "err")

    def on_check(self):
        self.ctx.sync_settings()
        self.ctx.set_status("Đang kiểm tra board…")

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
            self.v_addr.set(self.ctx.settings.bt_addr)
            self.ctx.log("Board OK: " + " · ".join(parts), "ok")
            self.ctx.set_status("Board OK")
        self.ctx.dispatcher.background(self.ctx.service.check_board, done)

    def on_deploy(self):
        self.ctx.sync_settings()
        agent = bundled_agent()
        if not agent:
            p = filedialog.askopenfilename(title="Chọn file ctt-agent (aarch64)")
            if not p:
                return
            agent = Path(p)
        self.ctx.set_status("Đang copy agent lên board…")
        self.ctx.dispatcher.background(
            lambda: self.ctx.service.deploy_agent(agent),
            lambda v: (self.ctx.log(f"Deploy OK: {v}", "ok"), self.ctx.set_status("Deploy OK")))

    def on_scan(self):
        self.ctx.set_status("Đang scan Bluetooth (khoảng 10–15 s)…")
        self.devtree.delete(*self.devtree.get_children())
        self.ctx.dispatcher.background(lambda: self.ctx.service.scan(10), self._show_devices)

    def on_paired(self):
        self.ctx.dispatcher.background(self.ctx.service.paired, self._show_devices)

    def _show_devices(self, devs):
        self.devtree.delete(*self.devtree.get_children())
        for d in devs:
            self.devtree.insert("", "end", values=(d.name or "(không tên)", d.address,
                                                   "có" if d.paired else ""))
        self.ctx.set_status(f"{len(devs)} thiết bị")
        self.ctx.log(f"Tìm thấy {len(devs)} thiết bị")

    def _on_dev_select(self, _e):
        sel = self.devtree.selection()
        if sel:
            self.v_addr.set(self.devtree.item(sel[0])["values"][1])

    def on_pair(self):
        addr = self.v_addr.get().strip().upper()
        if not addr:
            return messagebox.showinfo("Pair", "Chọn board trong danh sách hoặc nhập địa chỉ BT.")
        self.ctx.sync_settings()
        ui = self.ctx.dispatcher.ui
        accept = self.v_accept.get()
        self.ctx.set_status(f"Đang pair {addr}…")

        def work():
            return self.ctx.service.pair(
                addr, accept,
                on_pin=lambda pin: ui(self.ctx.log, f"Mã xác nhận (numeric comparison): {pin}"),
                on_log=lambda m: ui(self.ctx.log, m))

        def done(r):
            ok, msg = r
            self.ctx.log(f"Pair {addr}: {msg}", "ok" if ok else "err")
            self.ctx.set_status(msg)
            if ok:
                self.on_paired()
        self.ctx.dispatcher.background(work, done)

    def on_unpair(self):
        addr = self.v_addr.get().strip().upper()
        if not addr or not messagebox.askyesno("Bỏ pair", f"Bỏ pair {addr} trên PC?"):
            return
        self.ctx.dispatcher.background(
            lambda: self.ctx.service.unpair(addr),
            lambda r: (self.ctx.log(f"Bỏ pair {addr}: {r[1]}", "ok" if r[0] else "err"), self.on_paired()))
