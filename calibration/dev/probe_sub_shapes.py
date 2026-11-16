#!/usr/bin/env python3
"""Serum's Sub oscillator shapes against the engine's: the level (dB re the fundamental) of the first harmonics of a held sub note (A2, sub octave 0), every shape
Serum has, and the engine's six sub waves.  Output: printed."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import fxmap
import test_render_calibration as H

SR = H.SR
NOTE = 45
F0 = 440 * 2 ** ((NOTE - 69) / 12)
NH = 24
def harm(x):
    m = x[0, int(0.4 * SR):int(1.4 * SR)]
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)), 1 << 16)); f = np.fft.rfftfreq(1 << 16, 1 / SR)
    h = np.array([20 * np.log10(sp[max(np.searchsorted(f, k * F0) - 4, 0):np.searchsorted(f, k * F0) + 5].max() + 1e-12) for k in range(1, NH + 1)])
    return h - h[0]
serum, base = P.start()
for shape in ("kSine", "kTriangle", "kSaw", "kSquare", "kPulse", "kRoundRect"):
    t = copy.deepcopy(base)
    t["Oscillator3"]["plainParams"] = {"kParamEnable": 0.0}
    t["Oscillator4"] = {"plainParams": {"kParamEnable": 1.0, "kParamVolume": 0.5}, "SubOsc4": {"plainParams": {"kParamShape": shape}}}
    t["RoutingSlot4"] = {"plainParams": {"kParamRoutingDest": "kRoutingDestMaster"}}
    h = harm(serum.render(t, [NOTE], 2.0, 1.6))
    print("serum %-11s" % shape, " ".join("%5.0f" % x for x in h[:14]), flush=True)
for w, name in enumerate(["sine", "triangle", "saw", "square", "pulse25", "pulse12"]):
    b = fxmap.noise_patch({"noise_level": 0, "sub_level": 80, "sub_wave": w, "sub_tune": 0, "fx_on": 0})
    x = H.run_cli(b, [NOTE], 2.0, 1.6)
    print("engine %-10s" % name, " ".join("%5.0f" % x_ for x_ in harm(x)[:14]), flush=True)
os._exit(0)
