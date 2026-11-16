#!/usr/bin/env python3
"""An envelope route into the noise slot's volume in Serum: level of White noise held by a sustained envelope at route amounts 0..100 (oscillator volume 0),
against the same noise at a static oscillator volume. Tells whether a route of amount a behaves like a knob at a (additive in the knob domain) and what the
knob law is. Output: calibration/probe_noise_route.json."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import probe_noise_color as N
SR = N.SR
def level(serum, b):
    xs = [serum.render(b, [48], 2.5, 2.4) for _ in range(2)]
    m = [x.mean(axis=0)[int(0.5 * SR):int(2.0 * SR)] for x in xs]
    return 10 * np.log10(np.mean([(v ** 2).mean() for v in m]) + 1e-20)
if __name__ == "__main__":
    serum, base = N.start()
    base["Oscillator3"]["NoiseOsc3"] = {"plainParams": {"kParamNoiseType": "White"}}
    for i in range(4):
        base[f"Env{i}"] = {"plainParams": {"kParamAttack": 0.0005, "kParamDecay": 1.0, "kParamSustain": 1.0, "kParamRelease": 0.05}}
    res = json.load(open("calibration/probe_noise_route.json")) if os.path.exists("calibration/probe_noise_route.json") else {"static": {}, "route": {}}
    for v in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.75, 0.9, 1.0):
        b = copy.deepcopy(base); b["Oscillator3"]["plainParams"]["kParamVolume"] = v
        res["static"][v] = round(float(level(serum, b)), 2); print("static vol %.2f  %.2f dB" % (v, res["static"][v]), flush=True)
    res["mixed"] = {}
    for v in (0.25, 0.5):
        for amt in (25, 50, -25, -50):
            b = copy.deepcopy(base); b["Oscillator3"]["plainParams"]["kParamVolume"] = v
            b["ModSlot0"] = {"destModuleID": 3, "destModuleParamID": 1, "destModuleParamName": "kParamVolume", "destModuleTypeString": "Oscillator",
                             "plainParams": {"kParamAmount": float(amt)}, "source": [2, 0]}
            try: lv = round(float(level(serum, b)), 2)
            except RuntimeError: lv = None; serum, _ = N.start()
            res["mixed"][f"{v}:{amt}"] = lv; print("mixed vol %.2f amount %3d  %s dB" % (v, amt, lv), flush=True)
    Path("calibration/probe_noise_route.json").write_text(json.dumps(res, indent=1))
    sys.stdout.flush(); os._exit(0)
