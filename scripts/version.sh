#!/bin/sh
# One version for the whole tool: agent (CMakeLists.txt), Python apps (pyproject.toml, ctt/__init__.py).
#
#   scripts/version.sh                 show the version of each place
#   scripts/version.sh set 0.2.0       write 0.2.0 everywhere (then commit, tag v0.2.0)
#   scripts/version.sh check 0.2.0     exit 1 if any place differs (used by CI on a v* tag)
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
CM=$ROOT/CMakeLists.txt
PP=$ROOT/pyproject.toml
PY=$ROOT/ctt/__init__.py

cmake_v() { sed -n 's/^project(connectivity-tools VERSION \([0-9.]*\).*/\1/p' "$CM"; }
pyproj_v() { sed -n 's/^version = "\(.*\)"/\1/p' "$PP"; }
python_v() { sed -n 's/^__version__ = "\(.*\)"/\1/p' "$PY"; }

case "${1:-show}" in
show)
    echo "CMakeLists.txt   board / Linux agent  $(cmake_v)"
    echo "pyproject.toml   Python package      $(pyproj_v)"
    echo "ctt/__init__.py  Windows apps        $(python_v)"
    ;;
set)
    v=${2:?usage: version.sh set X.Y.Z}
    echo "$v" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || { echo "version must be X.Y.Z, got '$v'" >&2; exit 2; }
    sed -i "s/^project(connectivity-tools VERSION [0-9.]*/project(connectivity-tools VERSION $v/" "$CM"
    sed -i "s/^version = \".*\"/version = \"$v\"/" "$PP"
    sed -i "s/^__version__ = \".*\"/__version__ = \"$v\"/" "$PY"
    "$0" show
    ;;
check)
    v=${2:?usage: version.sh check X.Y.Z}
    bad=0
    for got in "$(cmake_v)" "$(pyproj_v)" "$(python_v)"; do
        [ "$got" = "$v" ] || bad=1
    done
    "$0" show
    if [ $bad -ne 0 ]; then
        echo "version mismatch: tag says $v. Run: scripts/version.sh set $v, commit, then tag again" >&2
        exit 1
    fi
    echo "all at $v"
    ;;
*)
    echo "usage: version.sh [show | set X.Y.Z | check X.Y.Z]" >&2
    exit 2
    ;;
esac
