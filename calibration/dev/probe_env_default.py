#!/usr/bin/env python3
"""What an Env2..Env4 is when its parameters are not written: noise through a closed voice filter (MgL24 at 0) with Env n routed to the cutoff at +50 %,
the envelope left unwritten, and the same with Sustain written as 1.0 and 0.0. The level of the noise after the attack and decay shows where the sustain is.
Output: printed (noise level in dBFS at 0.05, 0.3, 1.0 and 2.0 s)."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file

SR = H.SR
serum, base = P.start()
_, mind = H.load_preset("Pad - MIND")
slot = next(copy.deepcopy(mind[f"ModSlot{i}"]) for i in range(64) if mind[f"ModSlot{i}"].get("destModuleTypeString") == "VoiceFilter" and mind[f"ModSlot{i}"].get("destModuleID") == 0
            and mind[f"ModSlot{i}"].get("destModuleParamName") == "kParamFreq")
print("slot template:", {k: v for k, v in slot.items() if k != "plainParams"})
def level(x, t0): return 10 * np.log10((x[:, int(t0 * SR):int((t0 + 0.1) * SR)] ** 2).mean() + 1e-20)
for env in (0, 1, 2, 3):
    for label, ep in (("unwritten", "default"), ("sustain 1", {"kParamSustain": 1.0, "kParamDecay": 0.3}), ("sustain 0", {"kParamSustain": 0.0, "kParamDecay": 0.3})):
        b = copy.deepcopy(base)
        b["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.75}
        b["RoutingSlot3"] = {"plainParams": {"kParamRoutingDest": "kRoutingDestFilter"}}
        b["VoiceFilter0"] = {"plainParams": {"kParamEnable": 1.0, "kParamFreq": 0.1, "kParamType": "MgL24"}}
        b[f"Env{env}"]["plainParams"] = ep
        s = copy.deepcopy(slot); s["source"] = [env + 2, 0]; s["plainParams"] = {"kParamAmount": 80.0}
        b["ModSlot0"] = s
        x = serum.render(b, [48], 3.0, 2.5)
        print("Env%d %-10s" % (env + 1, label), " ".join("%7.1f" % level(x, t) for t in (0.05, 0.3, 1.0, 2.0)), flush=True)
os._exit(0)
