#!/usr/bin/env bash
# Build ctt-agent for every platform. Everything it produces is under build/:
#
#   build/ctt-agent-linux/ctt-agent          Linux PC / board, x86_64 (static)
#   build/ctt-agent-aarch/ctt-agent          aarch64 boards (static: one file for every board)
#   build/ctt-agent-window/ctt-agent.exe        Windows, console (menu + commands, also over SSH)
#   build/ctt-agent-window/ctt-agent-UI.exe  Windows, desktop window
#   build/SHA256SUMS
#   build/.work/                             intermediate files (CMake trees, logs) - ignore
#
#   scripts/build.sh                     # all three (window only from WSL)
#   scripts/build.sh linux aarch         # some of them
#   scripts/build.sh --clean             # delete build/ first, then build all
#   scripts/build.sh linux --no-test     # skip the tests (C++ unit, loopback, Python)
#   scripts/build.sh aarch --sdk /opt/<sdk>/environment-setup-<target>   # Yocto SDK instead of
#                                        # the generic static cross compiler
#   scripts/build.sh window --no-python-install   # fail instead of installing Python on Windows
#
# Needs: cmake, g++ (linux), g++-aarch64-linux-gnu (aarch), WSL + powershell.exe (window: Python
# for Windows and PyInstaller are installed on demand). A failed target does not stop the others.
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
OUT="$ROOT/build"
WORK="$OUT/.work"
TARGETS=()
TEST=1
CLEAN=0
SDK_ENV=""
NOPY=0
while [ $# -gt 0 ]; do
    case "$1" in
        linux|aarch|window) TARGETS+=("$1") ;;
        all) TARGETS+=(linux aarch window) ;;
        --no-test) TEST=0 ;;
        --clean) CLEAN=1 ;;
        --sdk) SDK_ENV=$2; shift ;;
        --no-python-install) NOPY=1 ;;
        -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
    esac
    shift
