#!/usr/bin/env python3
"""Does the voice filter's Drive change the level? Noise (volume 0.3 and 0.75) through an open MgL24 at Drive 0..100 in Serum, the mean dB change over
200 Hz-3.2 kHz against Drive 0; and the engine's the same through its flt_drive pot for the converter's mapping.  Output: printed table."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import deephouse2move as d2m
import fx_probe as P
import fxmap
import test_render_calibration as H
from probe_fxfilter_drive import GRID2, tf2

serum, base = P.start()
OFF = {"kParamEnable": 0.0}
rows = {}
for vol in (0.3, 0.75):
    b = copy.deepcopy(base)
    b["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": vol}
    b["RoutingSlot3"] = {"plainParams": {"kParamRoutingDest": "kRoutingDestFilter"}}
    b["VoiceFilter1"] = {"plainParams": OFF}
    b["VoiceFilter0"] = {"plainParams": {"kParamEnable": 1.0, "kParamFreq": 1.0, "kParamType": "MgL24"}}
    ref = serum.render(b, [48], 2.0, 1.9)
    for d in (0, 10, 25, 50, 75, 100):
        t = copy.deepcopy(b); t["VoiceFilter0"]["plainParams"]["kParamDrive"] = float(d)
        g = tf2(ref, serum.render(t, [48], 2.0, 1.9))
        rows[(vol, d)] = float(np.mean(g[4:12]))
        eng = {}
        print("serum vol %.2f drive %3d : %+5.1f dB (mean of 200 Hz-3.2 kHz points, re drive 0)" % (vol, d, rows[(vol, d)]), flush=True)
for vol in (0.3, 0.75):
    pot = round(127 * vol ** 0.5)
    base_rms = None
    for d in (0, 10, 25, 50, 75, 100):
        dp = round(d2m.interp(d2m.FILTER_DRIVE_POT, d))
        a = fxmap.render_cli(fxmap.noise_patch({"noise_level": pot, "noise_type": 2, "flt_drive": dp, "flt_freq": 127, "flt_type": 1}), 2.0, 1.9)
        r = 10 * np.log10((a[:, int(0.4 * 44100):] ** 2).mean())
        base_rms = r if base_rms is None else base_rms
        print("engine vol %.2f drive %3d (pot %3d): %+5.1f dB" % (vol, d, dp, r - base_rms), flush=True)
os._exit(0)
