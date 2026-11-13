"""`ctt usb`: gadget setup on the DUT, device inventory, storage throughput."""

from __future__ import annotations

from ctt import __version__
from ctt.cli.base import Command, CommandGroup
from ctt.cli.options import add_dut_args, add_out_args, board_profile, dut_agent_path, dut_executor, parse_kv
from ctt.core.executor import LocalExecutor
from ctt.reporting.console import VERDICT_COLOR, color
from ctt.reporting.writer import ReportWriter


class UsbGadgetCommand(Command):
    name = "gadget"
    help = "create/remove the CTT gadget on the DUT"

    def configure(self, p):
        p.add_argument("action", choices=("up", "down", "status", "bind"))
        p.add_argument("--ncm", action="store_true", help="also add an NCM network function (usb0)")
        p.add_argument("--keep-services", "--keep-fw", action="store_true", dest="keep_services",
                       help="fail instead of stopping the daemons of usb.gadget_stop_services "
                            "(board profile) that own the port")
        add_dut_args(p)

    def run(self, args):
        from ctt.interfaces import usb
        prof = board_profile(args)
        r = usb.gadget(dut_executor(args), args.action, ncm=args.ncm, keep_services=args.keep_services,
                       stop_services=prof.gadget_stop_services, role_file=prof.role_file)
        print((r.stdout + r.stderr).strip())
        return r.returncode


class UsbListCommand(Command):
    name = "list"
    help = "USB devices and negotiated speed"

    def configure(self, p):
        p.add_argument("--side", choices=("host", "dut"), default="dut")
        p.add_argument("--all", action="store_true", help="include root hubs")
        add_dut_args(p)

    def run(self, args):
        from ctt.interfaces import usb
        ex = LocalExecutor() if args.side == "host" else dut_executor(args)
        devs = [d for d in usb.list_devices(ex) if args.all or not d["root_hub"]]
        for d in devs:
            sp = f"{d['speed_mbps']:g}M" if d["speed_mbps"] else "?"
            print(f"{d['port']:<10} {d['id']}  {sp:>6}  {d['manufacturer']} {d['product']}".rstrip())
        if not devs:
            print("(no USB devices)")
        return 0


class UsbStorageCommand(Command):
    name = "storage"
    help = "write/read throughput of a file on USB storage (DUT as USB host)"

    def configure(self, p):
        p.add_argument("--path", required=True,
                       help="test file on the mounted device, e.g. /run/media/sda1/ctt.bin")
        p.add_argument("--size", default="256M")
        p.add_argument("--block", default="1M")
        p.add_argument("--no-direct", action="store_true", help="buffered I/O instead of O_DIRECT")
        p.add_argument("--expect", action="append", default=[], metavar="KEY=VALUE",
                       help="write_MBps_min=, read_MBps_min=")
        add_dut_args(p)
        add_out_args(p)

    def run(self, args):
        from ctt.interfaces import usb
        dut = dut_executor(args)
        agent = dut_agent_path(args, dut)
        print(f"storage test {args.path} ({args.size}, block {args.block}) on {dut.name} ...")
        ev = usb.storage(dut, agent, args.path, args.size, args.block, direct=not args.no_direct)
        checks = self._checks(ev, {k: float(v) for k, v in parse_kv(args.expect, "--expect").items()})
        if ev.get("event") == "error":
            verdict, error = "ERROR", ev.get("message")
        else:
            ok = bool(ev.get("ok")) and all(c["ok"] for c in checks)
            verdict, error = ("PASS" if ok else "FAIL"), ("" if ev.get("ok") else "data verification failed")
        self._print(ev, checks, verdict, error)
        report = ReportWriter(args.out, "usb-storage")
        row = {"name": f"storage {args.path}", "transport": "storage", "mode": "rw", "verdict": verdict,
               **{k: ev.get(k) for k in ("write_MBps", "read_MBps", "verify_errors") if k in ev},
               "error": error or ""}
        report.add_record("storage", row, {"result": ev, "checks": checks})
        report.finish({"run": "usb-storage", "dut": dut.name, "ctt_version": __version__})
        print(f"report: {report.dir}")
        return 0 if verdict == "PASS" else 1

    @staticmethod
    def _checks(ev: dict, expect: dict) -> list[dict]:
        checks = []
        for key, val in expect.items():
            if not key.endswith("_min"):
                raise SystemExit(f"ctt usb storage: unknown expect key {key} (write_MBps_min, read_MBps_min)")
            v = ev.get(key[:-4])
            checks.append({"check": key, "value": v, "op": ">=", "limit": val,
                           "ok": v is not None and v >= val})
        return checks

    @staticmethod
    def _print(ev: dict, checks: list[dict], verdict: str, error: str) -> None:
        for k in ("write_MBps", "read_MBps"):
            if k in ev:
                print(f"  {k[:-5]:<6} {ev[k]:9.2f} MB/s ({ev[k] * 8:8.1f} Mbit/s)"
                      f"{'' if ev.get(k[:-5] + '_direct') else '  [buffered]'}")
        if "verify_errors" in ev:
            print(f"  verify errors: {ev['verify_errors']}")
        for c in checks:
            if not c["ok"]:
                print(f"  check {c['check']}: {c['value']} not >= {c['limit']}")
        if error:
            print(f"  error: {error}")
        print(f"  => {color(verdict, VERDICT_COLOR[verdict])}")


class UsbCommand(CommandGroup):
    name = "usb"
    help = "USB gadget setup, inventory, storage test"
    dest = "usb_cmd"

    def subcommands(self):
        return [UsbGadgetCommand(), UsbListCommand(), UsbStorageCommand()]