done
[ ${#TARGETS[@]} -eq 0 ] && TARGETS=(linux aarch window)
VERSION=$(sed -n 's/^project(connectivity-tools VERSION \([0-9.]*\).*/\1/p' linux-app/CMakeLists.txt)

if [ "$CLEAN" = 1 ]; then
    echo "== clean: removing build/"
    rm -rf "$OUT"
fi
mkdir -p "$WORK/logs"
declare -A STATUS

step() {   # step <target> <function>
    local t=$1 log="$WORK/logs/$1.log"
    printf '\n\033[1m== %s\033[0m  (log: build/.work/logs/%s.log)\n' "ctt-agent-$t" "$t"
    if "$2" 2>&1 | tee "$log"; then STATUS[$t]=OK; else STATUS[$t]=FAILED; fi
}

build_linux() {
    local b="$WORK/linux"
    cmake -S linux-app -B "$b" -DCMAKE_BUILD_TYPE=Release -DCTT_STATIC=ON \
          -DCTT_BUILD_TESTS=$([ "$TEST" = 1 ] && echo ON || echo OFF) >/dev/null &&
    cmake --build "$b" -j"$(nproc)" &&
    ln -sf "$b/compile_commands.json" "$WORK/compile_commands.json" &&
    if [ "$TEST" = 1 ]; then (cd "$b" && ctest --output-on-failure); fi &&
    install -D -m 0755 "$b/ctt-agent" "$OUT/ctt-agent-linux/ctt-agent" &&
    strip "$OUT/ctt-agent-linux/ctt-agent" &&
    "$OUT/ctt-agent-linux/ctt-agent" version
}

build_aarch() {
    local b="$WORK/aarch" args=(-DCMAKE_BUILD_TYPE=Release -DCTT_BUILD_TESTS=OFF) strip_cmd=aarch64-linux-gnu-strip
    if [ -n "$SDK_ENV" ]; then
        [ -f "$SDK_ENV" ] || { echo "SDK environment script not found: $SDK_ENV"; return 1; }
        # shellcheck disable=SC1090
        set +u; source "$SDK_ENV"; set -u
        args+=(-DCMAKE_TOOLCHAIN_FILE="${OE_CMAKE_TOOLCHAIN_FILE:-$OECORE_NATIVE_SYSROOT/usr/share/cmake/OEToolchainConfig.cmake}")
        strip_cmd=${STRIP:-$strip_cmd}
        echo "Yocto SDK: $SDK_ENV (links against the image's libraries)"
    else
        command -v aarch64-linux-gnu-g++ >/dev/null ||
            { echo "aarch64-linux-gnu-g++ not found: sudo apt install g++-aarch64-linux-gnu"; return 1; }
        args+=(-DCMAKE_TOOLCHAIN_FILE="$ROOT/linux-app/cmake/toolchains/aarch64-linux-gnu.cmake" -DCTT_STATIC=ON)
    fi
    rm -rf "$b"
    cmake -S linux-app -B "$b" "${args[@]}" >/dev/null &&
    cmake --build "$b" -j"$(nproc)" &&
    install -D -m 0755 "$b/ctt-agent" "$OUT/ctt-agent-aarch/ctt-agent" &&
    $strip_cmd "$OUT/ctt-agent-aarch/ctt-agent" &&
    file "$OUT/ctt-agent-aarch/ctt-agent"
}

# Windows .exe: PyInstaller does not work reliably from a \\wsl.localhost path, so the sources
# are copied to %LOCALAPPDATA%\ctt-build and built there by window-app\build.ps1.
build_window() {
    command -v powershell.exe >/dev/null || { echo "powershell.exe not found: run this in WSL"; return 1; }
    local agent="$OUT/ctt-agent-aarch/ctt-agent" win_local work_win work args=()
    win_local=$(cd /mnt/c && powershell.exe -NoProfile -Command '[Environment]::GetFolderPath("LocalApplicationData")' | tr -d '\r')
    work_win="$win_local\\ctt-build"
    work=$(wslpath -u "$work_win")
    echo "copy sources -> $work_win"
    mkdir -p "$work"
    rsync -a --delete --exclude .git --exclude /build --exclude '__pycache__' --exclude '*.private.yaml' \
        $( [ -f .git/info/exclude ] && echo "--exclude-from=$ROOT/.git/info/exclude" ) "$ROOT/" "$work/" || return 1
    [ "$NOPY" = 1 ] && args+=(-NoPythonInstall)
    if [ -f "$agent" ]; then
        mkdir -p "$work/build/.work/stage"
        cp "$agent" "$work/build/.work/stage/ctt-agent"
        args+=(-Agent 'build\.work\stage\ctt-agent')
        echo "embedding the aarch64 agent (Deploy agent): $agent"
    else
        echo "warning: no build/ctt-agent-aarch/ctt-agent - the .exe cannot deploy the agent to a board"
    fi
    (cd /mnt/c && powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$work_win\\window-app\\build.ps1" "${args[@]}") || return 1
    mkdir -p "$OUT/ctt-agent-window"
    for exe in ctt-agent.exe ctt-agent-UI.exe; do
        cp "$work/build/ctt-agent-window/$exe" "$OUT/ctt-agent-window/$exe" || return 1
    done
}

for t in linux aarch window; do
    [[ " ${TARGETS[*]} " == *" $t "* ]] || continue
    if [ "$t" = window ] && [[ " ${TARGETS[*]} " == *" aarch "* ]] && [ "${STATUS[aarch]}" != OK ]; then
        echo "note: aarch failed - building the .exe without the board agent"
    fi
    step "$t" "build_$t"
done

(cd "$OUT" && find ctt-agent-* -type f | sort | xargs -r sha256sum > SHA256SUMS)

echo
echo "CTT $VERSION"
rc=0
for t in linux aarch window; do
    [ -n "${STATUS[$t]:-}" ] || continue
    [ "${STATUS[$t]}" = OK ] || rc=1
    printf '  %-8s %-7s' "$t" "${STATUS[$t]}"
    for f in "$OUT/ctt-agent-$t"/*; do
        [ -f "$f" ] && printf '  build/%s (%s)' "${f#"$OUT"/}" "$(du -h "$f" | cut -f1)"
    done
    echo
done
exit $rc
