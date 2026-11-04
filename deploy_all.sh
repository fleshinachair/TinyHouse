#!/bin/sh
# Install everything TinyHouse needs on the Move, in the order that is safe: the sample sets, then the presets (they name them) (a preset with keys the old
# module does not know is read fine; an old preset on a new module is too), then the module, then the on-device load test
# and a check of every checksum.   ./deploy_all.sh [host]      default move.local
set -eu
HOST=${1:-move.local}
cd "$(dirname "$0")"
BANK="${BANK:-Deep House}" sh ./deploy_wavetables.sh "$HOST"
sh ./deploy_samples.sh "$HOST"
RB=${RB:-/data/UserData/tinyhouse-rollback-$(date +%Y%m%d-%H%M)} sh ./deploy_presets.sh "$HOST"
(cd schwung-tablor && bash scripts/deploy.sh "ableton@$HOST")
want=$(md5sum schwung-tablor/build/dsp.so | cut -d' ' -f1)
got=$(ssh -o ConnectTimeout=8 "ableton@$HOST" "md5sum /data/UserData/schwung/modules/sound_generators/tinyhouse/dsp.so" | cut -d' ' -f1)
[ "$want" = "$got" ] || { echo "dsp.so checksum differs: $want vs $got"; exit 1; }
ssh "ableton@$HOST" 'cd /data/UserData/schwung/modules/sound_generators/tinyhouse && ./tablor_loadtest ./dsp.so 2>&1 | grep -E "FAIL|LOADTEST"; rm -f "/data/UserData/schwung/presets/tinyhouse/Neu Bass.json"'
echo "installed: module $(ssh "ableton@$HOST" 'grep -o "\"version\": *\"[^\"]*\"" /data/UserData/schwung/modules/sound_generators/tinyhouse/module.json'), dsp.so matches"
