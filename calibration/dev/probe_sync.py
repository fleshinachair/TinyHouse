#!/usr/bin/env python3
"""Serum's Sync warp against the engine's: a saw (Default Shapes frame 0) with the Sync warp at amounts 0..1 (and unwritten), the harmonic series 2..13 against the
fundamental, and the engine's wt1_sync pot (0..127) whose series is closest (same measure as probe_bend.py). Output: printed."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, os, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import probe_vfilter as V
import test_render_calibration as H
d2m = H.d2m
V.NH = 20
_, bt = H.load_preset("Keys - Loop")
t0 = H.init_tree(bt, table="S2 Tables/Default Shapes.wav", frames=9)
pool = V.SerumPool()
def setk(st, k, v): return re.sub(r"(?<![a-z_0-9])%s=[^;]*;" % k, "%s=%s;" % (k, v), st)
sel = list(range(1, 14))
x0, m = H.tablor_render(t0, [V.NOTE], 0.3, 0.2)
st = d2m.blob(m)
def serum_h(a):
    t = copy.deepcopy(t0)
    pp = {"kParamTablePos": 1.0, "kParamWarpMenu": "kSync"}
    if a is not None:
        pp["kParamWarp"] = a
    t["Oscillator0"]["WTOsc0"]["plainParams"] = pp
    h = V.harmonics(pool.render(t)); return h - h[0]
print("saw + Sync, harmonics 2..14 re fundamental")
for a in (None, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
    hs = serum_h(a)
    best = None
    for pot in range(0, 128, 2):
        he = V.harmonics(H.run_cli(setk(st, "wt1_sync", pot), [V.NOTE], V.DUR, V.HOLD)); he = he - he[0]
        err = float(np.sqrt(np.mean((np.maximum(he[sel], -60) - np.maximum(hs[sel], -60)) ** 2)))
        if best is None or err < best[0]: best = (err, pot)
    print("sync %-5s " % ("unw." if a is None else "%.1f" % a), " ".join("%5.0f" % hs[i] for i in sel), "| engine sync pot %3d (err %.1f dB)" % (best[1], best[0]), flush=True)
os._exit(0)
