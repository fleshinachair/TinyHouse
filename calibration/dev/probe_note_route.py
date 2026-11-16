#!/usr/bin/env python3
"""What a main-source route from the NOTE NUMBER (source 17) does to the voice filter cutoff, unipolar and bipolar: noise through a low-pass whose base cutoff is swept
(no route) to get level against cutoff, then the route at +50 % with the note at 36 / 48 / 60 / 72 / 84, read back as the cutoff offset (fraction of the knob). Output: printed."""
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
def level(x): return 10 * np.log10((x.mean(axis=0)[int(0.6 * SR):int(1.8 * SR)] ** 2).mean() + 1e-20)
def render(freq, note=48, route=None):
    b = copy.deepcopy(base)
    b["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.75}
    b["RoutingSlot3"] = {"plainParams": {"kParamRoutingDest": "kRoutingDestFilter"}}
    b["VoiceFilter0"] = {"plainParams": {"kParamEnable": 1.0, "kParamFreq": freq, "kParamType": "MgL24"}}
    if route:
        s = copy.deepcopy(slot); s["source"] = [17, 0]; s["plainParams"] = {"kParamAmount": route[0], "kParamBipolar": 1.0 if route[1] else 0.0}
        b["ModSlot0"] = s
    return level(serum.render(b, [note], 2.0, 1.9))
grid = np.arange(0.2, 0.95, 0.05)
curve = [render(f) for f in grid]
print("level vs cutoff:", " ".join("%.2f:%.1f" % (f, c) for f, c in zip(grid, curve)), flush=True)
def offset(lv, f0): return float(np.interp(lv, curve, grid) - f0)       # level rises with the cutoff: curve is increasing
for bip in (False, True):
    print("bipolar" if bip else "unipolar", "route +50 %, base 0.5:", end=" ")
    for note in (36, 48, 60, 72, 84):
        lv = render(0.5, note, (50.0, bip))
        print("note %d: offset %+.3f" % (note, offset(lv, 0.5)), end="   ")
    print(flush=True)
os._exit(0)
