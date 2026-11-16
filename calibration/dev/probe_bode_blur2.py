#!/usr/bin/env python3
"""Serum's Bode with Blur only: does a note's attack and release tail change?  Short saw note (hold 0.12 s) on note 48: 5 ms-hop envelope of the first 60 ms and the tail after release, node against none.
Result (2026-11-13): Blur 99.21 alone leaves the attack inside the render's own spread (first 5 ms 5 dB lower, the unison phase varies that much) and the tail after release at -57 dB, as any Bode node does (even Blur 0, Wet 100).
probe_bode_blur.py: level -0.04 dB.  Blur with nothing to smear is transparent (fxmap bode_idle)."""
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
def env(x, t0, t1, hop):
    m = np.abs(x.mean(axis=0)); h = int(hop * SR); n = int((t1 - t0) / hop)
    return 20 * np.log10(np.array([m[int(t0 * SR) + i * h:int(t0 * SR) + (i + 1) * h].max() for i in range(n)]) + 1e-9)
def show(lab, x):
    print("%-26s attack(5ms) %s" % (lab, " ".join("%5.1f" % v for v in env(x, 0.0, 0.06, 0.005))))
    print("%-26s tail (50ms) %s" % ("", " ".join("%5.1f" % v for v in env(x, 0.10, 0.80, 0.05))), flush=True)
show("none", serum.render(base, [48], 1.5, 0.12))
show("none again", serum.render(base, [48], 1.5, 0.12))
for lab, pp in {"Blur 99.21 (Envoker)": {"kParamBlur": 99.21}, "Blur 0, Wet 100": {"kParamWet": 100.0}}.items():
    show(lab, serum.render(P.fx_tree(base, "Bode", pp, 10), [48], 1.5, 0.12))
os._exit(0)
