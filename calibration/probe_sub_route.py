#!/usr/bin/env python3
"""The Sub oscillator's volume in Serum: static levels and an envelope route from volume 0 (does the Sub follow the same knob law as the noise slot and the WT
oscillators: static linear in amplitude, a route of depth a gives gain a^2?). Output: calibration/probe_sub_route.json."""
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import probe_noise_color as N
import probe_noise_route as R
if __name__ == "__main__":
    serum, base = N.start()
    base["Oscillator3"]["plainParams"] = {"kParamEnable": 0.0}
    base["Oscillator0"]["plainParams"] = {"kParamEnable": 0.0}; base["Oscillator1"]["plainParams"] = {"kParamEnable": 0.0}
    for i in range(4):
        base[f"Env{i}"] = {"plainParams": {"kParamAttack": 0.0005, "kParamDecay": 1.0, "kParamSustain": 1.0, "kParamRelease": 0.05}}
    res = {"static": {}, "route": {}}
    for v in (0.25, 0.5, 0.75, 1.0):
        b = copy.deepcopy(base); b["Oscillator4"] = {"plainParams": {"kParamEnable": 1.0, "kParamVolume": v}}
        res["static"][v] = round(float(R.level(serum, b)), 2); print("sub static %.2f %.2f dB" % (v, res["static"][v]), flush=True)
    for amt in (25, 50, 100):
        b = copy.deepcopy(base); b["Oscillator4"] = {"plainParams": {"kParamEnable": 1.0, "kParamVolume": 0.0}}
        b["ModSlot0"] = {"destModuleID": 4, "destModuleParamID": 1, "destModuleParamName": "kParamVolume", "destModuleTypeString": "Oscillator",
                         "plainParams": {"kParamAmount": float(amt)}, "source": [2, 0]}
        res["route"][amt] = round(float(R.level(serum, b)), 2); print("sub route %d %.2f dB" % (amt, res["route"][amt]), flush=True)
    Path("calibration/probe_sub_route.json").write_text(json.dumps(res, indent=1))
    sys.stdout.flush(); os._exit(0)
