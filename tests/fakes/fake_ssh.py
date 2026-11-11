#!/usr/bin/env python3
"""Stand-in for `ssh [opts] TARGET COMMAND`: runs COMMAND locally with sh."""
import os
import sys

args = sys.argv[1:]
i = 0
while i < len(args) and args[i].startswith("-"):
    i += 2 if args[i] in ("-o", "-p") else 1
cmd = args[i + 1] if i + 1 < len(args) else "true"
os.execvp("sh", ["sh", "-c", cmd])
