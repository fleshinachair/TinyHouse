import os, sys, copy, re
from pathlib import Path
ROOT = Path("C:/Github/TinyHouse")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration")); os.chdir(ROOT)
import numpy as np
import probe_vfilter as V
import test_render_calibration as H
d2m = H.d2m
V.NOTE = 36; V.F0 = 440 * 2 ** ((36 - 69) / 12); V.NH = 8
_, bt = H.load_preset("Keys - Loop")
t0 = H.init_tree(bt, table="Analog/Basic Shapes.wav", frames=28)
t0["Oscillator0"]["WTOsc0"]["plainParams"] = {"kParamTablePos": 1.0}
pool = V.SerumPool()
def setk(st, k, v): return re.sub(r"(?<![a-z_0-9])%s=[^;]*;" % k, "%s=%s;" % (k, v), st)
_, m = H.tablor_render(H.with_filter(t0, 0.9, 0.0, "MgL24"), [V.NOTE], 0.3, 0.2)
st = d2m.blob(m); st = setk(st, "flt_freq", 127)
sel = [1, 2, 3, 4]
print("harmonics 2..5 re fundamental (note 36, sine through an open MgL24)")
for d in (0.0, 10.0, 25.0, 50.0, 75.0, 100.0):
    t = H.with_filter(t0, 0.9, 0.0, "MgL24"); t["VoiceFilter0"]["plainParams"]["kParamDrive"] = d
    h = V.harmonics(pool.render(t)); hs = h - h[0]
    best = None
    for pot in range(0, 128, 3):
        he = V.harmonics(H.run_cli(setk(st, "flt_drive", pot), [V.NOTE], V.DUR, V.HOLD)); he = he - he[0]
        err = float(np.sqrt(np.mean((np.maximum(he[sel], -70) - np.maximum(hs[sel], -70)) ** 2)))
        if best is None or err < best[0]: best = (err, pot, he)
    print("serum drive %5.1f%%: " % d, " ".join("%6.1f" % hs[i] for i in sel), "| best engine pot %3d (err %.1f)  current rule -> %d" % (best[1], best[0], round(d / 100 * 127)), " engine there:", " ".join("%6.1f" % best[2][i] for i in sel)); sys.stdout.flush()
os._exit(0)
