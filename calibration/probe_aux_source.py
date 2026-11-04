#!/usr/bin/env python3
"""What a route's second source (aux) does: noise through a closed voice filter with Env2 routed to the cutoff at +80 % (sustain 1), the aux source of that
route set to each of 16..24 (and none), the note played at velocity 20 / 70 / 127 (and at notes 36 / 60 / 84 for the key). The level of the noise at 1 s shows
how far the filter is open, so how much the aux scaled the route. Output: printed."""
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
def level(x, t0=1.0): return 10 * np.log10((x[:, int(t0 * SR):int((t0 + 0.2) * SR)] ** 2).mean() + 1e-20)
def run(aux, note=48, vel=100):
    b = copy.deepcopy(base)
    b["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.75}
    b["RoutingSlot3"] = {"plainParams": {"kParamRoutingDest": "kRoutingDestFilter"}}
    b["VoiceFilter0"] = {"plainParams": {"kParamEnable": 1.0, "kParamFreq": 0.1, "kParamType": "MgL24"}}
    b["Env1"]["plainParams"] = {"kParamSustain": 1.0, "kParamDecay": 0.3}
    s = copy.deepcopy(slot); s["source"] = [3, aux]; s["plainParams"] = {"kParamAmount": 80.0}
    b["ModSlot0"] = s
    return level(serum.render(b, [note], 2.0, 1.6, vel=vel))
print("closed filter, no route level:", end=" ")
b = copy.deepcopy(base)
for aux in (0, 16, 17, 18, 19, 20, 21, 22, 23, 24):
    print("aux %-2d  vel 20/70/127: %s   note 36/60/84 (vel 100): %s" % (aux, " ".join("%7.1f" % run(aux, 48, v) for v in (20, 70, 127)), " ".join("%7.1f" % run(aux, n, 100) for n in (36, 60, 84))), flush=True)
os._exit(0)
