#!/usr/bin/env python3
"""Serum's Distortion filter between its ends: noise through Drive 0, PrePost 1 (after), Freq 0.5, BW 2, LPHP at 0 / 20 / 35 / 50 / 65 / 80 / 100 %: the measured transfer
(dB on probe_fxfilter_drive.GRID2) against candidates for the morph: the crossfade (1 - t) LP + t HP of one 12 dB state-variable filter (Q = 0.5 + 0.55 BW), the same
as a power fade, and the band-pass-style sum. Prints the measured rows and each candidate's rms error (dB).  Output: calibration/probe_dist_morph.json"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
from probe_fxfilter_drive import GRID2, tf2
import fxmap

serum, base = P.start()
b = copy.deepcopy(base)
b["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 1.0}
dry = P.render(serum, b)
f = np.array(GRID2, float)
FREQ, BW = 0.5, 2.0
fc = fxmap.serum_filter_hz(FREQ); q = 0.5 + 0.55 * BW
x = f / fc
D = 1.0 - x ** 2 + 1j * x / q
LP, HP = 1.0 / D, -(x ** 2) / D
res = {"grid": GRID2, "fc": fc, "q": q, "rows": {}}
print("fc %.0f Hz  Q %.2f" % (fc, q)); print("grid        ", " ".join("%6d" % g for g in GRID2))
for t in (0, 20, 35, 50, 65, 80, 100):
    pp = {"kParamDrive": 0.0, "kParamWet": 100.0, "kParamPrePost": 1.0, "kParamFreq": FREQ, "kParamBW": BW, "kParamLPHP": float(t)}
    g = np.array(tf2(dry, serum.render(P.fx_tree(b, "Distortion", pp, 0), [48], 2.0, 1.9)))
    res["rows"][str(t)] = g.tolist()
    s = t / 100.0
    cand = {"crossfade": (1 - s) * LP + s * HP, "power": np.sqrt(1 - s) * LP + np.sqrt(s) * HP, "sum": LP + HP * s}
    err = {k: float(np.sqrt(np.mean((20 * np.log10(np.abs(v) + 1e-6) - g) ** 2))) for k, v in cand.items()}
    print("LPHP %3d     " % t + " ".join("%6.1f" % v for v in g) + "   err " + " ".join("%s %.1f" % (k, e) for k, e in err.items()), flush=True)
Path("calibration/probe_dist_morph.json").write_text(json.dumps(res, indent=1))
sys.stdout.flush(); os._exit(0)
