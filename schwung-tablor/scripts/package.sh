#!/usr/bin/env bash
#
# Build the release tarball: dist/tinyhouse-module.tar.gz, with one top-level folder, tinyhouse/, ready to unpack into
# /data/UserData/schwung/modules/sound_generators/ on the Move. Build first (bash scripts/zig-build.sh arm) and run the
# load test on the device; this script only gathers files.
#
#   ./scripts/package.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$SRC/.." && pwd)"
VERSION="$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$SRC/src/module.json" | head -n 1)"
OUT="$SRC/dist/tinyhouse-module.tar.gz"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

[ -f "$SRC/build/dsp.so" ] || { echo "no build/dsp.so: run scripts/zig-build.sh arm first" >&2; exit 1; }
python "$SRC/tools/build_import_bundle.py"

D="$STAGE/tinyhouse"
mkdir -p "$D"
cp "$SRC/build/dsp.so" "$D/dsp.so"
cp "$SRC/src/module.json" "$SRC/src/ui_pages.json" "$SRC/src/help.json" "$SRC/src/web_ui.html" "$SRC/src/ui/canvas.js" "$SRC/src/ui/cards.js" "$D/"
cp "$SRC/build/import_bundle.zip" "$D/import_bundle.zip"
cp "$ROOT/LICENSE" "$D/LICENSE"
cp "$SRC/LICENSE" "$D/LICENSE-engine"
chmod 755 "$D/dsp.so"

mkdir -p "$SRC/dist"
tar -C "$STAGE" -czf "$OUT" tinyhouse
echo "tinyhouse $VERSION -> $OUT"
tar -tzf "$OUT"
( cd "$SRC/dist" && sha256sum tinyhouse-module.tar.gz )
