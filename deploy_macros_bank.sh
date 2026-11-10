#!/bin/sh
# Give the installed Deep House bank its Serum macros, keeping the levels it was fitted to, and remove the browser-imported test bank.
#
#   ./deploy_macros_bank.sh [host]          default host: 192.168.1.169
#
# 1. copies the bank's presets from the Move (what is installed is the starting point, not a fresh conversion)
# 2. calibration/add_macros.py adds the macro routes and names plus the few base values a macro-switched effect needs
# 3. deploy_presets.sh installs them back (rollback copy, checksums)
# 4. removes the bank "Loopsy UI Import" (presets, wavetables, samples) if it is there: BROWSER_BANK="" to skip
set -eu
HOST=${1:-192.168.1.169}
BANK=${BANK:-Deep House}
BROWSER_BANK=${BROWSER_BANK-Loopsy - UI Import}
SSH="ssh -o ConnectTimeout=8 ableton@$HOST"
ROOT_DST=/data/UserData/schwung/presets/tinyhouse
cd "$(dirname "$0")"
WORK=$(mktemp -d)
mkdir -p "$WORK/in" "$WORK/out"
$SSH "cd '$ROOT_DST/$BANK' && tar -cf - *.json" | tar -C "$WORK/in" -xf -
n=$(ls "$WORK/in" | wc -l)
[ "$n" -gt 0 ] || { echo "no presets in $ROOT_DST/$BANK"; exit 1; }
echo "copied $n presets of '$BANK' from the Move"
.venv/Scripts/python.exe -u calibration/add_macros.py "$WORK/in" "$WORK/out" --bank "$BANK" | tail -4
SRC="$WORK/out" BANK="$BANK" RB="/data/UserData/tinyhouse-rollback-macros-$(date +%Y%m%d-%H%M)" sh ./deploy_presets.sh "$HOST"
if [ -n "$BROWSER_BANK" ]; then
  for d in "$ROOT_DST" /data/UserData/UserLibrary/Wavetables /data/UserData/UserLibrary/Samples; do
    $SSH "if [ -d '$d/$BROWSER_BANK' ]; then rm -rf '$d/$BROWSER_BANK' && echo 'removed $d/$BROWSER_BANK'; else echo 'not there: $d/$BROWSER_BANK'; fi"
  done
fi
echo "done: restart the Shadow UI or reload the slot so the module rescans the banks"
