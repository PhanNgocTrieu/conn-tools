"""DUT bring-up: `ctt deploy`, `ctt dut-info`."""

from __future__ import annotations

import sys
from pathlib import Path

from ctt.cli.base import Command
from ctt.cli.options import add_dut_args, dut_executor
from ctt.core.agent import DEFAULT_DUT_AGENT, REPO_ROOT
from ctt.core.executor import SshExecutor

DUT_INFO_SCRIPT = r"""
section() { echo; echo "### $1"; }
section system;     uname -a; cat /etc/os-release 2>/dev/null | head -4; uptime
section cpu;        grep -m1 -iE 'model name|Hardware|Processor' /proc/cpuinfo; nproc 2>/dev/null
section memory;     free -m 2>/dev/null || head -3 /proc/meminfo
section network;    (ip -br addr 2>/dev/null || ifconfig -a) | head -30
section wifi;       (iw dev 2>/dev/null; for i in $(ls /sys/class/net); do [ -d /sys/class/net/$i/wireless ] && iw dev $i link 2>/dev/null; done) | head -30
section bluetooth;  (hciconfig -a 2>/dev/null || btmgmt info 2>/dev/null || ls /sys/class/bluetooth) | head -30
section bt-kernel;  grep -E 'bluetooth|rfcomm|l2cap|btusb|hci_uart' /proc/modules 2>/dev/null; ls /proc/net/rfcomm* 2>/dev/null; (command -v bluetoothd || ls /usr/libexec/bluetooth/bluetoothd) 2>/dev/null
section usb;        (lsusb 2>/dev/null || ls /sys/bus/usb/devices) | head -30; echo "UDC (device/gadget controllers):"; ls /sys/class/udc 2>/dev/null; for r in /sys/class/usb_role/*/role /sys/bus/platform/devices/*/dwc2_drdmode; do [ -f "$r" ] && echo "$r: $(cat $r)"; done
section i2c;        ls /dev/i2c-* 2>/dev/null; (command -v i2cdetect >/dev/null && echo 'i2c-tools: present' || echo 'i2c-tools: missing')
section tools;      for t in busctl iperf3 bluetoothctl btmgmt sdptool rfcomm iw wpa_cli hostapd i2cdetect python3 gdbserver; do printf '%-14s %s\n' $t "$(command -v $t || echo -)"; done
section ctt-agent;  for p in /tmp/ctt-agent /usr/bin/ctt-agent; do [ -x $p ] && $p version; done
"""


class DeployCommand(Command):
    name = "deploy"
    help = "copy ctt-agent to the DUT"

    def configure(self, p):
        p.add_argument("--binary", default=None, help="default: build/dut/agent/ctt-agent")
        add_dut_args(p)

    def run(self, args):
        dut = dut_executor(args)
        if not isinstance(dut, SshExecutor):
            raise SystemExit("ctt deploy: --dut user@host is required")
        binary = Path(args.binary or REPO_ROOT / "build" / "dut" / "agent" / "ctt-agent")
        if not binary.is_file():
            raise SystemExit(f"ctt deploy: {binary} not found - build it with scripts/build-dut.sh")
        remote = args.dut_agent or DEFAULT_DUT_AGENT
        print(f"copying {binary} -> {dut.target}:{remote}")
        dut.put(str(binary), remote + ".new")
        r = dut.run(f"chmod +x {remote}.new && mv -f {remote}.new {remote} && {remote} version")
        if r.returncode != 0:
            print(r.stdout + r.stderr, file=sys.stderr)
            raise SystemExit("ctt deploy: agent does not run on the DUT (wrong architecture or "
                             "missing libraries? try a static build: scripts/build-dut.sh --static)")
        print(r.stdout.strip())
        return 0


class DutInfoCommand(Command):
    name = "dut-info"
    help = "inventory of the DUT's connectivity stack"

    def configure(self, p):
        add_dut_args(p)

    def run(self, args):
        from ctt.firmware import vendor_daemons as fw
        dut = dut_executor(args)
        r = dut.run(["sh", "-c", DUT_INFO_SCRIPT], timeout=60)
        print(r.stdout)
        st = fw.status(dut)
        if fw.has_vendor_firmware(st):
            print("\n### firmware (vendor connectivity daemons)")
            print(fw.format_status(st))
        if r.returncode != 0 and r.stderr.strip():
            print(r.stderr, file=sys.stderr)
        return r.returncode
