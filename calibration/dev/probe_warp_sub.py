#!/usr/bin/env python3
"""Serum's oscillator warps that take the Sub oscillator as the modulator (FM from Sub, RM from Sub, PD from Sub; the Sub's own level 0 so only the warp is heard): a sine oscillator
at the note, the Sub a sine at the same pitch (octave 0) or one octave down (-1), harmonics 1..12 (dB, absolute) at amounts 0..1, and the fundamental's level change.
Output: calibration/probe_warp_sub.json {mode: {octave: {amount: [dB 1..12]}}, "ref": [...]}.  Usage: probe_warp_sub.py [mode ...]"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import probe_vfilter as V
import test_render_calibration as H
V.NH = 12
_, bt = H.load_preset("Keys - Loop")
t0 = H.init_tree(bt, table="Analog/Basic Shapes.wav", frames=7)
pool = V.SerumPool()
MODES = sys.argv[1:] or ["kFM_SUB", "kRM_SUB", "kPD_SUB"]
AMTS = [0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0]
def render(mode, a, octave=0.0):
    t = copy.deepcopy(t0)
    pp = {"kParamTablePos": 1.0}
    if mode:
        pp["kParamWarpMenu"] = mode; pp["kParamWarp"] = a
    t["Oscillator0"]["WTOsc0"]["plainParams"] = pp
    t["Oscillator4"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.0, "kParamOctave": octave}
    return V.harmonics(pool.render(t))
out = {"ref": render(None, 0).tolist(), "modes": {}}
print("ref " + " ".join("%6.1f" % v for v in out["ref"]), flush=True)
for md in MODES:
    out["modes"][md] = {}
    for octv in (0.0, -1.0):
        out["modes"][md][str(octv)] = {}
        for a in AMTS:
            h = render(md, a, octv)
            out["modes"][md][str(octv)][str(a)] = h.tolist()
            print("%-8s oct %+.0f  %.1f  " % (md[1:], octv, a) + " ".join("%6.1f" % v for v in h), flush=True)
Path("calibration/probe_warp_sub.json").write_text(json.dumps(out, indent=1))
sys.stdout.flush(); os._exit(0)
