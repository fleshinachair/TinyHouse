#!/usr/bin/env python3
"""Serum's Bode (frequency shifter): where does a saw's first harmonics land for a stored Shift / Range, Wet 100.  Prints the offset (Hz) of the strongest spectral line near each of the first 3 harmonics
and the line's level, so Shift and Range can be read as a frequency law.
Result (2026-11-13): per channel, the left is shifted DOWN and the right UP (Shift > 0) by Shift/100 x Range Hz (Range is a linear Hz span: 10.68, 43.45 and 100 measured), one sideband only
(width unstored = 100); calibration/dev/probe_bode_width_wet.py: OutputWidth 0 puts both sidebands in both channels at about -6 dB each (M = (L+R)/2 of the two)."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration")); os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H
SR = H.SR; F0 = 440 * 2 ** ((48 - 69) / 12)
serum, base = P.start()
base = copy.deepcopy(base); base["Oscillator0"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.6}
def lines(x, width=60.0, ch=None):
    m = (x.mean(axis=0) if ch is None else x[ch])[int(0.5 * SR):int(1.9 * SR)]; n = 1 << 18
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)), n)); f = np.fft.rfftfreq(n, 1 / SR)
    out = []
    for k in (1, 2, 3):
        sel = (f > k * F0 - width) & (f < k * F0 + width)
        i = np.argmax(np.where(sel, sp, 0)); out.append((f[i] - k * F0, 20 * np.log10(sp[i] + 1e-12)))
    return out
def run(pp):
    t = P.fx_tree(base, "Bode", pp, 10); x = serum.render(t, [48], 2.0, 1.9)
    return lines(x, ch=0) + lines(x, ch=1)
dry = lines(serum.render(base, [48], 2.0, 1.9)); print("dry", " ".join("%+.2f Hz %.1f dB" % l for l in dry), flush=True)
for r in [float(a) for a in (sys.argv[1].split(",") if len(sys.argv) > 1 else ["0", "10.68", "43.45", "100"])]:
    for s in [float(a) for a in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["-100", "-50", "-6.14", "2.63", "6.14", "15.8", "50", "100"])]:
        L = run({"kParamWet": 100.0, "kParamShift": s, "kParamRange": r})
        print("Range %6.2f Shift %7.2f  " % (r, s) + " | ".join("%+7.2f %4.1f" % l for l in L), flush=True)
os._exit(0)
