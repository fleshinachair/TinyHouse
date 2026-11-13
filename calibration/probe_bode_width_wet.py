#!/usr/bin/env python3
"""Serum's Bode: OutputWidth law (which channel gets which shift) and the Wet law (dry line vs shifted line level).  Shift 50 of Range 43.45 = a 21.7 Hz shift; saw on note 48 (130.8 Hz)."""
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration")); os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H
SR = H.SR; F0 = 440 * 2 ** ((48 - 69) / 12)
serum, base = P.start()
base = copy.deepcopy(base); base["Oscillator0"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.6}
S = 21.725
def spec(x, ch):
    m = x[ch][int(0.5 * SR):int(1.9 * SR)]; n = 1 << 18
    return np.fft.rfftfreq(n, 1 / SR), np.abs(np.fft.rfft(m * np.hanning(len(m)), n))
def amp(x, ch, f0, w=3.0):
    f, s = spec(x, ch); k = (f > f0 - w) & (f < f0 + w); return 20 * np.log10(s[k].max() + 1e-12)
def r(pp):
    return serum.render(P.fx_tree(base, "Bode", pp, 10), [48], 2.0, 1.9)
dry = serum.render(base, [48], 2.0, 1.9)
d0 = amp(dry, 0, F0)
print("dry line (L) %.1f dB, (R) %.1f dB" % (d0, amp(dry, 1, F0)))
print("-- OutputWidth, Shift 50, Range 43.45, Wet 100: level at F0 -S / F0 / F0 +S for L and R (dB re dry line)")
for w in (0, 25, 50, 75, 100):
    x = r({"kParamWet": 100.0, "kParamShift": 50.0, "kParamRange": 43.45, "kParamOutputWidth": float(w)})
    print("width %3d  L: %s   R: %s" % (w, " ".join("%+6.1f" % (amp(x, 0, F0 + o) - d0) for o in (-S, 0, S)), " ".join("%+6.1f" % (amp(x, 1, F0 + o) - d0) for o in (-S, 0, S))), flush=True)
x = r({"kParamWet": 100.0, "kParamShift": 50.0, "kParamRange": 43.45})
print("width unstored  L: %s   R: %s" % (" ".join("%+6.1f" % (amp(x, 0, F0 + o) - d0) for o in (-S, 0, S)), " ".join("%+6.1f" % (amp(x, 1, F0 + o) - d0) for o in (-S, 0, S))))
print("-- Wet (width unstored), L channel (shifted line is F0 -S): dry line / shifted line, dB re dry")
for wv in (5, 10, 25, 50, 75, 100):
    x = r({"kParamWet": float(wv), "kParamShift": 50.0, "kParamRange": 43.45})
    print("wet %3d  dry line %+6.1f   shifted line %+6.1f" % (wv, amp(x, 0, F0) - d0, amp(x, 0, F0 - S) - d0), flush=True)
os._exit(0)
