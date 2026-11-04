#!/usr/bin/env python3
"""A macro route to the voice filter cutoff, unipolar and bipolar, at macro values 0 / 25 / 50 / 75 / 100 %: the cutoff offset (fraction of the knob) read back from the noise
level against a swept base cutoff, route +50 %. Tells whether a bipolar route is x - 0.5 or 2x - 1. Output: printed."""
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H
SR = H.SR
serum, base = P.start()
_, mind = H.load_preset("Pad - MIND")
slot = next(copy.deepcopy(mind[f"ModSlot{i}"]) for i in range(64) if mind[f"ModSlot{i}"].get("destModuleTypeString") == "VoiceFilter" and mind[f"ModSlot{i}"].get("destModuleID") == 0
            and mind[f"ModSlot{i}"].get("destModuleParamName") == "kParamFreq")
print("macro0 template:", mind.get("Macro0"))
def level(x): return 10 * np.log10((x.mean(axis=0)[int(0.6 * SR):int(1.8 * SR)] ** 2).mean() + 1e-20)
def render(freq, route=None, macro=0.0):
    b = copy.deepcopy(base)
    b["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.75}
    b["RoutingSlot3"] = {"plainParams": {"kParamRoutingDest": "kRoutingDestFilter"}}
    b["VoiceFilter0"] = {"plainParams": {"kParamEnable": 1.0, "kParamFreq": freq, "kParamType": "MgL24"}}
    if route:
        s = copy.deepcopy(slot); s["source"] = [25, 0]; s["plainParams"] = {"kParamAmount": route[0], "kParamBipolar": 1.0 if route[1] else 0.0}
        b["ModSlot0"] = s
        b["Macro0"] = copy.deepcopy(mind["Macro0"]); b["Macro0"]["plainParams"] = dict(b["Macro0"].get("plainParams") if isinstance(b["Macro0"].get("plainParams"), dict) else {}, kParamValue=macro)
    return level(serum.render(b, [48], 2.0, 1.9))
grid = np.arange(0.2, 0.95, 0.05)
curve = [render(f) for f in grid]
def offset(lv, f0): return float(np.interp(lv, curve, grid) - f0)
for bip in (False, True):
    print("bipolar" if bip else "unipolar", "macro route +50 %, base 0.5:", end=" ")
    for mv in (0.0, 25.0, 50.0, 75.0, 100.0):
        print("macro %3.0f: offset %+.3f" % (mv, offset(render(0.5, (50.0, bip), mv), 0.5)), end="   ")
    print(flush=True)
os._exit(0)
