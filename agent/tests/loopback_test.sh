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
    "$AGENT" server --once --json -t "$TRANSPORT" -e "$EP" --accept-timeout 10 -i 0 "${SOPTS[@]}" >"$log" 2>&1 &
    local spid=$!
    for _ in $(seq 100); do
        grep -q '"event":"listening"' "$log" && break
        sleep 0.05
    done

    local out
    if ! out=$("$AGENT" client --json -t "$TRANSPORT" -e "$EP" "${SOPTS[@]}" -m "$mode" -d 1 -i 0 "$@" 2>&1); then
        echo "FAIL $mode: client exited non-zero"; echo "$out"; fail=1
    elif ! grep -q '"event":"result","ok":true' <<<"$out"; then
        echo "FAIL $mode: no ok result"; echo "$out"; fail=1
    elif grep -qE '"(lost|crc_errors)":[1-9]' <<<"$out"; then
        echo "FAIL $mode: loss/CRC errors on loopback"; echo "$out"; fail=1
    else
        echo "ok   $TRANSPORT ${SOPTS[*]} $mode $*"
    fi
    if ! wait "$spid"; then
        echo "FAIL $mode: server exited non-zero"; cat "$log"; fail=1
    fi
}

TRANSPORT=tcp; EP="127.0.0.1:$PORT"; SOPTS=()
run_mode tx
run_mode rx
run_mode bidir
run_mode echo --echo-interval 1
run_mode tx -b 990 --rate 5M
run_mode tx --no-verify

# Packet transport path (same code as L2CAP): frames larger than the MTU.
TRANSPORT=unix; EP="@ctt-loopback-$PORT"; SOPTS=(-o type=seqpacket -o mtu=100)
run_mode bidir -b 990
run_mode echo -b 300 --echo-interval 1
TRANSPORT=unix; EP="@ctt-loopback-$PORT"; SOPTS=(-o type=stream)
run_mode rx
exit $fail
