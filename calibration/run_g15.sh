#!/bin/sh
# One faithful cycle: convert -> loudness fit (new sounds only) -> convert again with the fits -> keep the limiter out -> report.
cd "$(dirname "$0")/.."
T="$(python converter/serum_paths.py tables)"      # set SERUM_ROOT if Serum is not in the default place
PK="Loopsy - DEEP HOUSE.SerumPack"
PRE=out_g15/move/data/UserData/schwung/presets/tinyhouse
python -u converter/deephouse2move.py "$PK" -o out_g15 --serum-tables "$T" > calibration/g15_convert1.out 2>&1
python -u calibration/fit_level.py > calibration/g15_level.out 2>&1
python -u converter/deephouse2move.py "$PK" -o out_g15 --serum-tables "$T" > calibration/g15_convert2.out 2>&1
python -u calibration/dev/post_level.py "$PRE" calibration/g15_final > calibration/g15_post.out 2>&1
python -u calibration/dev/chain_report.py calibration/g14_final calibration/g15_final --out calibration/chain_report_g15.json > calibration/chain_report_g15.out 2>&1
echo done > calibration/g15.done
