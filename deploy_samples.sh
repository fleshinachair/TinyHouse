#!/bin/sh
# Install the sample sets (.tbs, made by sample_export.py for the presets' Sample / Multisample oscillators) on the Move, checked file by file.
#   ./deploy_samples.sh [host]        default host: move.local
# (one folder per bank, so */*.tbs; a glob copes with the space in 'Deep House', which xargs on the Move cannot).
# They are Serum's own factory sounds: for this device only, never committed or shared.
set -eu
HOST=${1:-move.local}
SSH="ssh -o ConnectTimeout=8 ableton@$HOST"
SRC=out/move/data/UserData/UserLibrary/Samples
DST=/data/UserData/UserLibrary/Samples
[ -d "$SRC" ] || { echo "no $SRC: nothing to install"; exit 0; }
norm() { sed -E 's/^([0-9a-f]+) [ *]/\1  /'; }
want=$(cd "$SRC" && md5sum */*.tbs | norm | sort -k2)
$SSH "mkdir -p '$DST'"
tar -C "$SRC" -cf - . | $SSH "tar -C '$DST' -xf -"
got=$($SSH "cd '$DST' && md5sum */*.tbs" | norm | sort -k2)
[ "$want" = "$got" ] || { echo "sample checksums differ"; exit 1; }
echo "installed $(echo "$want" | wc -l) sample sets, all checksums match"
