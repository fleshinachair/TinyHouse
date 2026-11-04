import os, sys, copy, re
from pathlib import Path
ROOT = Path("C:/Github/TinyHouse")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration")); os.chdir(ROOT)
import numpy as np
import probe_vfilter as V
import test_render_calibration as H
d2m = H.d2m
V.NH = 12
_, bt = H.load_preset("Keys - Loop")
t0 = H.init_tree(bt, table="Analog/Basic Shapes.wav", frames=28)
pool = V.SerumPool()
def setk(st, k, v): return re.sub(r"(?<![a-z_0-9])%s=[^;]*;" % k, "%s=%s;" % (k, v), st)
sel = list(range(1, 9))
x0, m = H.tablor_render(t0, [V.NOTE], 0.3, 0.2)
st = d2m.blob(m)
print("harmonics 2..9 re fundamental")
for a in (0.03, 0.06, 0.12, 0.2, 0.3, 0.4, 0.5, 0.7, 0.85, 1.0):
    t = copy.deepcopy(t0)
    t["Oscillator0"]["WTOsc0"]["plainParams"] = {"kParamTablePos": 1.0, "kParamWarp": a, "kParamWarpMenu": "kBendPos"}
    h = V.harmonics(pool.render(t)); hs = h - h[0]
    best = None
    for pot in (list(range(64, 92)) + list(range(92, 128, 2))):
        he = V.harmonics(H.run_cli(setk(st, "wt1_bend", pot), [V.NOTE], V.DUR, V.HOLD)); he = he - he[0]
        err = float(np.sqrt(np.mean((np.maximum(he[sel], -60) - np.maximum(hs[sel], -60)) ** 2)))
        if best is None or err < best[0]: best = (err, pot, he)
    print("serum amt %.2f  " % a, " ".join("%6.1f" % hs[i] for i in sel), "| best engine bend pot %d (err %.1f dB)  formula 64+63a = %d" % (best[1], best[0], round(64 + 63 * a)))
    sys.stdout.flush()
os._exit(0)
