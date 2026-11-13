#!/usr/bin/env python3
"""A Distortion in the Mid branch of a SplitMS against the same Distortion on the whole signal, a mono sine through both: does Serum drive the branch with M = (L + R) / 2 (the same
level as a mono input) or another scale? Harmonic levels (dB re the fundamental) and the total level at 2 x f0 .. 6 x f0, for a few drives. Output: printed."""
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file
SR = 44100
h, t = decode_preset_file(Path(os.environ["LOVELY"]))
ms, dist = copy.deepcopy(t["FXRack0"]["FX"][1]), copy.deepcopy(t["FXRack0"]["FX"][2])
serum, base = P.start()
b = copy.deepcopy(base)
b["Oscillator0"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.5}
b["Oscillator3"]["plainParams"] = {"kParamEnable": 0.0}
def harm(x, f0):
    m = x.mean(axis=0)[int(0.4 * SR):int(1.8 * SR)]
    s = np.abs(np.fft.rfft(m * np.hanning(len(m)))); f = np.fft.rfftfreq(len(m), 1 / SR)
    lv = lambda c: 20 * np.log10(s[(f > c * 0.97) & (f < c * 1.03)].max() + 1e-12)
    return [lv(k * f0) for k in range(1, 6)]
f0 = 440.0 * 2 ** ((48 - 69) / 12)
print("f0 %.1f Hz; harmonics 1..5 in dB, then rel. to fundamental" % f0)
for drive in (20.0, 50.0, 90.0):
    d = copy.deepcopy(dist); d["FXDistortion"]["plainParams"] = {"kParamDrive": drive, "kParamWet": 100.0}
    for lab, rack in (("whole", [d]), ("mid", [copy.deepcopy(ms), d]), ("side", [copy.deepcopy(ms), d])):
        r = copy.deepcopy(rack)
        if lab != "whole":
            pp = r[0]["FXSplitMS"]["plainParams"]; pp.pop("kParamModuleCount1", None); pp.pop("kParamModuleCount2", None)
            pp["kParamModuleCount%d" % (1 if lab == "mid" else 2)] = 1.0
        tr = copy.deepcopy(b); tr["FXRack0"]["FX"] = r
        x = serum.render(tr, [48], 2.0, 1.9)
        hh = harm(x, f0)
        print("drive %3.0f %-6s" % (drive, lab), " ".join("%6.1f" % (v - hh[0]) for v in hh), " | fund %.1f" % hh[0], flush=True)
os._exit(0)
