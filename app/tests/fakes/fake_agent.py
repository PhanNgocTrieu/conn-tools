#!/usr/bin/env python3
"""Stand-in for ctt-agent on the board: `version`, `server`, `pipe` (RFCOMM-free)."""
import json
import sys

def emit(**ev):
    print(json.dumps(ev), flush=True)

cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd == "version":
    print("ctt-agent 0.1.0 (CTP v1, fake)")
elif cmd == "server":
    emit(event="listening", transport="rfcomm", bdaddr="00:11:22:33:44:55", channel=5)
    sys.stdin.read() if not sys.stdin.isatty() else None
elif cmd == "pipe":
    emit(event="listening", transport="rfcomm", bdaddr="00:11:22:33:44:55", channel=6)
    emit(event="connected", peer="AA:BB:CC:DD:EE:FF/1")
    for line in sys.stdin:
        data = bytes.fromhex(line.strip())
        emit(event="tx", len=len(data))
        emit(event="rx", t=0.1, len=len(data), hex=data[::-1].hex())   # "peer" answers reversed
    emit(event="closed", reason="stdin closed")
