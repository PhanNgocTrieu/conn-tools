"""USB helpers: gadget setup on the DUT, device inventory, storage throughput."""

from __future__ import annotations

import json
from pathlib import Path

from ctt.core.executor import Executor

GADGET_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dut-usb-gadget.sh"

LIST_SCRIPT = r"""
for d in /sys/bus/usb/devices/*; do
  [ -f "$d/idVendor" ] || continue
  printf '%s|%s|%s|%s|%s|%s|%s\n' "${d##*/}" "$(cat $d/idVendor)" "$(cat $d/idProduct)" \
    "$(cat $d/speed 2>/dev/null)" "$(cat $d/bDeviceClass 2>/dev/null)" \
    "$(cat $d/manufacturer 2>/dev/null)" "$(cat $d/product 2>/dev/null)"
done
"""


def gadget(ex: Executor, action: str, ncm: bool = False, keep_fw: bool = False,
           timeout: float = 30):
    """Runs scripts/dut-usb-gadget.sh ACTION on the DUT (script is streamed, not copied).

    On the vendor image `up` stops usb-daemon and switches the dwc2 port to device;
    `down` undoes both. keep_fw=True refuses instead of stopping usb-daemon."""
    script = GADGET_SCRIPT.read_text()
    args = [action] + (["--ncm"] if ncm and action == "up" else []) + (["--keep-fw"] if keep_fw else [])
    return ex.run(["sh", "-c", script, "dut-usb-gadget.sh", *args], timeout=timeout)


def parse_list(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) != 7:
            continue
        name, vid, pid, speed, cls, mfr, prod = parts
        out.append({"port": name, "id": f"{vid}:{pid}", "speed_mbps": float(speed) if speed else None,
                    "class": cls, "manufacturer": mfr, "product": prod,
                    "root_hub": name.startswith("usb")})
    return out


def list_devices(ex: Executor) -> list[dict]:
    r = ex.run(["sh", "-c", LIST_SCRIPT], timeout=20)
    return parse_list(r.stdout)


def storage(ex: Executor, agent: str, path: str, size: str = "256M", block: str = "1M",
            direct: bool = True, timeout: float = 1800) -> dict:
    argv = [agent, "storage", "--json", "--path", path, "--size", size, "--block", block]
    if not direct:
        argv.append("--no-direct")
    r = ex.run(argv, timeout=timeout)
    for line in r.stdout.splitlines():
        if line.startswith("{"):
            ev = json.loads(line)
            if ev.get("event") in ("storage_result", "error"):
                return ev
    raise RuntimeError(r.stderr.strip() or f"storage test failed (rc={r.returncode})")
