#!/usr/bin/env python3
"""The static warps against Serum through the real converter and engine: for each warp and amount a held Default Shapes saw (or the table named) goes through Serum and through
tablor_cli; the harmonics 1..12 of each, in dB against that side's own unwarped note, must agree (rows print the largest difference among harmonics above -40 dB).
Usage: validate_static_warps.py   (needs a built calibration/tablor_cli.exe: bash schwung-tablor/scripts/zig-build.sh native)"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import warpwave as W
import numpy as np
import probe_vfilter as V
import test_render_calibration as H
V.NH = 12
DUR, HOLD = V.DUR, V.HOLD
CASES = [("kSelfPD", a) for a in (0.1, 0.17, 0.2, 0.35, 0.5)] if os.environ.get("CASES") == "selfpd" else [("kASYMPosNeg", a) for a in (0.553, 0.566, 0.35)] + [("kASYMNeg", 0.559), ("kASYMPos", 0.274)] if os.environ.get("CASES") == "asym" else [("kDistSoftClip", a) for a in (0.0, 0.3, 0.6, 1.0)] + [("kDistDiode2", a) for a in (0.1, 0.5, 1.0)] + [("kDistAsym", a) for a in (0.2, 0.5, 0.9)] \
      + [("kDistRectify", a) for a in (0.3, 0.8)] + [("kDistZeroSquare", a) for a in (0.1, 1.0)] + [("kDistSineShaper", a) for a in (0.1, 0.4)] \
      + [("kQuantize", a) for a in (0.4, 0.5)] + [("kEvenOdd", a) for a in (0.0, 0.25, 0.8, 1.0)]
TABLE = ("Analog/Basic Shapes.wav", 7) if os.environ.get("TABLE") == "sine" else ("S2 Tables/Default Shapes.wav", 9)


def tree_for(mode, amt):
    t = copy.deepcopy(W.tree(*TABLE))
    pp = {"kParamTablePos": 0.0, "kParamRandomPhase": 0.0}
    if mode:
        pp["kParamWarpMenu"] = mode; pp["kParamWarp"] = amt
    t["Oscillator0"]["WTOsc0"]["plainParams"] = pp
    return t


def both(mode, amt):
    t = tree_for(mode, amt)
    try:
        s = V.harmonics(W.pool.render(t))
    except RuntimeError:
        s = np.full(12, -120.0)
    e = V.harmonics(H.tablor_render(t, [48], DUR, HOLD)[0])
    return s, e


s0, e0 = both(None, 0.0)
print("unwarped fundamental: serum %.1f dB, engine %.1f dB" % (s0[0], e0[0]), flush=True)
worst = 0.0
for mode, amt in CASES:
    s, e = both(mode, amt)
    ds, de = s - s0[0], e - e0[0]                   # against each side's own unwarped fundamental
    use = ds > -40
    dif = np.abs(ds - de)[use]
    worst = max(worst, float(dif.max()) if dif.size else 0.0)
    print("%-16s %.2f  max |engine - serum| over %2d harmonics above -40 dB: %5.2f dB   h1 %+.1f/%+.1f  h2 %+.1f/%+.1f  h3 %+.1f/%+.1f" %
          (mode[1:], amt, use.sum(), dif.max() if dif.size else 0.0, ds[0], de[0], ds[1], de[1], ds[2], de[2]), flush=True)
print("worst", round(worst, 2))
sys.stdout.flush(); os._exit(0)
