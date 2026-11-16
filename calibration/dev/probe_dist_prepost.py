#!/usr/bin/env python3
"""Which of PrePost 1 and 2 puts the Distortion's filter before the distorting: noise at a loud level through Overdrive at Drive 100 with the filter low
(Freq 0.3). A filter after the distortion removes everything above it; one before leaves the harmonics the clipping makes."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import fx_probe as P
from probe_fxfilter_drive import GRID2, tf2

serum, base = P.start()
b = copy.deepcopy(base)
b["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 1.0}
dry = P.render(serum, b)
print("grid", GRID2)
for name, pp in [("no filter", {}), ("prepost 1", {"kParamPrePost": 1.0}), ("prepost 2", {"kParamPrePost": 2.0})]:
    pp = dict(pp, kParamDrive=100.0, kParamMode="kOverdrive", kParamFreq=0.3, kParamBW=1.0, kParamWet=100.0)
    g = tf2(dry, serum.render(P.fx_tree(b, "Distortion", pp, 0), [48], 2.0, 1.9))
    print(name.ljust(14), " ".join("%6.1f" % x for x in g), flush=True)
os._exit(0)
