#!/usr/bin/env python3
"""What a Bode node does in Serum when its Mix is unwritten or ~0: a saw through the node against the same saw with no node.  Deep House has six Bode nodes (type id 10): two with no
params (oskar med k, Kisaya), HotLap's with a Blur / DelayTime / Feedback but no mix, NOTSOBAD's OutputMix 26.7, Origins' OutputMix 0.32.  Prints level and spectrum difference (dB)
of node-vs-none for each case, and the same render against itself (the floor)."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H
SR = H.SR
EDGES = [60, 120, 250, 500, 1000, 2000, 4000, 8000, 16000]
def bands(x):
    m = x.mean(axis=0)[int(0.4 * SR):int(1.8 * SR)]
    s = np.abs(np.fft.rfft(m * np.hanning(len(m)))) ** 2; f = np.fft.rfftfreq(len(m), 1 / SR)
    return np.array([10 * np.log10(s[(f >= a) & (f < b)].sum() + 1e-20) for a, b in zip(EDGES[:-1], EDGES[1:])])
serum, base = P.start()
base = copy.deepcopy(base); base["Oscillator0"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.6}
none = bands(serum.render(base, [48], 2.0, 1.9))
print("band edges", EDGES)
CASES = {"Bode, no params": {}, "Bode OutputMix 0.32 (Origins)": {"kParamOutputMix": 0.32, "kParamShift": 0.12, "kParamBlur": 4.8, "kParamFeedback": 8.68, "kParamRange": 13.27},
         "Bode OutputMix 26.7 (NOTSOBAD)": {"kParamBlur": 41.67, "kParamDelayBalance": 41.54, "kParamOutputMix": 26.7, "kParamOutputWidth": 71.8},
         "Bode HotLap (no mix)": {"kParamBlur": 100.0, "kParamDelayTime": 0.0, "kParamFeedback": 14.39, "kParamRange": 0.1}, "Bode OutputMix 100": {"kParamOutputMix": 100.0}}
for lab, pp in CASES.items():
    t = P.fx_tree(base, "Bode", pp, 10)
    d = bands(serum.render(t, [48], 2.0, 1.9)) - none
    print("%-34s %s" % (lab, " ".join("%+6.1f" % v for v in d)), flush=True)
print("floor (same patch again)           ", " ".join("%+6.1f" % v for v in bands(serum.render(base, [48], 2.0, 1.9)) - none))
os._exit(0)
