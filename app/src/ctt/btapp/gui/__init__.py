"""Desktop window of the CTT Bluetooth test app (Tkinter, Windows and WSLg)."""

from __future__ import annotations

import os


def _smoke(root, target: str) -> None:
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
    import tkinter as tk

    from ctt.btapp.gui.window import MainWindow
    root = tk.Tk()
    MainWindow(root)
    if os.environ.get("CTT_BT_SMOKE"):
        _smoke(root, os.environ["CTT_BT_SMOKE"])
    root.mainloop()
    return 0
