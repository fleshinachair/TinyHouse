#!/usr/bin/env python3
"""Serum's Bode (frequency shifter): a sine through it at several Shift / Range / OutputMix settings, the strongest spectral lines per channel (Hz and dB re the strongest), to read off
the shift law in Hz, the sideband (up or down), the mix and the stereo split.  Output: printed table, calibration/probe_bode.json.  Usage: probe_bode.py [note]"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H
SR = H.SR
NOTE = int(sys.argv[1]) if len(sys.argv) > 1 else 57
serum, base = P.start()
base = copy.deepcopy(base)
def render(pp):
    t = H.init_tree(base, table="Analog/Basic Shapes.wav", frames=7)
    t["Oscillator0"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.5}
    t = P.fx_tree(t, "Bode", dict(pp, kParamWet=100.0), 10)
    return serum.render(t, [NOTE], 1.6, 1.5, vel=100)
def lines(x, k=5):
    x = x[int(0.4 * SR):int(1.4 * SR)]; n = 1 << 15; sp = np.abs(np.fft.rfft(x[:n] * np.hanning(n))); f = np.fft.rfftfreq(n, 1 / SR)
    pk = [i for i in range(2, len(sp) - 2) if sp[i] > sp[i - 1] and sp[i] >= sp[i + 1]]
    pk.sort(key=lambda i: -sp[i]); r = sp[pk[0]]
    return [[round(float(f[i]), 1), round(float(20 * np.log10(sp[i] / r)), 1)] for i in pk[:k] if sp[i] > r * 0.003], float(20 * np.log10(r + 1e-12))
out = {}
SET = sys.argv[2] if len(sys.argv) > 2 else "a"
if SET == "a":
    CASES = [("dry (no params)", {})]
    for sh in (-100, -50, -20, -5, 5, 20, 50, 100): CASES.append((f"Shift {sh}", {"kParamShift": float(sh), "kParamOutputMix": 100.0}))
    for rg in (1, 10, 50, 100): CASES.append((f"Shift 50 Range {rg}", {"kParamShift": 50.0, "kParamRange": float(rg), "kParamOutputMix": 100.0}))
else:
    CASES = []
    for mx in (-100, -50, -20, 0, 20, 50, 100):
        for wd in (0, 100):
            CASES.append((f"S100 R100 Mix {mx} Width {wd}", {"kParamShift": 100.0, "kParamRange": 100.0, "kParamOutputMix": float(mx), "kParamOutputWidth": float(wd)}))
    CASES.append(("S100 R100 no mix key", {"kParamShift": 100.0, "kParamRange": 100.0}))
    CASES.append(("S-100 R100 Mix 100 Width 100", {"kParamShift": -100.0, "kParamRange": 100.0, "kParamOutputMix": 100.0, "kParamOutputWidth": 100.0}))
for lab, pp in CASES:
    y = render(pp); L, lv = lines(y[0]); R, _ = lines(y[1])
    out[lab] = {"L": L, "R": R, "peak_db": lv}
    print("%-30s L %s | R %s" % (lab, " ".join("%.0f:%+.0f" % tuple(a) for a in L[:4]), " ".join("%.0f:%+.0f" % tuple(a) for a in R[:4])), flush=True)
Path(f"calibration/probe_bode_{SET}.json").write_text(json.dumps(out, indent=1))
sys.stdout.flush(); os._exit(0)
