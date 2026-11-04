#!/usr/bin/env python3
"""Serum's PWM warp against the engine's Bend: a saw (Default Shapes frame 0) with the PWM warp at several amounts, the harmonic series 2..9 against the fundamental,
and the engine's wt1_bend pot (0..127, both sides of 64) whose series is closest. Output: printed; the best pot per amount and its error."""
import copy, os, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import probe_vfilter as V
import test_render_calibration as H
d2m = H.d2m
V.NH = 12
_, bt = H.load_preset("Keys - Loop")
TABLE = sys.argv[1] if len(sys.argv) > 1 else "S2 Tables/Default Shapes.wav"
MODES = sys.argv[2].split(",") if len(sys.argv) > 2 else ["kPWM", "kBendPos", "kBendNeg"]
t0 = H.init_tree(bt, table=TABLE, frames=9)
pool = V.SerumPool()
def setk(st, k, v): return re.sub(r"(?<![a-z_0-9])%s=[^;]*;" % k, "%s=%s;" % (k, v), st)
sel = list(range(1, 9))
x0, m = H.tablor_render(t0, [V.NOTE], 0.3, 0.2)
st = d2m.blob(m)
def serum_h(mode, a):
    t = copy.deepcopy(t0)
    t["Oscillator0"]["WTOsc0"]["plainParams"] = {"kParamTablePos": 1.0, "kParamWarp": a, "kParamWarpMenu": mode}
    h = V.harmonics(pool.render(t)); return h - h[0]
print("saw, harmonics 2..9 re fundamental")
print("no warp       ", " ".join("%6.1f" % serum_h("kNone", 0.0)[i] for i in sel))
for mode in MODES:
    for a in (0.1, 0.2, 0.3, 0.4, 0.6, 0.8):
        hs = serum_h(mode, a)
        best = None
        for pot in range(0, 128, 2):
            he = V.harmonics(H.run_cli(setk(st, "wt1_bend", pot), [V.NOTE], V.DUR, V.HOLD)); he = he - he[0]
            err = float(np.sqrt(np.mean((np.maximum(he[sel], -60) - np.maximum(hs[sel], -60)) ** 2)))
            if best is None or err < best[0]: best = (err, pot)
        print("%-9s %.1f " % (mode, a), " ".join("%6.1f" % hs[i] for i in sel), "| best engine bend pot %3d (err %.1f dB)" % (best[1], best[0]), flush=True)
os._exit(0)
