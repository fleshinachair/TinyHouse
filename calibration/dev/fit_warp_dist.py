#!/usr/bin/env python3
"""Serum's Dist* oscillator warps (probe_warp_dist.json: a sine through the warp, harmonics 1..12 at amounts 0..1) as a static shaper out = K f(G x) of a unit sine, per mode and
amount: the curve family chosen per mode (the best of hard clip / softsign / cubic soft clip over the odd harmonics 3..11), then G per amount (harmonics re fundamental, dB)
and K (the fundamental against the unwarped sine). Diode 2 is asymmetric (even harmonics) and not fitted. Output: calibration/warp_dist_curves.json
{mode: {"curve", "amounts", "G", "K", "err_db"}}."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
import numpy as np
d = json.load(open("calibration/probe_warp_dist.json")); ref = np.array(d["ref"])
N = 1 << 13; th = np.arange(N) * 2 * np.pi / N; x = np.sin(th)
CURVES = {"clip": lambda u: np.clip(u, -1, 1), "softsign": lambda u: u / (1 + np.abs(u)), "cubic": lambda u: np.where(np.abs(u) < 1, 1.5 * (u - u ** 3 / 3), np.sign(u))}
ENGINE_ID = {"clip": 2, "softsign": 3, "cubic": 4}      # (no tanh: std::tanh costs 7x the others per stream-sample, and Diode 1 fits a hard clip as well)
SEL = [2, 4, 6, 8, 10]
GS = np.exp(np.linspace(np.log(0.02), np.log(300), 260))
def harm(y):
    sp = np.abs(np.fft.rfft(y)) * 2 / N; return sp[1:13]
out = {}
for md, rows in d["modes"].items():
    if md == "kDistDiode2":
        continue
    best = None
    for name, fn in CURVES.items():
        fits = {}
        for a, h in rows.items():
            h = np.array(h); rel = h - h[0]
            e_best = None
            for G in GS:
                hh = harm(fn(G * x)); hr = 20 * np.log10(hh / hh[0] + 1e-12)
                e = float(np.sqrt(np.mean([(max(hr[i], -60) - max(rel[i], -60)) ** 2 for i in SEL])))
                if e_best is None or e < e_best[0]: e_best = (e, float(G), float(hh[0]))
            K = 10 ** ((h[0] - ref[0]) / 20) / e_best[2]
            fits[a] = (e_best[0], e_best[1], float(K))
        m = float(np.mean([v[0] for v in fits.values()]))
        if best is None or m < best[0]: best = (m, name, fits)
    m, name, fits = best
    amts = sorted(fits, key=float)
    out[md] = {"curve": name, "engine_curve": ENGINE_ID[name], "amounts": [float(a) for a in amts], "G": [round(fits[a][1], 4) for a in amts], "K": [round(fits[a][2], 4) for a in amts], "err_db": [round(fits[a][0], 2) for a in amts]}
    print("%-13s %-8s mean err %.1f dB\n   G %s\n   K %s" % (md, name, m, out[md]["G"], out[md]["K"]), flush=True)
Path("calibration/warp_dist_curves.json").write_text(json.dumps(out, indent=1))
sys.stdout.flush()
