#!/usr/bin/env python3
"""The multiband compressor's thresholds when ThreshUD0..2 are not written (Lead - Consoul Training): noise through a Comp with Multiband on and Makeup 1,
the thresholds left out, written as 100 (the converter's assumed default) and as 143 / 170 / 200, the output level against the same noise without it.
Output: printed (dB, and the same for the levels of the three bands is implied by the broadband result)."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H
SR = H.SR
serum, base = P.start()
def lvl(x): return 10 * np.log10((x.mean(axis=0)[int(0.5 * SR):int(2.0 * SR)] ** 2).mean() + 1e-20)
def render(pp):
    t = copy.deepcopy(base)
    t["FXRack0"]["FX"] = [{"FXComp": {"plainParams": pp}, "type": 5}] if pp is not None else []
    return lvl(serum.render(t, [48], 2.5, 2.4))
dry = np.mean([render(None) for _ in range(2)])
print("dry noise level %.1f dB" % dry)
for label, ud in (("unwritten", None), ("100", 100.0), ("143", 143.0), ("170", 170.0), ("200", 200.0)):
    pp = {"kParamMultiband": 1.0}
    if ud is not None:
        pp.update({f"kParamThreshUD{b}": ud for b in range(3)})
    print("multiband, thresholds %-10s: output %+5.1f dB re dry" % (label, np.mean([render(pp) for _ in range(2)]) - dry), flush=True)
pp = {"kParamMultiband": 0.0, "kParamThresh": 0.5}
print("single band default ratio, thresh 0.5: output %+5.1f dB re dry" % (render(pp) - dry))
os._exit(0)
