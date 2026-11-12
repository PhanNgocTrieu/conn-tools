#!/bin/sh
# Copy the ctt-agent sources into an vendor-style Yocto recipe folder (source kept next to the recipe,
# no git fetch), e.g. for meta-project/recipes-project/ctt-agent/. Nothing is built here.
#
#   scripts/export-recipe.sh [RECIPE_DIR]      default: recipes-project/ctt-agent
#
# Result:  RECIPE_DIR/ctt-agent.bb             only if the folder has no .bb yet (yocto/recipe-local-src/)
#          RECIPE_DIR/files/ctt-agent/   CMakeLists.txt, agent/{CMakeLists.txt,include,src},
#                                        scripts/dut-usb-gadget.sh, SOURCE-COMMIT
#          RECIPE_DIR/files/ctt-agent-bt.service
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEST=${1:-$ROOT/recipes-project/ctt-agent}
SRC=$DEST/files/ctt-agent

rm -rf "$SRC"
mkdir -p "$SRC/agent" "$SRC/scripts"
cp "$ROOT/CMakeLists.txt" "$SRC/"
cp "$ROOT/agent/CMakeLists.txt" "$SRC/agent/"
cp -r "$ROOT/agent/include" "$ROOT/agent/src" "$SRC/agent/"
cp "$ROOT/scripts/dut-usb-gadget.sh" "$SRC/scripts/"
cp "$ROOT/yocto/meta-ctt/recipes-connectivity/ctt-agent/files/ctt-agent-bt.service" "$DEST/files/"

# which commit the copy came from (+ "-dirty" when the working tree had local changes)
rev=$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)
git -C "$ROOT" diff --quiet HEAD -- CMakeLists.txt agent scripts/dut-usb-gadget.sh 2>/dev/null || rev="$rev-dirty"
echo "$rev" > "$SRC/SOURCE-COMMIT"

# first time: start from the recipe template kept in this repo
ls "$DEST"/*.bb >/dev/null 2>&1 || cp "$ROOT/yocto/recipe-local-src/ctt-agent.bb" "$DEST/"

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
