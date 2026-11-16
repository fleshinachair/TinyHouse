#!/usr/bin/env python3
"""Serum's FilterLPF / FilterHPF oscillator warps: a saw (Default Shapes frame 0) with the warp at amounts 0..1, each harmonic's level against the unwarped saw's
(the transfer, dB), at two notes (is the corner a harmonic number or a frequency?). Output: calibration/probe_filterwarp.json and a printout."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import probe_vfilter as V
import test_render_calibration as H
V.NH = 40
_, bt = H.load_preset("Keys - Loop")
t0 = H.init_tree(bt, table="S2 Tables/Default Shapes.wav", frames=9)
pool = V.SerumPool()
MODES = (sys.argv[1] if len(sys.argv) > 1 else "kFilterLPF,kFilterHPF").split(",")
NOTES = [int(x) for x in (sys.argv[2] if len(sys.argv) > 2 else "48,60").split(",")]
AMTS = [0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


def render(mode, a):
    t = copy.deepcopy(t0)
    pp = {"kParamTablePos": 1.0, "kParamWarpMenu": mode}
    if a is not None:
        pp["kParamWarp"] = a
    t["Oscillator0"]["WTOsc0"]["plainParams"] = pp
    h = V.harmonics(pool.render(t))
    return h


out = {}
for note in NOTES:
    V.NOTE = note
    V.F0 = 440 * 2 ** ((note - 69) / 12)
    base = render("kNone", 0.0)
    for mode in MODES:
        for a in AMTS:
            tr = render(mode, a) - base
            out[f"{mode}|{note}|{a}"] = [round(float(x), 2) for x in tr]
            print(f"{mode[7:]:4s} note {note} amt {a:4.2f}", " ".join("%5.0f" % x for x in tr[:24]), flush=True)
(ROOT / "calibration/probe_filterwarp.json").write_text(json.dumps(out))
os._exit(0)
