#!/bin/sh
# Export ctt-agent as a Yocto recipe whose sources sit next to it in your layer (file://, no git
# fetch, no network: for build machines that cannot reach this repository). Nothing is built here.
#
#   scripts/export-recipe.sh [RECIPE_DIR]      e.g. <your-layer>/recipes-connectivity/ctt-agent
#                                              default: output/ctt-agent-recipe
#
# Result:  RECIPE_DIR/ctt-agent.bb             only if the folder has no .bb yet (packaging/yocto/recipe-local-src/)
#          RECIPE_DIR/files/ctt-agent/         CMakeLists.txt, agent/ (C++ sources,
#                                              scripts/dut-usb-gadget.sh inside it), SOURCE-COMMIT
#          RECIPE_DIR/files/ctt-agent-bt.service
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEST=${1:-$ROOT/output/ctt-agent-recipe}
SRC=$DEST/files/ctt-agent

rm -rf "$SRC"
mkdir -p "$SRC/agent"
cp "$ROOT/CMakeLists.txt" "$SRC/"
cp -r "$ROOT/agent/CMakeLists.txt" "$ROOT/agent/include" "$ROOT/agent/src" "$ROOT/agent/scripts" "$SRC/agent/"
SVC="$ROOT/packaging/systemd"
cp "$SVC/ctt-agent-bt.service" "$SVC/ctt-agent-ble.service" "$DEST/files/"

# which commit the copy came from (+ "-dirty" when the working tree had local changes)
rev=$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)
git -C "$ROOT" diff --quiet HEAD -- CMakeLists.txt agent 2>/dev/null || rev="$rev-dirty"
echo "$rev" > "$SRC/SOURCE-COMMIT"

# first time: start from the recipe template kept in this repo
ls "$DEST"/*.bb >/dev/null 2>&1 || cp "$ROOT/packaging/yocto/recipe-local-src/ctt-agent.bb" "$DEST/"

# keep the recipe version (PV) equal to the agent version (CMakeLists.txt), if the recipe is there
ver=$(sed -n 's/^project(connectivity-tools VERSION \([0-9.]*\).*/\1/p' "$ROOT/CMakeLists.txt")
for bb in "$DEST"/*.bb; do
    [ -f "$bb" ] || continue
    sed -i "s/^PV = \"[^\"]*\"/PV = \"$ver\"/" "$bb"
    echo "$(basename "$bb"): PV = \"$ver\""
done

echo "copied ctt-agent sources ($rev) to $SRC"
find "$SRC" -type f | wc -l | xargs echo "files:"
du -sh "$SRC" | cut -f1 | xargs echo "size:"
