#!/usr/bin/env bash
# End-to-end test: run server + client over TCP loopback in every mode.
# Usage: loopback_test.sh /path/to/ctt-agent
set -euo pipefail

AGENT=${1:?usage: $0 /path/to/ctt-agent}
PORT=$(( 20000 + RANDOM % 20000 ))
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fail=0

run_mode() {
    local mode=$1; shift
    local log="$TMP/server.log"
    : >"$log"
    "$AGENT" server --once --json -e "127.0.0.1:$PORT" --accept-timeout 10 -i 0 >"$log" 2>&1 &
    local spid=$!
    for _ in $(seq 100); do
        grep -q '"event":"listening"' "$log" && break
        sleep 0.05
    done

    local out
    if ! out=$("$AGENT" client --json -e "127.0.0.1:$PORT" -m "$mode" -d 1 -i 0 "$@" 2>&1); then
        echo "FAIL $mode: client exited non-zero"; echo "$out"; fail=1
    elif ! grep -q '"event":"result","ok":true' <<<"$out"; then
        echo "FAIL $mode: no ok result"; echo "$out"; fail=1
    elif grep -qE '"(lost|crc_errors)":[1-9]' <<<"$out"; then
        echo "FAIL $mode: loss/CRC errors on loopback"; echo "$out"; fail=1
    else
        echo "ok   $mode $*"
    fi
    if ! wait "$spid"; then
        echo "FAIL $mode: server exited non-zero"; cat "$log"; fail=1
    fi
}

run_mode tx
run_mode rx
run_mode bidir
run_mode echo --echo-interval 1
run_mode tx -b 990 --rate 5M
run_mode tx --no-verify
exit $fail
