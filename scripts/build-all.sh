#!/usr/bin/env bash
# Build every release file in one go (from WSL / Linux) and collect them per version:
#
#   output/v<version>/
#     linux-aarch64/ctt-agent      board agent (static, stripped)
#     linux-x86_64/ctt-agent       PC / board agent (static, stripped)
#     windows/ctt-agent.exe        desktop app
#     windows/ctt-agent-console.exe
#     SHA256SUMS                   checksums of the files above
#     BUILD-REPORT.txt             steps, logs, where each file is and how to run it
#
#   scripts/build-all.sh                       # all steps
#   scripts/build-all.sh --no-test             # skip the host unit/loopback tests
#   scripts/build-all.sh --no-windows          # no .exe (also when not in WSL)
#   scripts/build-all.sh --no-python-install   # do not install Python on Windows if missing
#
# Steps (a failed step does not stop the others; exit code 0 only if all passed):
#   host     ctt-agent for this PC + all tests      scripts/build-host.sh     -> build/host
#   board    ctt-agent for the board (aarch64)      scripts/build-dut.sh      -> build/dut
#   linux    static release ctt-agent (x86_64)      cmake -DCTT_STATIC=ON     -> build/rel
#   exe      ctt-agent.exe + ctt-agent-console.exe  scripts/build-windows.sh  -> output/v<version>/windows
#   package  copy into output/v<version>/, write SHA256SUMS
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
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

VERSION=$(sed -n 's/^project(connectivity-tools VERSION \([0-9.]*\).*/\1/p' CMakeLists.txt)
OUT="output/v$VERSION"
START=$(date +%s)
LOGDIR="$ROOT/build/logs"
mkdir -p "$LOGDIR" "$ROOT/$OUT"
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

linux_release() {
    cmake -S "$ROOT" -B "$ROOT/build/rel" -DCTT_BUILD_TESTS=OFF -DCTT_STATIC=ON >/dev/null &&
        cmake --build "$ROOT/build/rel" -j"$(nproc)" &&
        strip "$ROOT/build/rel/agent/ctt-agent" &&
        "$ROOT/build/rel/agent/ctt-agent" version
}

package() {
    local ok=0
    if [ "${STATUS[board]}" = OK ]; then
        install -D -m 0755 build/dut/agent/ctt-agent "$OUT/linux-aarch64/ctt-agent"
    fi
    if [ "${STATUS[linux]}" = OK ]; then
        install -D -m 0755 build/rel/agent/ctt-agent "$OUT/linux-x86_64/ctt-agent"
    fi
    (cd "$OUT" && find . -type f ! -name SHA256SUMS ! -name BUILD-REPORT.txt | sed 's#^\./##' | sort |
        xargs -r sha256sum > SHA256SUMS) || ok=1
    cat "$OUT/SHA256SUMS"
    return $ok
}

step host "ctt-agent for this PC + tests" scripts/build-host.sh "${HOST_ARGS[@]}"
step board "ctt-agent for the board (aarch64)" scripts/build-dut.sh --generic --static --strip
step linux "static release ctt-agent (x86_64)" linux_release
if [ "$WINDOWS" = 0 ]; then
    skip exe "--no-windows"
elif ! command -v powershell.exe >/dev/null; then
    skip exe "powershell.exe not found: not WSL"
elif [ "${STATUS[board]}" != OK ]; then
    skip exe "board agent failed; the .exe embeds it for Deploy agent"
else
    step exe "ctt-agent.exe + ctt-agent-console.exe (Windows)" \
        scripts/build-windows.sh all --agent build/dut/agent/ctt-agent "${WIN_ARGS[@]}"
fi
step package "collect into $OUT, SHA256SUMS" package

# ---------------------------------------------------------------- report
artifact() {   # artifact <step> <path> <how to run>
    local step=$1 f=$2 run=$3
    if [ -f "$ROOT/$f" ]; then
        local size when win="" note
        size=$(du -h "$ROOT/$f" | cut -f1)
        when=$(date -r "$ROOT/$f" '+%Y-%m-%d %H:%M')
        case "$f" in *.exe) win="Windows: $(wslpath -w "$ROOT/$f" 2>/dev/null)" ;; esac
        if [ "${STATUS[$step]}" != OK ]; then
            note="OLD FILE: step '$step' ${STATUS[$step]}"
        elif [ "$(date -r "$ROOT/$f" +%s)" -lt "$START" ]; then
            note="up to date"
        else
            note="new"
        fi
        printf '  %-46s %6s  %s  [%s]\n      run: %s\n' "$f" "$size" "$when" "$note" "$run"
        [ -n "$win" ] && printf '      %s\n' "$win"
    else
        printf '  %-46s (not built)\n' "$f"
    fi
}

report() {
    echo "CTT $VERSION build report  $(date '+%Y-%m-%d %H:%M')  commit $(git rev-parse --short HEAD 2>/dev/null)$(git diff --quiet 2>/dev/null || echo ' (+ uncommitted changes)')"
    echo "repo: $ROOT"
    echo
    echo "Steps:"
    for s in "${STEPS[@]}"; do
        printf '  %-8s %-50s %4ss   log: build/logs/%s.log\n' "$s" "${STATUS[$s]}" "${TIME[$s]}" "$s"
    done
    echo
    echo "Release files ($OUT):"
    artifact board "$OUT/linux-aarch64/ctt-agent" "copy to the board (scripts/ctt deploy, USB stick or UART), then: /tmp/ctt-agent version"
    artifact linux "$OUT/linux-x86_64/ctt-agent" "$OUT/linux-x86_64/ctt-agent version"
    local wout="${OUT//\//\\}\\windows"
    artifact exe "$OUT/windows/ctt-agent.exe" "double-click, or PowerShell: .\\$wout\\ctt-agent.exe"
    artifact exe "$OUT/windows/ctt-agent-console.exe" "PowerShell: .\\$wout\\ctt-agent-console.exe   |   WSL: $OUT/windows/ctt-agent-console.exe"
    echo "  $OUT/SHA256SUMS"
    echo
    echo "Development builds:"
    artifact host build/host/agent/ctt-agent "tests ran with it; CLI: scripts/ctt selftest   |   apps: scripts/ctt-app [console]"
    echo
    echo "Docs: README.md, docs/README.md"
}

echo
report | tee "$ROOT/$OUT/BUILD-REPORT.txt"
echo "(saved: $OUT/BUILD-REPORT.txt)"

for s in "${STEPS[@]}"; do [ "${STATUS[$s]}" = FAIL ] && exit 1; done
exit 0
