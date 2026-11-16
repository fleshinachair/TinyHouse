#!/usr/bin/env python3
"""The comb / flanger voice filters against Serum through the real converter and engine: white noise through the filter, the power spectrum of each side set against its own unfiltered
noise (so level differences between the two noise generators drop out).  Per case: the mean power gain on each side, the rms difference of the two responses in dB (3-bin smoothed,
200 Hz .. 12 kHz; the estimate itself is good to about 1.3 dB) and the tooth spacing read from the spectrum.
Usage: validate_comb.py   (needs a built calibration/tablor_cli.exe)"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import probe_comb as C
import numpy as np
import test_render_calibration as H
SR = H.SR; f = np.fft.rfftfreq(C.NFFT, 1 / SR); sel = (f > 200) & (f < 12000)
CASES = [("CombP", 0.5, 30), ("CombP", 0.5, 50), ("CombP", 0.5, 80), ("CombP", 0.3, 50), ("CombP", 0.7, 50), ("CombN", 0.5, 50), ("CombN", 0.4, 70),
         ("FlangeP", 0.5, 50), ("FlangeP", 0.6, 30), ("FlangeN", 0.5, 50), ("FlangeN", 0.55, 70)]
if len(sys.argv) > 1:
    CASES = [tuple(float(x) if i else x for i, x in enumerate(a.split(":"))) for a in sys.argv[1:]]


def engine_response(tree):
    x, m = H.tablor_render(tree, [48], C.DUR, C.HOLD)
    return C.spectrum(x), m


base_e, _ = engine_response(C.noise_tree())
base_s = np.array(C.response("CombP", 0.5, 0)) * 0                                   # (the cached "base" is what response() divides by)
import json
cache = json.loads(C.CACHE.read_text()); base_s = np.array(cache["base"])
worst = 0.0
for model, cut, reso in CASES:
    hs = C.response(model, cut, reso)
    sp_e, m = engine_response(C.noise_tree(model, cut, reso))
    he = 10 * np.log10(sp_e / base_e + 1e-12)
    sm = lambda h: np.convolve(h, np.ones(3) / 3, "same")
    d = sm(he) - sm(hs); rms = float(np.sqrt(np.mean(d[sel] ** 2)))
    mean_s = 10 * np.log10(np.mean(10 ** (hs[sel] / 10))); mean_e = 10 * np.log10(np.mean(10 ** (he[sel] / 10)))
    def spacing(h):
        s = sm(h)[sel] - sm(h)[sel].mean(); lo = int(100 / (SR / C.NFFT)); ac = np.correlate(s, s, "full")[len(s) - 1:]
        return (lo + int(np.argmax(ac[lo:int(4000 / (SR / C.NFFT))]))) * SR / C.NFFT
    worst = max(worst, rms)
    print("%-8s cut %.2f reso %3d   mean power Serum %+5.1f dB / engine %+5.1f dB   response rms diff %.2f dB   tooth spacing Serum %.0f / engine %.0f Hz   type %s" %
          (model, cut, reso, mean_s, mean_e, rms, spacing(hs), spacing(he), m["params"].get("flt_type")), flush=True)
print("worst rms", round(worst, 2))
sys.stdout.flush(); os._exit(0)
