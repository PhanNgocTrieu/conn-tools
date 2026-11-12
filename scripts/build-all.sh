#!/usr/bin/env bash
# Build everything in one go, from WSL, then print where each result is and how to run it.
#
#   scripts/build-all.sh                       # all steps
#   scripts/build-all.sh --no-test             # skip the host unit/loopback tests
#   scripts/build-all.sh --no-windows          # WSL/board parts only (no .exe)
#   scripts/build-all.sh --no-python-install   # do not install Python on Windows if missing
#
# Steps:
#   1. host    ctt-agent for WSL/Linux (+ tests)     scripts/build-host.sh
#   2. board   ctt-agent for the board (aarch64)   scripts/build-dut.sh --generic --static --strip
#   3. exe     ctt-agent-console.exe + ctt-agent.exe    scripts/build-windows.sh (embeds the board agent)
# A failed step does not stop the others; the report shows what failed and where the log is.
# The report is also saved to dist/BUILD-REPORT.txt. Exit code 0 only if every step passed.
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
HOST_ARGS=()
WIN_ARGS=()
WINDOWS=1
while [ $# -gt 0 ]; do
    case "$1" in
        --no-test) HOST_ARGS+=(--no-test) ;;
        --no-windows) WINDOWS=0 ;;
        --no-python-install) WIN_ARGS+=(--no-python-install) ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

START=$(date +%s)
LOGDIR="$ROOT/build/logs"
mkdir -p "$LOGDIR" "$ROOT/dist"
declare -A STATUS TIME
STEPS=()

step() {   # step <name> <title> <command...>
    local name=$1 title=$2; shift 2
    STEPS+=("$name")
    local log="$LOGDIR/$name.log" t0=$SECONDS
    printf '\n\033[1m== [%s] %s\033[0m  (log: build/logs/%s.log)\n' "$name" "$title" "$name"
    if "$@" 2>&1 | tee "$log"; then STATUS[$name]=OK; else STATUS[$name]=FAIL; fi
    TIME[$name]=$((SECONDS - t0))
}

skip() { STEPS+=("$1"); STATUS[$1]="SKIP ($2)"; TIME[$1]=0; }

step host "ctt-agent for WSL/Linux" scripts/build-host.sh "${HOST_ARGS[@]}"
step board "ctt-agent for the board (aarch64)" scripts/build-dut.sh --generic --static --strip
if [ "$WINDOWS" = 0 ]; then
    skip exe "--no-windows"
elif ! command -v powershell.exe >/dev/null; then
    skip exe "powershell.exe not found: not WSL"
elif [ "${STATUS[board]}" != OK ]; then
    skip exe "board agent failed; the .exe needs it for Deploy agent"
else
    step exe "ctt-agent-console.exe + ctt-agent.exe (Windows)" \
        scripts/build-windows.sh all --agent build/dut/agent/ctt-agent "${WIN_ARGS[@]}"
fi

# ---------------------------------------------------------------- report
artifact() {   # artifact <step> <path> <run command>
    local step=$1 f=$2 run=$3
    if [ -f "$ROOT/$f" ]; then
        local size when win=""
        size=$(du -h "$ROOT/$f" | cut -f1)
        when=$(date -r "$ROOT/$f" '+%Y-%m-%d %H:%M')
        case "$f" in *.exe) win="   Windows: $(wslpath -w "$ROOT/$f" 2>/dev/null)" ;; esac
        local note
        if [ "${STATUS[$step]}" != OK ]; then
            note="OLD FILE: step '$step' ${STATUS[$step]}"
        elif [ "$(date -r "$ROOT/$f" +%s)" -lt "$START" ]; then
            note="up to date"
        else
            note="new"
        fi
        printf '  %-32s %6s  %s  [%s]\n      run: %s\n' "$f" "$size" "$when" "$note" "$run"
        [ -n "$win" ] && printf '   %s\n' "$win"
    else
        printf '  %-32s (not built)\n' "$f"
    fi
}

report() {
    echo "CTT build report  $(date '+%Y-%m-%d %H:%M')  commit $(git rev-parse --short HEAD 2>/dev/null)$(git diff --quiet 2>/dev/null || echo ' (+ uncommitted changes)')"
    echo "repo: $ROOT"
    echo
    echo "Steps:"
    for s in "${STEPS[@]}"; do
        printf '  %-6s %-50s %4ss   log: build/logs/%s.log\n' "$s" "${STATUS[$s]}" "${TIME[$s]}" "$s"
    done
    echo
    echo "Results:"
    echo " [WSL / Linux]"
    artifact host build/host/agent/ctt-agent "build/host/agent/ctt-agent version   (CLI: python3 -m ctt selftest)"
    echo "      apps without .exe: python3 -m ctt.btapp console   |   python3 -m ctt.btapp (window, needs python3-tk)"
    echo " [Board board]"
    artifact board build/dut/agent/ctt-agent "copy to the board (python3 -m ctt deploy, USB stick or UART), then: /tmp/ctt-agent version"
    echo " [Windows]"
    artifact exe dist/ctt-agent-console.exe "PowerShell: .\\dist\\ctt-agent-console.exe   |   WSL: dist/ctt-agent-console.exe"
    artifact exe dist/ctt-agent.exe "PowerShell: .\\dist\\ctt-agent.exe   (or double-click in Explorer)"
    echo
    echo "Docs: README.md, docs/bt-app.md, docs/bt-client-test.md"
}

echo
report | tee "$ROOT/dist/BUILD-REPORT.txt"
echo "(saved: dist/BUILD-REPORT.txt)"

for s in "${STEPS[@]}"; do [ "${STATUS[$s]}" = FAIL ] && exit 1; done
exit 0
