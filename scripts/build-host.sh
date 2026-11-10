#!/usr/bin/env bash
# Build ctt-agent for this machine (WSL / Linux x86_64) and run the tests.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BUILD="$ROOT/build/host"

cmake -S "$ROOT" -B "$BUILD" -DCMAKE_BUILD_TYPE="${BUILD_TYPE:-Release}"
cmake --build "$BUILD" -j"$(nproc)"
ln -sf "$BUILD/compile_commands.json" "$ROOT/compile_commands.json"

if [[ "${1:-}" != "--no-test" ]]; then
    (cd "$BUILD" && ctest --output-on-failure)
fi
echo "host agent: $BUILD/agent/ctt-agent"
