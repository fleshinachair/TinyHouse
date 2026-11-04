#!/bin/sh
# Install the converted tinyhouse presets on the Move, keeping a rollback copy and checking every file.
#
#   ./deploy_presets.sh [host]            default host: move.local; rollback folder: $RB or ...-rollback-p5
#   SRC="out_import/Factory Picks" BANK="Factory Picks" ./deploy_presets.sh   an importer bank (import_pack.py) into a bank of its own
#   BANK="Deep House" ./deploy_presets.sh   put the presets in that bank (a folder under the module's presets; the Bank knob lists them)
#                                         and take the same-named loose files out of the loose "User" bank
#
# Order: rollback copy of what is installed -> upload to a staging folder -> checksum the staging
# folder against out/ -> copy into place -> checksum the installed folder. Stops at the first mismatch,
# before anything installed has been touched unless the failure is in the last step (then the
# rollback folder has the previous files). No restart is needed: Schwung reads a preset when it loads.
# Does not touch the module (dsp.so); schwung-tablor/scripts/deploy.sh does that.
set -eu
HOST=${1:-move.local}
SSH="ssh -o ConnectTimeout=8 ableton@$HOST"
SRC=${SRC:-out/move/data/UserData/schwung/presets/tinyhouse}      # the converter's folder; an importer bank is SRC="out_import/Factory Picks"
BANK=${BANK:-}
ROOT_DST=/data/UserData/schwung/presets/tinyhouse
DST=$ROOT_DST${BANK:+/$BANK}
STAGE=/data/UserData/tinyhouse-presets-staging
RB=${RB:-/data/UserData/tinyhouse-rollback-p6}      # the tablor-dh copies stay in ...tablor-dh-rollback-p5

[ -d "$SRC" ] || { echo "no $SRC: run deephouse2move.py first"; exit 1; }
# Git Bash prints "hash *name", the Move prints "hash  name": same sums, so normalise before comparing
norm() { sed -E 's/^([0-9a-f]+) [ *]/\1  /'; }
want=$(cd "$SRC" && md5sum *.json | norm)

$SSH "mkdir -p '$DST' '$RB/presets' && { cp -p '$DST'/*.json '$RB/presets/' 2>/dev/null || true; } && ls '$RB/presets' | wc -l" | sed 's/^/rollback copy, files: /'
$SSH "rm -rf '$STAGE' && mkdir -p '$STAGE'"
tar -C "$SRC" -cf - . | $SSH "tar -C '$STAGE' -xf -"
got=$($SSH "cd '$STAGE' && md5sum *.json" | norm)
[ "$want" = "$got" ] || { echo "staging checksums differ: nothing installed"; exit 1; }
$SSH "mkdir -p '$DST' && cp -f '$STAGE'/*.json '$DST'/"
got=$($SSH "cd '$DST' && md5sum *.json" | norm)
echo "$got" | sort > /tmp/installed.md5
echo "$want" | sort > /tmp/wanted.md5
# the installed folder may hold presets of the user's own: every converted file must match, extras are left
missing=$(comm -23 /tmp/wanted.md5 /tmp/installed.md5 | wc -l)
[ "$missing" -eq 0 ] || { echo "$missing installed file(s) differ: restore from $RB/presets"; exit 1; }
if [ -n "$BANK" ]; then
  # the rollback copy above came from the bank folder; a first move into a bank also saves the loose files it replaces
  $SSH sh -s <<EOF
mkdir -p '$RB/loose'
cd '$STAGE'
for f in *.json; do
  if [ -f '$ROOT_DST'/"\$f" ]; then mv -f '$ROOT_DST'/"\$f" '$RB/loose'/; fi
done
EOF
fi
$SSH "rm -rf '$STAGE'"
echo "installed $(echo "$want" | wc -l) presets, all checksums match. Rollback: $RB/presets"
