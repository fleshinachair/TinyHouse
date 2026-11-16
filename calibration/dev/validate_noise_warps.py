#!/usr/bin/env python3
"""The warps driven by the Noise against Serum through the real converter and engine: a held Basic Shapes sine through FM_NOISE / PD_NOISE / RM_NOISE at an amount, REPS renders each side.
Per case: the carrier line's share (dB against the unwarped note), the total power (dB), and the power in six bands (carrier-relative: 0.5-0.9, 0.9-1.1 (without the line), 1.1-2, 2-4, 4-8, 8-16 times
the carrier) as dB of the total.  Serum and engine side by side.
Usage: validate_noise_warps.py [mode:amount ...]   (needs a built calibration/tablor_cli.exe)"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import warpwave as W
import numpy as np
import probe_vfilter as V
import test_render_calibration as H
SR = H.SR; REPS = int(os.environ.get("REPS", "3")); NOTE = 48; fc = 440 * 2 ** ((NOTE - 69) / 12)
CASES = [("kFM_NOISE", 0.5), ("kFM_NOISE", 0.6), ("kFM_NOISE", 0.7), ("kFM_NOISE", 0.9), ("kPD_NOISE", 0.75), ("kPD_NOISE", 0.9), ("kPD_NOISE", 1.0), ("kRM_NOISE", 0.6), ("kRM_NOISE", 0.9)]
if len(sys.argv) > 1:
    CASES = [(a.split(":")[0], float(a.split(":")[1])) for a in sys.argv[1:]]
V.NOTE = NOTE
t0 = W.tree("Analog/Basic Shapes.wav", 7)


def tree_for(mode, amt):
    t = copy.deepcopy(t0)
    t["Oscillator0"]["WTOsc0"]["plainParams"] = {"kParamTablePos": 1.0, "kParamRandomPhase": 0.0, **({"kParamWarpMenu": mode, "kParamWarp": amt} if mode else {})}
    t["Oscillator0"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.75, "kParamOctave": 0.0, "kParamCoarsePit": 0.0}
    t["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.0}
    return t


def stats(x):
    m = x[0, int(0.35 * SR):int(1.2 * SR)]
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)), 1 << 16)) ** 2
    f = np.fft.rfftfreq(1 << 16, 1 / SR); df = f[1]
    line = (f > fc - 4 * df) & (f < fc + 4 * df)
    car = sp[line].sum(); tot = sp.sum()
    edges = [0.5, 0.9, 1.1, 2, 4, 8, 16]
    bands = [sp[(f >= fc * edges[i]) & (f < fc * edges[i + 1]) & ~line].sum() for i in range(len(edges) - 1)]
    return np.array([car, tot] + bands)


def side(render, mode, amt):
    return np.mean([stats(render(tree_for(mode, amt))) for _ in range(REPS)], axis=0)


ser = lambda t: W.pool.render(t)
eng = lambda t: H.tablor_render(t, [NOTE], V.DUR, V.HOLD)[0]
s0 = side(ser, None, 0); e0 = side(eng, None, 0)
db = lambda v: 10 * np.log10(np.maximum(v, 1e-30))
for mode, amt in CASES:
    s = side(ser, mode, amt); e = side(eng, mode, amt)
    row = lambda v, v0: "carrier %+6.1f total %+5.1f | bands dB of total: %s" % (db(v[0] / v0[0]), db(v[1] / v0[1]), " ".join("%6.1f" % db(b / v[1]) for b in v[2:]))
    print("%-10s %.2f  Serum  %s" % (mode[1:], amt, row(s, s0)))
    print("%-10s %.2f  engine %s" % ("", amt, row(e, e0)), flush=True)
sys.stdout.flush(); os._exit(0)
