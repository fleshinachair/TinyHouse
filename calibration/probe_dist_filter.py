#!/usr/bin/env python3
"""The Distortion effect's own filter (Freq, BW, LPHP, PrePost): noise at a low level through Distortion at Drive 0 and Wet 100 with the filter knobs set,
transfer in dB at half-octave points.  Output: printed."""
import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import fx_probe as P
from probe_fxfilter_drive import GRID2, tf2

serum, base = P.start()
b = copy.deepcopy(base)
b["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.1}
dry = P.render(serum, b)
print("grid", GRID2)
cases = []
for lphp in (0.0, 25.0, 50.0, 75.0, 100.0):
    cases.append((f"post f.5 bw1 lphp {lphp:.0f}", {"kParamDrive": 0.0, "kParamFreq": 0.5, "kParamBW": 1.0, "kParamPrePost": 2.0, "kParamLPHP": lphp}))
for bw in (0.3, 1.0, 2.0, 3.0):
    cases.append((f"post f.5 bw{bw} lphp default", {"kParamDrive": 0.0, "kParamFreq": 0.5, "kParamBW": bw, "kParamPrePost": 2.0}))
cases.append(("post f.5 bw unwritten", {"kParamDrive": 0.0, "kParamFreq": 0.5, "kParamPrePost": 2.0}))
cases.append(("post, nothing else", {"kParamDrive": 0.0, "kParamPrePost": 2.0}))
for name, pp in cases:
    pp = dict(pp); pp.setdefault("kParamWet", 100.0)
    g = tf2(dry, serum.render(P.fx_tree(b, "Distortion", pp, 0), [48], 2.0, 1.9))
    print(name.ljust(26), " ".join("%6.1f" % x for x in g), flush=True)
os._exit(0)
