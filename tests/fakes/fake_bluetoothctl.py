#!/usr/bin/env python3
"""Minimal stand-in for BlueZ `bluetoothctl` (non-tty mode) used by the tests.

Mimics the output format of bluetoothctl 5.6x/5.7x, including ANSI colours,
prompts without a trailing newline and the interactive passkey confirmation.

Environment:
  FAKE_BT_CTRL     controller address           (default 11:22:33:44:55:66)
  FAKE_BT_DEVICES  "ADDR=Name,ADDR=Name"        devices found by scanning
  FAKE_BT_PAIR     confirm | ok | fail          pairing behaviour (default confirm)
"""

import os
import sys
import threading
import time

CTRL = os.environ.get("FAKE_BT_CTRL", "11:22:33:44:55:66")
PAIR_MODE = os.environ.get("FAKE_BT_PAIR", "confirm")
DISCOVERABLE = {}
for item in filter(None, os.environ.get("FAKE_BT_DEVICES", "AA:BB:CC:DD:EE:01=board").split(",")):
    addr, _, name = item.partition("=")
    DISCOVERABLE[addr.upper()] = name

known = {}      # addr -> name
paired = set()
trusted = set()
agent = True    # bluetoothctl registers an agent at start-up
lock = threading.Lock()
PROMPT = "\x1b[0;94m[bluetooth]\x1b[0m# "


def out(text, prompt=True):
    with lock:
        sys.stdout.write(text + ("\n" + PROMPT if prompt else ""))
        sys.stdout.flush()


def tag(t, color="92"):
    return f"[\x1b[0;{color}m{t}\x1b[0m]"


def discover():
    time.sleep(0.2)
    for addr, name in DISCOVERABLE.items():
        if addr not in known:
            known[addr] = name
            out(f"\r{tag('NEW')} Device {addr} {name}")


def main():
    global agent
    out("Agent registered")
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        cmd, _, arg = line.partition(" ")
        arg = arg.strip().upper()
        if cmd in ("quit", "exit"):
            return
        if cmd == "version":
            out("Version 5.72")
        elif cmd == "agent":
            if arg == "OFF":
                out("Agent unregistered" if agent else "No agent is registered")
                agent = False
            elif agent:
                out("Agent is already registered")
            else:
                agent = True
                out("Agent registered")
        elif cmd == "default-agent":
            out("Default agent request successful")
        elif cmd == "list":
            out(f"Controller {CTRL} fake-ctrl [default]")
        elif cmd in ("power", "pairable", "discoverable", "connectable"):
            out(f"Changing {cmd} {arg.lower()} succeeded")
        elif cmd == "scan":
            if arg == "ON":
                out("SetDiscoveryFilter success\nDiscovery started\n"
                    f"{tag('CHG', '93')} Controller {CTRL} Discovering: yes")
                threading.Thread(target=discover, daemon=True).start()
            else:
                out("Discovery stopped")
        elif cmd == "devices":
            out("\n".join(f"Device {a} {n}" for a, n in known.items()) or "", prompt=True)
        elif cmd == "info":
            if arg not in known:
                out(f"Device {arg} not available")
            else:
                yn = lambda b: "yes" if b else "no"  # noqa: E731
                out(f"Device {arg} (public)\n\tName: {known[arg]}\n\tAlias: {known[arg]}\n"
                    f"\tPaired: {yn(arg in paired)}\n\tTrusted: {yn(arg in trusted)}\n"
                    "\tConnected: no\n"
                    "\tUUID: Serial Port               (00001101-0000-1000-8000-00805f9b34fb)\n"
                    "\tUUID: Handsfree                 (0000111e-0000-1000-8000-00805f9b34fb)")
        elif cmd == "pair":
            if arg not in known:
                out(f"Device {arg} not available")
            elif arg in paired:
                out(f"Attempting to pair with {arg}\nFailed to pair: org.bluez.Error.AlreadyExists")
            elif PAIR_MODE == "fail":
                out(f"Attempting to pair with {arg}\n"
                    "Failed to pair: org.bluez.Error.AuthenticationFailed")
            elif PAIR_MODE == "confirm":
                out(f"Attempting to pair with {arg}\n{tag('CHG', '93')} Device {arg} Connected: yes\n"
                    "[agent] Confirm passkey 123456 (yes/no): ", prompt=False)
                answer = sys.stdin.readline().strip()
                if answer == "yes":
                    paired.add(arg)
                    out(f"{tag('CHG', '93')} Device {arg} Paired: yes\nPairing successful")
                else:
                    out("Failed to pair: org.bluez.Error.AuthenticationRejected")
            else:
                paired.add(arg)
                out(f"Attempting to pair with {arg}\nPairing successful")
        elif cmd == "trust":
            if arg not in known:
                out(f"Device {arg} not available")
            else:
                trusted.add(arg)
                out(f"{tag('CHG', '93')} Device {arg} Trusted: yes\nChanging {arg} trust succeeded")
        elif cmd == "remove":
            if arg not in known:
                out(f"Device {arg} not available")
            else:
                name = known.pop(arg)
                paired.discard(arg)
                trusted.discard(arg)
                out(f"{tag('DEL', '91')} Device {arg} {name}\nDevice has been removed")
        elif cmd == "connect":
            if arg not in paired:
                out(f"Attempting to connect to {arg}\nFailed to connect: org.bluez.Error.Failed")
            else:
                out(f"Attempting to connect to {arg}\nConnection successful")
        else:
            out(f"Invalid command in menu main: {cmd}")


if __name__ == "__main__":
    main()
