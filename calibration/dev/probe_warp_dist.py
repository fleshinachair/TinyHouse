#!/usr/bin/env python3
"""Serum's waveshaper warps (Dist Tube / SoftSat / TapeSat / Diode 1 / Diode 2 / StompBox) on a pure sine oscillator: the harmonics 1..12 (dB, absolute) at amounts 0..1,
read from a held note. The warp is applied to the oscillator's waveform, so with one voice it is a static shaper of the sine; the amplitude of the fundamental against the
unwarped sine shows its gain law and the harmonics its curve.  Output: calibration/probe_warp_dist.json {mode: {amount: [dB 1..12]}, "ref": [dB]}.  Usage: probe_warp_dist.py [mode ...]"""
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
MODES = sys.argv[1:] or ["kDistTube", "kDistSoftSat", "kDistTapeSat", "kDistDiode1", "kDistDiode2", "kDistStompBox"]
AMTS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
def render(mode, a, vol=None):
    t = copy.deepcopy(t0)
    pp = {"kParamTablePos": 1.0}
    if mode:
        pp["kParamWarpMenu"] = mode; pp["kParamWarp"] = a
    t["Oscillator0"]["WTOsc0"]["plainParams"] = pp
    if vol is not None:
        t["Oscillator0"]["plainParams"] = {"kParamVolume": vol}
    return V.harmonics(pool.render(t))
out = {"ref": render(None, 0).tolist(), "modes": {}}
print("ref (no warp)      " + " ".join("%6.1f" % v for v in out["ref"]), flush=True)
for md in MODES:
    out["modes"][md] = {}
    for a in AMTS:
        h = render(md, a)
        out["modes"][md][str(a)] = h.tolist()
        print("%-12s %.1f  " % (md[1:], a) + " ".join("%6.1f" % v for v in h), flush=True)
Path("calibration/probe_warp_dist.json").write_text(json.dumps(out, indent=1))
sys.stdout.flush(); os._exit(0)
