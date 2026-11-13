#!/usr/bin/env python3
"""Serum's Split crossover: noise through Split (Freq) with a muted Distortion (Wet 100, LevelOut 0) in one branch, so the other branch's band is all that is left.
Branch 2 muted -> the low band's transfer; branch 1 muted -> the high band's. Transfer in dB at half-octave points. Output: printed."""
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import fxmap
import fx_probe as P
from probe_fxfilter_drive import GRID2, tf2
from serum2_preset_loader.codec import decode_preset_file
import serum_paths
f = next((serum_paths.SERUM_ROOT / "Presets" / "Factory").rglob("BA - Fonk.SerumPreset"))
_, tree = decode_preset_file(f)
split, dist = copy.deepcopy(tree["FXRack0"]["FX"][0]), copy.deepcopy(tree["FXRack0"]["FX"][1])
serum, base = P.start()
b = P.noise_base(base)
b["Oscillator3"]["plainParams"]["kParamVolume"] = 0.1
dry = P.render(serum, b)
print("grid", GRID2)
dist["FXDistortion"]["plainParams"] = {"kParamDrive": 0.0, "kParamWet": 100.0, "kParamLevelOut": 0.0}
for fq in (250.0, 1000.0):
    for mute_branch in (2, 1):
        s = copy.deepcopy(split); pp = s["FXSplit"]["plainParams"]
        for k in ("kParamModuleCount1", "kParamModuleCount2"): pp.pop(k, None)
        pp["kParamFreq"] = fq; pp["kParamModuleCount%d" % mute_branch] = 1.0
        t = copy.deepcopy(b); t["FXRack0"]["FX"] = [s, copy.deepcopy(dist)]
        g = tf2(dry, serum.render(t, [48], 2.0, 1.9))
        print(f"freq {fq:6.0f} branch {mute_branch} muted ({'low' if mute_branch == 2 else 'high'} band left)".ljust(46), " ".join("%6.1f" % x for x in g), flush=True)
os._exit(0)
