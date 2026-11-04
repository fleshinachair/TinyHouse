#!/usr/bin/env python3
"""The H24 voice filter at low cutoffs: noise through Serum's H24 (key tracking off) at cutoff 0.10 .. 0.55, and the engine's HP 24 (flt_type 3) cutoff pot whose band levels
are closest. The converter's delta table (FILTER_CUTOFF_DELTA) was measured from 0.35 up; this is what it should be below. Output: printed, delta = pot - the closed-form law."""
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import fxmap
import test_render_calibration as H
import deephouse2move as d2m
SR = H.SR
EDGES = [40, 80, 160, 320, 640, 1250, 2500, 5000, 10000]
def bands(x):
    m = x.mean(axis=0)[int(0.6 * SR):int(1.8 * SR)]
    s = np.abs(np.fft.rfft(m * np.hanning(len(m)))) ** 2
    f = np.fft.rfftfreq(len(m), 1 / SR)
    return np.array([10 * np.log10(s[(f >= a) & (f < b)].sum() + 1e-20) for a, b in zip(EDGES[:-1], EDGES[1:])])
model = sys.argv[1] if len(sys.argv) > 1 else "H24"
etype = {"H24": 3, "H12": 2, "B24": 5, "B12": 4, "MgL24": 1, "L24": 1, "MgL12": 0, "L12": 0, "MgL18": 1}.get(model, 3)
serum, base = P.start()
def serum_bands(c):
    b = copy.deepcopy(base)
    b["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.75}
    b["RoutingSlot3"] = {"plainParams": {"kParamRoutingDest": "kRoutingDestFilter"}}
    b["VoiceFilter0"] = {"plainParams": {"kParamEnable": 1.0, "kParamFreq": c, "kParamType": model, "kParamKeyTrack": 0.0}}
    return bands(serum.render(b, [48], 2.0, 1.9))
print("model", model, "engine type", etype)
for c in ((0.30, 0.40, 0.50, 0.60, 0.70, 0.80) if model[0] in 'HB' else (0.50, 0.60, 0.70, 0.80, 0.90)):
    sb = serum_bands(c)
    best = None
    for pot in range(0, 100):
        st = fxmap.noise_patch({"noise_type": 2, "flt_type": etype, "flt_freq": pot, "flt_res": 0, "vca_a": 0, "vca_s": 127, "flt_env": 64, "volume": 100})
        eb = bands(H.run_cli(st, [48], 2.0, 1.9))
        sb2, eb2 = np.maximum(sb - sb.max(), -60), np.maximum(eb - eb.max(), -60)
        err = float(np.sqrt(np.mean((sb2 - eb2) ** 2)))
        if best is None or err < best[0]: best = (err, pot)
    law = c * d2m.CUTOFF_NOTES * d2m.CAL["cutoff_scale"] + d2m.CAL["cutoff_offset"] - 8
    print("cutoff %.2f: engine pot %2d (err %.1f dB)  closed form %.1f  delta %+.1f   current table delta %+.1f" % (c, best[1], best[0], law, best[1] - law, d2m.interp([tuple(x) for x in d2m.FILTER_CUTOFF_DELTA.get(model, [(0.0, 0.0)])], c)), flush=True)
os._exit(0)
