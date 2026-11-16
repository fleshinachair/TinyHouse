#!/bin/sh
# FX buses, routing destinations and serial filters: refit the deep house presets they change, convert the pack, then the limiter-share cap on those presets only.
cd "$(dirname "$0")/.."
T="$(python converter/serum_paths.py tables)"      # set SERUM_ROOT if Serum is not in the default place
PK="Loopsy - DEEP HOUSE.SerumPack"
PRE=out_g16/move/data/UserData/schwung/presets/tinyhouse
python -u calibration/fit_level.py calibration/g16_changed.json > calibration/g16_level.out 2>&1
python -u converter/deephouse2move.py "$PK" -o out_g16 --serum-tables "$T" > calibration/g16_convert.out 2>&1
rm -rf calibration/g16_in && mkdir -p calibration/g16_in
python - <<'PY'
import json, shutil
from pathlib import Path
pre = Path("out_g16/move/data/UserData/schwung/presets/tinyhouse")
for n in json.load(open("calibration/g16_changed.json")):
    shutil.copy(pre / f"{n}.json", "calibration/g16_in")
PY
python -u calibration/dev/post_level.py calibration/g16_in calibration/g16_post > calibration/g16_post.out 2>&1
echo done > calibration/g16.done
