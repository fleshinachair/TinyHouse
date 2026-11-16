#!/usr/bin/env python3
"""A route's aux curve (kParamAuxCurveData on, the points in flex[1]) and what the random aux sources (21, 22) are.  Noise through a closed MgL24 low-pass (cutoff 0.1)
with Env2 (sustain 1) routed to the cutoff at 60 %; the aux of that route is a macro (25) at 0 / .25 / .5 / .75 / 1 with an aux curve of various shapes, or aux 21 / 22
over several notes.  The route's effective scale s is read by inverting the same patch without an aux and with the amount scaled (level at 1 s against amount).
Output: printed.  Finding (Lead - Other Side, 2026-11-08): the aux curve's value is 1 - y (like every curve in Serum) and it replaces the straight x (scale = x without a curve)."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H
SR = H.SR
serum, base = P.start()
_, mind = H.load_preset("Pad - MIND")
slot = next(copy.deepcopy(mind[f"ModSlot{i}"]) for i in range(64) if mind[f"ModSlot{i}"].get("destModuleTypeString") == "VoiceFilter" and mind[f"ModSlot{i}"].get("destModuleID") == 0
            and mind[f"ModSlot{i}"].get("destModuleParamName") == "kParamFreq")
slot.pop("flex", None)
AMT = 60.0


def level(x, t0=1.0):
    seg = x[:, int(t0 * SR):int((t0 + 0.3) * SR)]
    sp = np.abs(np.fft.rfft(seg.mean(0) * np.hanning(seg.shape[1]))) ** 2
    f = np.fft.rfftfreq(seg.shape[1], 1 / SR)
    return 10 * np.log10(sp[(f > 1500) & (f < 8000)].sum() + 1e-20)


def render(aux=None, amount=AMT, curve=None, macro=None, note=60, vel=100):
    b = copy.deepcopy(base)
    b["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.75}
    b["Oscillator3"]["NoiseOsc3"] = {"plainParams": {"kParamNoiseType": "White"}}
    b["RoutingSlot3"] = {"plainParams": {"kParamRoutingDest": "kRoutingDestFilter"}}
    b["VoiceFilter0"] = {"plainParams": {"kParamEnable": 1.0, "kParamFreq": 0.1, "kParamType": "MgL24"}}
    b["Env1"]["plainParams"] = {"kParamSustain": 1.0, "kParamDecay": 0.3}
    s = copy.deepcopy(slot)
    s["source"] = [3, aux or 0]
    s["plainParams"] = {"kParamAmount": amount}
    if curve is not None:
        s["plainParams"]["kParamAuxCurveData"] = 1.0
        s["flex"] = [{}, {"curveVals": [0.5, 0.5], "numPoints": 1, "xVals": [0.0, 1.0], "yVals": list(curve)}]
    b["ModSlot0"] = s
    if macro is not None:
        b["Macro0"]["plainParams"] = {"kParamValue": 100.0 * macro}
    return level(serum.render(b, [note], 2.0, 1.6, vel=vel))


# calibration: level against the route's own scale (amount 60 x s)
SC = [i / 20 for i in range(0, 21)]
cal = [render(amount=AMT * s) for s in SC]
def scale_of(lv):
    return float(np.interp(lv, cal, SC)) if cal[0] < cal[-1] else float("nan")
print("calibration (scale: level)", " ".join("%.2f:%.1f" % (s, c) for s, c in zip(SC, cal)), flush=True)
print("macro aux (25), no aux curve:", " ".join("%.2f->%.2f" % (m, scale_of(render(25, macro=m))) for m in (0.0, 0.25, 0.5, 0.75, 1.0)), flush=True)
for curve in ((0.274, 0.166), (0.5, 0.5), (1.0, 0.0), (0.0, 1.0), (0.8, 0.2), (0.3, 0.9)):
    print("macro aux, curve y %s:" % (curve,), " ".join("%.2f->%.2f" % (m, scale_of(render(25, curve=curve, macro=m))) for m in (0.0, 0.25, 0.5, 0.75, 1.0)), flush=True)
for aux in (21, 22):
    r = [scale_of(render(aux, note=48 + k)) for k in range(24)]
    print("aux %d, no curve, 24 notes: scale mean %.2f min %.2f max %.2f" % (aux, np.nanmean(r), np.nanmin(r), np.nanmax(r)), " ".join("%.2f" % v for v in r), flush=True)
r = [scale_of(render(21, curve=(0.274, 0.166), note=48 + k)) for k in range(24)]
print("aux 21, curve (0.274, 0.166), 24 notes: mean %.2f min %.2f max %.2f" % (np.nanmean(r), np.nanmin(r), np.nanmax(r)), " ".join("%.2f" % v for v in r), flush=True)
for aux, what in ((16, "velocity"), (17, "note")):
    for curve in (None, (0.43, 0.0), (0.0, 1.0)):
        got = []
        for v in (20, 60, 100, 127):
            got.append(scale_of(render(aux, curve=curve, vel=v) if aux == 16 else render(aux, curve=curve, note=v)))
        pred = " ".join("%.2f" % (v / 127.0 if curve is None else (1 - curve[0]) + (curve[0] - curve[1]) * v / 127.0) for v in (20, 60, 100, 127))
        print("aux %d (%s), curve %s at 20/60/100/127: %s   predicted %s" % (aux, what, curve, " ".join("%.2f" % g for g in got), pred), flush=True)
os._exit(0)
