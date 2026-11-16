#!/usr/bin/env python3
"""Serum's Delay and HyperD with Wet 0: are they bypassed?  A single saw (one voice, so the source is mono) through one node, note 48: level of Mid and Side (dB, 0.3-1.2 s) and the Mid
spectrum in octave bands against no node; the Delay at Wet 0 also with Time/Feedback stored, the HyperD at Wet 0 with the Dimension size / Wet and Detune/Unison stored.
Result (2026-11-13): Delay Wet 0 and HyperD Wet 0 (with or without Detune, Unison, Dimension Size, Time, Feedback, Freq, BW) leave the Mid within 0.07 dB and the Side inside the source's own spread
(+-4 dB between identical renders); a Delay at Wet 0 with Level Out 0.576 was +2.4 dB at every band; a HyperD with Dimension Wet 45.6 added 4 to 14 dB of Side. fxmap.idle_effect."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration")); os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H
SR = H.SR
serum, base = P.start()
base = copy.deepcopy(base); base["Oscillator3"]["plainParams"] = {"kParamEnable": 0.0}
base["Oscillator0"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.6, "kParamUnison": 1.0}
def lv(x):
    a = x[:, int(0.3 * SR):int(1.2 * SR)]; m = a.mean(axis=0); s = 0.5 * (a[0] - a[1])
    r = lambda v: 10 * np.log10(np.mean(v ** 2) + 1e-30)
    f = np.fft.rfftfreq(len(m), 1 / SR); p = np.abs(np.fft.rfft(m * np.hanning(len(m)))) ** 2
    bands = [10 * np.log10(p[(f > c / 1.414) & (f < c * 1.414)].sum() + 1e-30) for c in (100, 200, 400, 800, 1600, 3200, 6400)]
    return r(m), r(s), bands
d = None
def show(lab, x):
    global d
    mid, side, b = lv(x)
    if d is None: d = (mid, side, b)
    print("abs mid %.1f side %.1f " % (mid, side), end=""); print("%-44s mid %6.2f dB side %7.2f dB | bands re none: %s" % (lab, mid - d[0], side - d[1] if d[1] > -150 else side, " ".join("%5.1f" % (u - v) for u, v in zip(b, d[2]))), flush=True)
r = lambda t: serum.render(t, [48], 1.5, 1.2)
show("none", r(base)); show("none again", r(base))
for lab, ty, pp in [
    ("Delay Wet 0", 4, {"kParamWet": 0.0}),
    ("Delay Wet 0, Time .045 Fb 31 BW 6.75", 4, {"kParamWet": 0.0, "kParamTimeL": 0.0453, "kParamTimeR": 0.0453, "kParamFeedback": 31.6, "kParamBW": 6.75, "kParamMode": 1.0}),
    ("Delay Wet 0, Time .05 Fb 70 Freq 734", 4, {"kParamWet": 0.0, "kParamTimeL": 0.0508, "kParamFeedback": 70.3, "kParamFreq": 734.0, "kParamBW": 1.07, "kParamLevelOut": 0.576}),
    ("Delay Wet 25 (control)", 4, {"kParamWet": 25.0, "kParamTimeL": 0.0453, "kParamTimeR": 0.0453, "kParamFeedback": 31.6}),
    ("HyperD Wet 0", 9, {"kParamWet": 0.0}),
    ("HyperD Wet 0, DimESize 20", 9, {"kParamWet": 0.0, "kParamDimESize": 20.2}),
    ("HyperD Wet 0, DimESize 0", 9, {"kParamWet": 0.0, "kParamDimESize": 0.0}),
    ("HyperD Wet 0, DimEWet 0, DimESize 20", 9, {"kParamWet": 0.0, "kParamDimEWet": 0.0, "kParamDimESize": 20.2}),
    ("HyperD Wet 0, DimEWet 45.5, DimESize 13.9", 9, {"kParamWet": 0.0, "kParamDimEWet": 45.6, "kParamDimESize": 13.9}),
    ("HyperD Wet 0, DimEWet 45.5, DimESize 0", 9, {"kParamWet": 0.0, "kParamDimEWet": 45.6, "kParamDimESize": 0.0}),
    ("HyperD Wet 0, Detune 47 Unison 1 DimESize 11.6", 9, {"kParamWet": 0.0, "kParamDetune": 47.0, "kParamUnison": 1.0, "kParamDimESize": 11.6}),
    ("HyperD Wet 0, Detune 11 Unison 2", 9, {"kParamWet": 0.0, "kParamDetune": 11.4, "kParamUnison": 2.0, "kParamDimESize": 0.0}),
]:
    show(lab, r(P.fx_tree(base, "Delay" if ty == 4 else "HyperD", pp, ty)))
os._exit(0)
