#!/usr/bin/env python3
"""Serum's Sub-driven FM / PD / RM oscillator warps read as sidebands: a sine oscillator at the note, the Sub a sine a perfect fifth up (so the carrier and the
modulator share no harmonics), the Sub's own level 0 (only the warp is heard). A phase modulation by a sine puts lines at fc + n fm with amplitude A J_n(beta), so
the index beta is read from the lines, per amount, at two notes (does the index follow the note?) and for another Sub shape.
Output: calibration/probe_warp_fm.json {mode|note|shape|amount: {line: amplitude re the unwarped carrier}}.  Usage: probe_warp_fm.py [mode,...] [note,...] [shape,...]"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import probe_vfilter as V
import test_render_calibration as H
SR = H.SR
_, bt = H.load_preset("Keys - Loop")
t0 = H.init_tree(bt, table="Analog/Basic Shapes.wav", frames=7)
pool = V.SerumPool()
MODES = (sys.argv[1] if len(sys.argv) > 1 else "kFM_SUB,kPD_SUB,kRM_SUB").split(",")
NOTES = [int(x) for x in (sys.argv[2] if len(sys.argv) > 2 else "48,60").split(",")]
SHAPES = (sys.argv[3] if len(sys.argv) > 3 else "kSine").split(",")
AMTS = [0.0, 0.1, 0.15, 0.2, 0.25, 0.3]
SUB_ST = float(os.environ.get("SUB_ST", "7"))
LINES = [("c", 0, 0)] + [(f"p{n}", n, 1) for n in (1, 2, 3, 4)] + [(f"m{n}", n, -1) for n in (1, 2, 3, 4)] + [("s", 0, 0)]


def render(mode, a, shape):
    t = copy.deepcopy(t0)
    pp = {"kParamTablePos": 1.0}
    if mode:
        pp["kParamWarpMenu"] = mode; pp["kParamWarp"] = a
    t["Oscillator0"]["WTOsc0"]["plainParams"] = pp
    t["Oscillator4"] = {"plainParams": {"kParamEnable": 1.0, "kParamVolume": 0.0, "kParamOctave": 0.0, "kParamCoarsePit": SUB_ST}, "SubOsc4": {"plainParams": {"kParamShape": shape}}}
    return pool.render(t)


def lines(x, fc):
    m = x[0, int(0.35 * SR):int(1.2 * SR)]
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)), 1 << 17)) / len(m) * 4
    f = np.fft.rfftfreq(1 << 17, 1 / SR)
    fm = fc * 2 ** (SUB_ST / 12)
    out = {}
    for name, n, sgn in LINES:
        if name == "s":
            fr = fm
        else:
            fr = abs(fc + sgn * n * fm)
        if fr > 18000 or fr < 20:
            out[name] = None; continue
        i = np.searchsorted(f, fr)
        out[name] = float(sp[max(i - 3, 0):i + 4].max())
    return out


res = {}
for note in NOTES:
    V.NOTE = note
    fc = 440 * 2 ** ((note - 69) / 12)
    ref = lines(render(None, 0.0, "kSine"), fc)["c"]
    res[f"ref|{note}"] = ref
    for shape in SHAPES:
        for mode in MODES:
            for a in AMTS:
                ln = lines(render(mode, a, shape), fc)
                res[f"{mode}|{note}|{shape}|{a}"] = {k: (None if v is None else round(v / ref, 5)) for k, v in ln.items()}
                print(f"{mode[1:]:7s} n{note} {shape[1:]:5s} a{a:4.2f} " + " ".join(f"{k}={'-' if v is None else '%.3f' % (v / ref)}" for k, v in ln.items()), flush=True)
Path(os.environ.get("OUT", "calibration/probe_warp_fm.json")).write_text(json.dumps(res, indent=1))
sys.stdout.flush(); os._exit(0)
