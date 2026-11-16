#!/usr/bin/env python3
"""Do Serum's filter warps change phase like a first-order filter? Phase of harmonics 1..6 of the warped saw minus the unwarped saw's, against -atan(f/c) (LP) / +atan(c/f) (HP)."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import probe_vfilter as V
import test_render_calibration as H
_, bt = H.load_preset("Keys - Loop")
t0 = H.init_tree(bt, table="S2 Tables/Default Shapes.wav", frames=9)
pool = V.SerumPool()
SR = H.SR
def cplx(x, f0, ks):
    m = x[0, int(0.35 * SR):int(1.2 * SR)]
    n = np.arange(len(m)) / SR
    return [np.sum(m * np.hanning(len(m)) * np.exp(-2j * np.pi * k * f0 * n)) for k in ks]
def render(mode, a):
    t = copy.deepcopy(t0)
    t["Oscillator0"]["WTOsc0"]["plainParams"] = {"kParamTablePos": 1.0, "kParamWarpMenu": mode, "kParamWarp": a}
    return pool.render(t)
f0 = 440 * 2 ** ((V.NOTE - 69) / 12)
ks = list(range(1, 7))
b1, b2 = cplx(render("kNone", 0.0), f0, ks), cplx(render("kNone", 0.0), f0, ks)
print("base run-to-run phase diff (deg):", " ".join("%6.1f" % np.degrees(np.angle(p / q)) for p, q in zip(b1, b2)))
for mode, a, c in (("kFilterLPF", 0.3, 331.1), ("kFilterLPF", 0.5, 1288.2), ("kFilterHPF", 0.5, 1318.3), ("kFilterHPF", 0.7, 5128.6)):
    w = cplx(render(mode, a), f0, ks)
    got = [np.degrees(np.angle(p / q)) for p, q in zip(w, b1)]
    if mode == "kFilterLPF":
        want = [-np.degrees(np.arctan(k * f0 / c)) for k in ks]
    else:
        want = [np.degrees(np.arctan(c / (k * f0))) for k in ks]
    print(mode[7:], a, "got ", " ".join("%6.1f" % g for g in got))
    print(mode[7:], a, "want", " ".join("%6.1f" % g for g in want))
sys.stdout.flush(); os._exit(0)
