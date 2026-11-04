#!/bin/sh
# The whole fit pipeline, in the order each depends on the one before: full-chain (filter, FX width), then
# tune (amp envelope, shelves, drive), then level. Each starts from scratch.
cd "$(dirname "$0")/.."
python -u calibration/fit_chain.py --fresh > calibration/fit_chain.out 2>&1
python -u calibration/fit_tune.py --fresh > calibration/fit_tune.out 2>&1
python -u calibration/fit_level.py --fresh > calibration/fit_level.out 2>&1
echo pipeline done > calibration/run_fits.done
