#!/usr/bin/env python3
"""Serum's SplitMS node: which branch is Mid and which Side, and whether the split is transparent.  A wide saw (unison 7 spread 100 %) through [SplitMS(ModuleCount2 = 1), EQ A, EQ B]
(branch 1 = the first ModuleCount1 modules, branch 2 = the next ModuleCount2) with a +12 dB peak at 1 kHz in A or in B, against the same signal with no SplitMS.  Prints the
change of the mid (L+R) and side (L-R) third-octave bands near 1 kHz and of the overall level.  Output: printed."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H
SR = H.SR
def ms_bands(x, centres=(250, 500, 1000, 2000, 4000)):
    m = (x[0] + x[1]) / 2; s = (x[0] - x[1]) / 2
    out = []
    for y in (m, s):
        y = y[int(0.4 * SR):int(1.8 * SR)]; sp = np.abs(np.fft.rfft(y * np.hanning(len(y)))) ** 2; f = np.fft.rfftfreq(len(y), 1 / SR)
        out.append([10 * np.log10(sp[(f >= c / 1.26) & (f < c * 1.26)].sum() + 1e-20) for c in centres])
    return np.array(out)
serum, base = P.start()
base = copy.deepcopy(base)
base["Oscillator0"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.6, "kParamUnison": 7.0, "kParamDetune": 0.5, "kParamUnisonStereo": 100.0}
def rack(nodes):
    t = copy.deepcopy(base); t["FXRack0"]["FX"] = nodes; return t
def eq(gain):
    return {"FXEQ": {"plainParams": {"kParamType2": 1.0, "kParamFreq2": 1000.0, "kParamGain2": gain, "kParamReso2": 50.0}}, "type": 7}
split = {"FXSplitMS": {"plainParams": {"kParamModuleCount2": 1.0}}, "type": 15}
ref = ms_bands(serum.render(rack([]), [48], 2.0, 1.9))
def eqf(freq, gain=12.0):
    return {"FXEQ": {"plainParams": {"kParamType2": 1.0, "kParamFreq2": float(freq), "kParamGain2": gain, "kParamReso2": 50.0}}, "type": 7}
FREQS = (500, 1000, 2000)
print("three EQs after the split, +12 dB peaks at 500 / 1000 / 2000 Hz (A, B, C); mid | side change at 500 1000 2000 Hz")
for lab, pp in (("counts unwritten", {}), ("count1 1, count2 1", {"kParamModuleCount1": 1.0, "kParamModuleCount2": 1.0}), ("count1 unwritten, count2 1", {"kParamModuleCount2": 1.0}), ("count1 2, count2 1", {"kParamModuleCount1": 2.0, "kParamModuleCount2": 1.0}),
                ("count1 1, count2 2", {"kParamModuleCount1": 1.0, "kParamModuleCount2": 2.0}), ("count1 0, count2 1", {"kParamModuleCount1": 0.0, "kParamModuleCount2": 1.0})):
    node = {"FXSplitMS": {"plainParams": pp}, "type": 15}
    x = serum.render(rack([node] + [eqf(f) for f in FREQS]), [48], 2.0, 1.9)
    d = ms_bands(x, centres=FREQS) - ms_bands(serum.render(rack([]), [48], 2.0, 1.9), centres=FREQS)
    print("%-28s mid %s | side %s" % (lab, " ".join("%+5.1f" % v for v in d[0]), " ".join("%+5.1f" % v for v in d[1])), flush=True)
os._exit(0)
