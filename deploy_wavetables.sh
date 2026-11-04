#!/bin/sh
# Install the wavetables the presets name (out/.../Wavetables/<bank>/..., copied there by the converter) on the Move, every file checked.
#   BANK="Deep House" ./deploy_wavetables.sh [host]        default host: move.local, bank: Deep House
# Without them a preset's oscillators have no table to play. Serum's own and the pack's tables: for this device only, never committed or shared.
set -eu
HOST=${1:-move.local}
BANK=${BANK:-Deep House}
SSH="ssh -o ConnectTimeout=8 ableton@$HOST"
SRC="out/move/data/UserData/UserLibrary/Wavetables/$BANK"
DST="/data/UserData/UserLibrary/Wavetables/$BANK"
[ -d "$SRC" ] || { echo "no $SRC: nothing to install"; exit 0; }
norm() { sed -E 's/^([0-9a-f]+) [ *]/\1  /'; }
T=$(mktemp -d)
(cd "$SRC" && find . -type f -exec md5sum {} \; | norm | sort -k2) > "$T/want"
$SSH "mkdir -p '$DST'"
tar -C "$SRC" -cf - . | $SSH "tar -C '$DST' -xf -"
$SSH "cd '$DST' && find . -type f -exec md5sum {} \;" | norm | sort -k2 > "$T/got"
# the folder may hold tables of the user's own: every file installed here must match, extras are left alone
bad=$(comm -23 "$T/want" "$T/got" | wc -l)
[ "$bad" -eq 0 ] || { echo "$bad wavetable(s) differ or are missing:"; comm -23 "$T/want" "$T/got" | head; exit 1; }
echo "installed $(wc -l < "$T/want") wavetables in $BANK, all checksums match"
rm -rf "$T"
