#!/usr/bin/env python3
"""Where each source goes when its routing slot is not written: a source through a closed filter is silent, one that bypasses it is not. One source at a time
(osc A, B, C, noise, sub), RoutingSlot left as written in the init patch, the voice filter an MgL24 at cutoff 0.  Prints the level in dBFS."""
import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import test_render_calibration as H

SR = H.SR
_, base_tree = H.load_preset("Keys - Loop")
serum = H.Serum()
ids = {"A": "Oscillator0", "B": "Oscillator1", "C": "Oscillator2", "noise": "Oscillator3", "sub": "Oscillator4"}
for closed in (False, True):
    for src, key in ids.items():
        t = H.init_tree(base_tree)
        for k, o in ids.items():
            pp = {"kParamEnable": 1.0 if k == src else 0.0, "kParamVolume": 0.75}
            t[o]["plainParams"] = pp
        if src != "A":
            t["Oscillator0"]["plainParams"] = {"kParamEnable": 0.0}
        t["VoiceFilter0"]["plainParams"] = {"kParamEnable": 1.0, "kParamFreq": 0.0 if closed else 1.0, "kParamType": "MgL24"}
        x = serum.render(t, [48], 1.5, 1.2)
        print("%-6s filter %-6s rms %6.1f dBFS" % (src, "closed" if closed else "open", 10 * np.log10((x[:, int(0.4 * SR):int(1.1 * SR)] ** 2).mean() + 1e-20)), flush=True)
os._exit(0)
