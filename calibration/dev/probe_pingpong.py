#!/usr/bin/env python3
"""Ping-pong delay: a short noise burst through Serum's Delay in Ping-Pong mode (Time 1/8, feedback 50 %, wet 100) and through the engine's (dly_mode 1), the echoes
in the LEFT and RIGHT channels separately (time s, level dB re the burst)."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import fxmap
import test_render_calibration as H

SR = H.SR
def peaks(x):
    w = int(0.01 * SR)
    env = 10 * np.log10(np.array([(x[i:i + w] ** 2).mean() for i in range(0, len(x) - w, w)]) + 1e-14)
    pk = env[:12].max()
    e = env - pk
    out = []
    for i in range(14, len(e) - 2):
        if e[i] > e[i - 1] and e[i] >= e[i + 1] and e[i] > -40 and e[i] > e[max(i - 6, 0):i].min() + 3:
            out.append((round(i * 0.01, 2), round(float(e[i]), 1)))
    return out[:7]
serum, base = P.start()
for mode in (0.0, 1.0):
    node = {"FXDelay": {"plainParams": {"kParamTimeL": 0.0425, "kParamTimeR": 0.0425, "kParamFeedback": 50.0, "kParamWet": 100.0, "kParamMode": mode, "kParamFreq": 1000.0, "kParamBW": 8.0}}, "type": 4}
    t = copy.deepcopy(base); t["FXRack0"]["FX"] = [node]
    x = serum.render(t, [48], 3.0, 0.12)
    print("serum mode", int(mode), "L", peaks(x[0])); print("             R", peaks(x[1]), flush=True)
for mode in (0, 1):
    b = fxmap.noise_patch({"dly_mix": 127, "dly_time": 84, "dly_timer": 84, "dly_fb": 64, "dly_mode": mode, "dly_lo": 0, "dly_hi": 127, "dly_on": 1})
    x = H.run_cli(b, [48], 3.0, 0.12)
    print("engine mode", mode, "L", peaks(x[0])); print("              R", peaks(x[1]), flush=True)
os._exit(0)
