#!/usr/bin/env python3
"""The voice filter's Drive on a saw: harmonic levels (dB re drive 0) of a held saw (note 48, volume 0.75 and 0.3) through an MgL24 at 0.5 at Drive 0..100.
If the drive were a plain gain every harmonic would move by the same amount."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import test_render_calibration as H
from check_filter2 import harm

_, base_tree = H.load_preset("Keys - Loop")
serum = H.Serum()
for vol in (0.75, 0.3):
    ref = None
    for d in (0, 10, 25, 50, 100):
        t = H.init_tree(base_tree)
        t["Oscillator0"]["plainParams"]["kParamVolume"] = vol
        t["VoiceFilter0"]["plainParams"] = {"kParamEnable": 1.0, "kParamFreq": 0.5, "kParamType": "MgL24", "kParamDrive": float(d)}
        t["RoutingSlot0"] = {"plainParams": {"kParamRoutingDest": "kRoutingDestFilter"}}
        h = harm(serum.render(t, [48], 1.6, 1.4))
        ref = h if ref is None else ref
        print("vol %.2f drive %3d  h1..h14 re drive 0:" % (vol, d), " ".join("%+5.1f" % x for x in (h - ref)[:14]), "  abs h1 %.1f" % h[0], flush=True)
os._exit(0)
