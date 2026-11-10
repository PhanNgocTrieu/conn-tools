#!/usr/bin/env bash
# Cross-compile ctt-agent for the DUT (board, aarch64).
#
#   scripts/build-dut.sh --sdk /opt/<sdk>/environment-setup-<target>   # Yocto SDK (preferred)
#   scripts/build-dut.sh --generic [--static]                           # Ubuntu cross gcc
#
# Yocto SDK: produced by `bitbake <image> -c populate_sdk`; the binary then
# links against exactly the DUT's glibc/libstdc++.
# Generic: `sudo apt install g++-aarch64-linux-gnu`; use --static so the binary
# does not depend on the DUT's library versions.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BUILD="$ROOT/build/dut"
SDK_ENV=""
GENERIC=0
STATIC=OFF

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sdk) SDK_ENV="$2"; shift 2 ;;
        --generic) GENERIC=1; shift ;;
        --static) STATIC=ON; shift ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 64 ;;
    esac
done

CMAKE_ARGS=(-DCMAKE_BUILD_TYPE=Release -DCTT_BUILD_TESTS=OFF -DCTT_STATIC="$STATIC")

if [[ -n "$SDK_ENV" ]]; then
    [[ -f "$SDK_ENV" ]] || { echo "SDK environment script not found: $SDK_ENV" >&2; exit 1; }
    # shellcheck disable=SC1090
    set +u; source "$SDK_ENV"; set -u
    : "${OECORE_NATIVE_SYSROOT:?SDK environment did not set OECORE_NATIVE_SYSROOT}"
    TOOLCHAIN="${OE_CMAKE_TOOLCHAIN_FILE:-$OECORE_NATIVE_SYSROOT/usr/share/cmake/OEToolchainConfig.cmake}"
    CMAKE_ARGS+=(-DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN")
    echo "Using Yocto SDK: $SDK_ENV"
elif [[ $GENERIC -eq 1 ]]; then
    command -v aarch64-linux-gnu-g++ >/dev/null || {
        echo "aarch64-linux-gnu-g++ not found: sudo apt install g++-aarch64-linux-gnu" >&2; exit 1; }
    CMAKE_ARGS+=(-DCMAKE_TOOLCHAIN_FILE="$ROOT/cmake/toolchains/aarch64-linux-gnu.cmake")
    echo "Using generic aarch64-linux-gnu toolchain (static=$STATIC)"
else
    echo "choose --sdk <environment-setup-file> or --generic" >&2; exit 64
fi

rm -rf "$BUILD"
cmake -S "$ROOT" -B "$BUILD" "${CMAKE_ARGS[@]}"
cmake --build "$BUILD" -j"$(nproc)"
file "$BUILD/agent/ctt-agent" 2>/dev/null || true
echo
echo "DUT agent: $BUILD/agent/ctt-agent"
echo "deploy with: python3 -m ctt deploy --dut root@<dut-ip>"
