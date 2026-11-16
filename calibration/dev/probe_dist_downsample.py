#!/usr/bin/env python3
"""Serum's Distortion mode Downsample: a sine through it at several drives (wet 100); the strongest spectral lines, to read off the hold rate (images at k x rate +- f0) and whether it also
quantises. Output: printed table, calibration/probe_dist_downsample.json {drive: [[hz, dB re fundamental], ...]}.  Usage: probe_dist_downsample.py [note]"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fxmap
import test_render_calibration as H
SR = H.SR
NOTE = int(sys.argv[1]) if len(sys.argv) > 1 else 57
F0 = 440.0 * 2 ** ((NOTE - 69) / 12.0)
header, t0 = H.load_preset("Bass - AVAION")
dist = next(copy.deepcopy(n) for n in t0["FXRack0"]["FX"] if fxmap.kind_of(n) == "Distortion")
s = H.Serum()
header, base = H.load_preset("Keys - Loop")
def render(drive):
    t = H.init_tree(base, table="Analog/Basic Shapes.wav", frames=7)
    t["Oscillator0"]["plainParams"] = {"kParamVolume": 0.5}
    n = copy.deepcopy(dist); n["FXDistortion"]["plainParams"] = {"kParamDrive": float(drive), "kParamWet": 100.0, "kParamMode": "kDownsample"}
    t["FXRack0"]["FX"] = [n]
    return s.render(t, [NOTE], 1.4, 1.3, vel=100)[0]
out = {}
for d in (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100):
    x = render(d)[int(0.3 * SR):int(1.2 * SR)]
    n = 1 << 15; w = np.hanning(n)
    sp = np.abs(np.fft.rfft(x[:n] * w)); f = np.fft.rfftfreq(n, 1 / SR)
    k0 = int(np.argmax(sp)); ref = sp[k0]
    # local maxima, strongest first
    pk = [i for i in range(2, len(sp) - 2) if sp[i] > sp[i - 1] and sp[i] >= sp[i + 1] and sp[i] > ref * 10 ** (-60 / 20)]
    pk.sort(key=lambda i: -sp[i])
    out[str(d)] = [[round(float(f[i]), 1), round(float(20 * np.log10(sp[i] / ref)), 1)] for i in pk[:8]]
    print("drive %3d  f0 %.1f  " % (d, F0) + "  ".join("%.0f:%+.0f" % (a, b) for a, b in out[str(d)]), flush=True)
Path("calibration/probe_dist_downsample.json").write_text(json.dumps(out, indent=1))
sys.stdout.flush(); os._exit(0)
