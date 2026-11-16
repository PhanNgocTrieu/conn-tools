"""Tab 1: board over SSH, PC-side discovery and pairing, connection target."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ctt.bluetooth.board import SERVICE_CHANNEL, bundled_agent
from ctt.btapp.gui.context import AppContext
from ctt.protocol.links import LINK_LABELS, LINKS, ble_scan, capabilities


class ConnectTab(ttk.Frame):
    title = "  1. Connect  "

    def __init__(self, master, ctx: AppContext):
        super().__init__(master, padding=12)
        self.ctx = ctx
        s = ctx.settings
        self.v_board = tk.StringVar(value=s.board)
        self.v_nossh = tk.BooleanVar(value=s.no_ssh)
        self.v_accept = tk.BooleanVar(value=True)
        self.v_addr = tk.StringVar(value=s.bt_addr)
        self.v_chan = tk.StringVar(value=s.channel)
        self.caps = {c.link: c for c in capabilities()}
        self.v_link = tk.StringVar(value=s.link if s.link in LINKS and self.caps[s.link].ok else "rfcomm")
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
        settings.link = self.v_link.get()

    # ---- layout ------------------------------------------------------------------------

    def _board_frame(self):
        bf = ttk.LabelFrame(self, text="Board (SSH, used to run the agent on the board)", padding=10)
        ttk.Label(bf, text="SSH").grid(row=0, column=0, sticky="w")
        ttk.Entry(bf, textvariable=self.v_board, width=30).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Button(bf, text="Check board", command=self.on_check).grid(row=0, column=2, padx=3)
        ttk.Button(bf, text="Deploy agent", command=self.on_deploy).grid(row=0, column=3, padx=3)
        ttk.Checkbutton(bf, text=f"No SSH (agent already runs as a service, channel {SERVICE_CHANNEL})",
                        variable=self.v_nossh).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self.board_info = ttk.Label(bf, text="Not checked yet", style="Cap.TLabel")
        self.board_info.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        return bf

    def _pairing_frame(self):
        pf = ttk.LabelFrame(self, text="PC Bluetooth (USB dongle): find and pair with the board", padding=10)
        pf.columnconfigure(0, weight=1)
        pf.rowconfigure(1, weight=1)
        bar = ttk.Frame(pf)
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Button(bar, text="Scan", command=self.on_scan).pack(side="left")
        ttk.Button(bar, text="Paired devices", command=self.on_paired).pack(side="left", padx=4)
        ttk.Button(bar, text="Pair", style="Accent.TButton", command=self.on_pair).pack(side="left", padx=4)
        ttk.Button(bar, text="Unpair", command=self.on_unpair).pack(side="left")
        ttk.Checkbutton(bar, text="Board accepts the pairing (over SSH)",
                        variable=self.v_accept).pack(side="left", padx=12)
        self.backend_lbl = ttk.Label(bar, text="", style="Cap.TLabel")
        self.backend_lbl.pack(side="right")

        self.devtree = ttk.Treeview(pf, columns=("name", "addr", "paired"), show="headings", height=8)
        for c, t, w in (("name", "Name", 260), ("addr", "Address", 160), ("paired", "Paired", 80)):
            self.devtree.heading(c, text=t)
            self.devtree.column(c, width=w, anchor="w")
        self.devtree.grid(row=1, column=0, sticky="nsew", pady=6)
        self.devtree.bind("<<TreeviewSelect>>", self._on_dev_select)
        return pf

    def _target_frame(self):
        tf = ttk.LabelFrame(self, text="Target", padding=10)
        ttk.Label(tf, text="Link").grid(row=1, column=0, sticky="w", pady=(8, 0))
        lf = ttk.Frame(tf)
        lf.grid(row=1, column=1, columnspan=4, sticky="w", pady=(8, 0))
        for link in LINKS:   # each PC offers what it supports; the others are greyed out with the reason
            cap = self.caps[link]
            rb = ttk.Radiobutton(lf, text=LINK_LABELS[link], value=link, variable=self.v_link,
                                 command=self._on_link)
            rb.pack(side="left", padx=(0, 12))
            if not cap.ok:
                rb.state(["disabled"])
        self.link_info = ttk.Label(tf, text="", style="Cap.TLabel", wraplength=900)
        self.link_info.grid(row=2, column=0, columnspan=5, sticky="w")
        ttk.Label(tf, text="Board BT address").grid(row=0, column=0, sticky="w")
        ttk.Entry(tf, textvariable=self.v_addr, width=22).grid(row=0, column=1, padx=6, sticky="w")
        ttk.Label(tf, text="RFCOMM channel").grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Entry(tf, textvariable=self.v_chan, width=8).grid(row=0, column=3, padx=6, sticky="w")
        ttk.Label(tf, text="auto = ask the agent on the board over SSH", style="Cap.TLabel").grid(
            row=0, column=4, sticky="w")
        self._on_link()
        return tf

    def _on_link(self):
        link = self.v_link.get()
        missing = [f"{LINK_LABELS[c.link]}: {c.why}" for c in self.caps.values() if not c.ok]
        board = {"rfcomm": "RFCOMM channel (no SSH: 22)", "ble-gatt": "ctt-agent server -t gatt",
                 "ble-coc": "ctt-agent server -t l2cap -o type=le"}[link]
        self.link_info.configure(text=f"Board (no SSH, on the UART): {board}."
                                 + (f"   Not on this PC: {'; '.join(missing)}" if missing else ""))
        self.ctx.settings.link = link

    # ---- actions -------------------------------------------------------------------------

    def show_backend_status(self, r) -> None:
        ok, msg = r
        self.backend_lbl.configure(text=f"{self.ctx.service.pairing.name}: {'OK' if ok else msg}")
        if not ok:
            self.ctx.log(f"Bluetooth PC: {msg}", "err")

    def on_check(self):
        self.ctx.sync_settings()
        self.ctx.set_status("Checking board…")

        def done(info):
            fw = info.get("fw", {})
            svc = fw.get("services", {})
            parts = [f"{info['hostname'] or '?'} ({info['arch']})",
                     f"agent: {info['agent'] or 'MISSING → press Deploy agent'}",
                     f"BT: {info.get('bt_address') or '?'}"]
            parts += [f"{name}: {state}" for name, state in svc.items()]
            if isinstance(fw.get("bt", {}).get("paired"), int):
                parts.append(f"bonds: {fw['bt']['paired']}")
            parts.append(f"profile: {info.get('profile', '-')}")
            self.board_info.configure(text="   ·   ".join(parts))
            self.v_addr.set(self.ctx.settings.bt_addr)
            self.ctx.log("Board OK: " + " · ".join(parts), "ok")
            self.ctx.set_status("Board OK")
        self.ctx.dispatcher.background(self.ctx.service.check_board, done)

    def on_deploy(self):
        self.ctx.sync_settings()
        agent = bundled_agent()
        if not agent:
            p = filedialog.askopenfilename(title="Choose the ctt-agent file (aarch64)")
            if not p:
                return
            agent = Path(p)
        self.ctx.set_status("Copying agent to the board…")
        self.ctx.dispatcher.background(
            lambda: self.ctx.service.deploy_agent(agent),
            lambda v: (self.ctx.log(f"Deploy OK: {v}", "ok"), self.ctx.set_status("Deploy OK")))

    def on_scan(self):
        self.devtree.delete(*self.devtree.get_children())
        if self.v_link.get() != "rfcomm":
            self.ctx.set_status("BLE scan (8 s): boards advertising the CTT service…")
            self.ctx.dispatcher.background(lambda: ble_scan(8, only_ctt=True), self._show_ble)
            return
        self.ctx.set_status("Scanning Bluetooth (about 10–15 s)…")
        self.ctx.dispatcher.background(lambda: self.ctx.service.scan(10), self._show_devices)

    def _show_ble(self, devs):
        for addr, name, rssi in devs:
            self.devtree.insert("", "end", values=(f"{name or '(no name)'}  [BLE {rssi} dBm]", addr, ""))
        self.ctx.set_status(f"{len(devs)} BLE devices with the CTT service")
        self.ctx.log(f"BLE scan: {len(devs)} boards running ctt-agent server -t gatt")

    def on_paired(self):
        self.ctx.dispatcher.background(self.ctx.service.paired, self._show_devices)

    def _show_devices(self, devs):
        self.devtree.delete(*self.devtree.get_children())
        for d in devs:
            self.devtree.insert("", "end", values=(d.name or "(no name)", d.address,
                                                   "yes" if d.paired else ""))
        self.ctx.set_status(f"{len(devs)} devices")
        self.ctx.log(f"Found {len(devs)} devices")

    def _on_dev_select(self, _e):
        sel = self.devtree.selection()
        if sel:
            self.v_addr.set(self.devtree.item(sel[0])["values"][1])

    def on_pair(self):
        addr = self.v_addr.get().strip().upper()
        if not addr:
            return messagebox.showinfo("Pair", "Select the board in the list or enter its Bluetooth address.")
        self.ctx.sync_settings()
        ui = self.ctx.dispatcher.ui
        accept = self.v_accept.get()
        self.ctx.set_status(f"Pairing {addr}…")

        def work():
            return self.ctx.service.pair(
                addr, accept,
                on_pin=lambda pin: ui(self.ctx.log, f"Passkey (numeric comparison): {pin}"),
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
        if not addr or not messagebox.askyesno("Unpair", f"Unpair {addr} on the PC?"):
            return
        self.ctx.dispatcher.background(
            lambda: self.ctx.service.unpair(addr),
            lambda r: (self.ctx.log(f"Unpair {addr}: {r[1]}", "ok" if r[0] else "err"), self.on_paired()))
