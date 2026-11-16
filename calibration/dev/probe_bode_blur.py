#!/usr/bin/env python3
"""Serum's Bode with only Blur stored (PL - Envoker: Blur 99.21, Wet unstored = 100): residual of the node against none, saw on note 48, after the render's own residual (dry against dry)."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration")); os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H
SR = H.SR
serum, base = P.start()
base = copy.deepcopy(base); base["Oscillator0"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.6}
def seg(x): return x[:, int(0.3 * SR):int(1.8 * SR)]
a = seg(serum.render(base, [48], 2.0, 1.9)); b = seg(serum.render(base, [48], 2.0, 1.9))
def res(y, ref): return 10 * np.log10(((y - ref) ** 2).sum() / (ref ** 2).sum() + 1e-20)
print("dry vs dry residual %.1f dB" % res(b, a))
for lab, pp in {"Blur 99.21": {"kParamBlur": 99.21}, "Blur 50": {"kParamBlur": 50.0}, "Blur 8.77 DelayBalance -14.4 (Freak)": {"kParamBlur": 8.77, "kParamDelayBalance": -14.43},
                "Wet 100 only": {"kParamWet": 100.0}, "Shift 0 range 10": {"kParamShift": 0.0, "kParamRange": 10.0}}.items():
    y = seg(serum.render(P.fx_tree(base, "Bode", pp, 10), [48], 2.0, 1.9))
    print("%-38s residual %.1f dB   level %+.2f dB" % (lab, res(y, a), 10 * np.log10((y ** 2).sum() / (a ** 2).sum())), flush=True)
os._exit(0)
