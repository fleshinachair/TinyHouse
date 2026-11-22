#!/usr/bin/env bash
#
# Install the TinyHouse module on the Move SAFELY.
#
# Critical: never scp directly over a live dsp.so. The shim dlopen()s it into
# MoveOriginal, so overwriting the file mutates the mmap'd code pages of a
# running process — which segfaults the whole firmware. Upload to a temp name,
# then mv: rename(2) is atomic and leaves the old inode intact for the running
# process. New code is picked up when the slot next loads the module.
#
#   ./scripts/deploy.sh [host]      (default: move.local)
set -euo pipefail

HOST="${1:-move.local}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="/data/UserData/schwung/modules/sound_generators/tinyhouse"

[ -f "$SRC/build/dsp.so" ] || { echo "no build/dsp.so — run ./scripts/build.sh first" >&2; exit 1; }

echo "==> $HOST:$DEST"
"$(dirname "$0")/move_check_owner.sh" "$HOST"    # refuse to copy onto a Move whose / or /data ownership is already wrong
ssh "$HOST" "mkdir -p $DEST/wavetables"

ssh "$HOST" "mkdir -p $DEST/presets"
scp -q "$SRC/build/dsp.so"          "$HOST:$DEST/dsp.so.new"
scp -q "$SRC/src/module.json"       "$HOST:$DEST/module.json.new"
scp -q "$SRC/src/ui_pages.json"     "$HOST:$DEST/ui_pages.json.new"
scp -q "$SRC/src/help.json"         "$HOST:$DEST/help.json"
scp -q "$SRC/src/web_ui.html"       "$HOST:$DEST/web_ui.html"
scp -q "$SRC/src/ui/canvas.js"      "$HOST:$DEST/canvas.js"      # the Macros and Tone page cells (grey out unused macros and oscillators)
scp -q "$SRC/src/ui/cards.js"       "$HOST:$DEST/cards.js"       # the wavetable card raised while a Tone-page position knob is held
# The remote UI's Import runs the converter from this bundle (python tools/build_import_bundle.py makes it)
if [ -f "$SRC/build/import_bundle.zip" ]; then
    scp -q "$SRC/build/import_bundle.zip" "$HOST:$DEST/import_bundle.zip"
else
    echo "note: no build/import_bundle.zip (python tools/build_import_bundle.py): the Import button will say the converter is missing" >&2
fi
# Convert on load: the module's own converter (python tools/build_converter_dir.py makes it). Sent as a new folder and swapped in whole, so the engine
# never sees half of one; a slot that loaded the old dsp.so keeps working with what it has (it reads the converter's stamp once, at load).
if [ -d "$SRC/build/converter" ]; then
    ssh "$HOST" "rm -rf $DEST/converter.new"
    scp -q -r "$SRC/build/converter" "$HOST:$DEST/converter.new"
    ssh "$HOST" "cd $DEST && rm -rf converter.old && { [ ! -d converter ] || mv converter converter.old; } && mv converter.new converter && rm -rf converter.old"
else
    echo "note: no build/converter (python tools/build_converter_dir.py): presets will not be converted again on load" >&2
fi
# No factory.tbl: the engine seeds a module's preset folder from it on first load, and TinyHouse
# carries only the converted Deep House presets (installed by ../deploy_presets.sh), not stock Tablor's.
ssh "$HOST" "rm -f $DEST/presets/factory.tbl"

# Atomic swap. Do NOT replace this with a direct scp.
# (also removes the retired custom editor — Schwung 0.12+'s stock
# hierarchy UI is the interface now)
ssh "$HOST" "cd $DEST && \
    mv -f dsp.so.new dsp.so && \
    mv -f module.json.new module.json && \
    mv -f ui_pages.json.new ui_pages.json && \
    rm -f ui_chain.js && \
    chmod 755 dsp.so && ls -l dsp.so module.json"

# Factory wavetables, if present locally
if command -v rsync >/dev/null && [ -d "$SRC/src/wavetables" ] && [ -n "$(ls -A "$SRC/src/wavetables" 2>/dev/null)" ]; then
    rsync -az -e ssh "$SRC/src/wavetables/" "$HOST:$DEST/wavetables/"
fi

echo "==> done. Reload the slot (or restart the Shadow UI) to pick up new code."
