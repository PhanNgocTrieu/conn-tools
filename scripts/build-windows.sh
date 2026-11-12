#!/usr/bin/env bash
# Build the Windows .exe files from WSL, without pushing to GitHub.
#
#   scripts/build-windows.sh                  # ctt-agent-console.exe + ctt-bt.exe
#   scripts/build-windows.sh console          # ctt-agent-console.exe only
#   scripts/build-windows.sh gui              # ctt-bt.exe only
#   scripts/build-windows.sh console --agent build/dut/agent/ctt-agent
#
# Needs Python 3.10+ installed on Windows (winget install Python.Python.3.12).
# PyInstaller does not work reliably from a \\wsl.localhost path, so the sources are
# copied to %LOCALAPPDATA%\ctt-build, built there with scripts/build-btapp.ps1
# (venv .venv-win is kept between runs), and the .exe files are copied back to dist/.
# The aarch64 agent is embedded automatically if build/dut/agent/ctt-agent exists.
set -euo pipefail

ONLY=all
AGENT=""
while [ $# -gt 0 ]; do
    case "$1" in
        all|gui|console) ONLY=$1 ;;
        --agent) AGENT=$2; shift ;;
        -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

ROOT=$(cd "$(dirname "$0")/.." && pwd)
command -v powershell.exe >/dev/null || { echo "powershell.exe not found: run this in WSL" >&2; exit 1; }
[ -z "$AGENT" ] && [ -f "$ROOT/build/dut/agent/ctt-agent" ] && AGENT="$ROOT/build/dut/agent/ctt-agent"

# Run from a Windows directory so cmd/powershell do not complain about the UNC cwd.
cd /mnt/c
WIN_LOCAL=$(powershell.exe -NoProfile -Command '[Environment]::GetFolderPath("LocalApplicationData")' | tr -d '\r')
WORK_WIN="$WIN_LOCAL\\ctt-build"
WORK=$(wslpath -u "$WORK_WIN")

echo "== copy sources -> $WORK_WIN"
mkdir -p "$WORK"
rsync -a --delete \
    --exclude .git --exclude build --exclude out --exclude dist --exclude .venv-win \
    --exclude recipes-project --exclude '__pycache__' --exclude '*.private.yaml' \
    "$ROOT/" "$WORK/"

ARGS=(-Only "$ONLY")
if [ -n "$AGENT" ]; then
    mkdir -p "$WORK/dist/agent"
    cp "$AGENT" "$WORK/dist/agent/ctt-agent"
    ARGS+=(-Agent 'dist\agent\ctt-agent')
    echo "== embedding agent: $AGENT"
fi

echo "== build on Windows (scripts\\build-btapp.ps1 ${ARGS[*]})"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$WORK_WIN\\scripts\\build-btapp.ps1" "${ARGS[@]}"

case "$ONLY" in
    console) EXES="ctt-agent-console.exe" ;;
    gui) EXES="ctt-bt.exe" ;;
    *) EXES="ctt-agent-console.exe ctt-bt.exe" ;;
esac
mkdir -p "$ROOT/dist"
for exe in $EXES; do
    cp "$WORK/dist/$exe" "$ROOT/dist/$exe"
    echo "-> dist/$exe   (Windows: $(wslpath -w "$ROOT/dist/$exe"))"
done
