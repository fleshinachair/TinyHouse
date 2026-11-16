#!/usr/bin/env python3
"""Serum's Flip warp on the Default Shapes table: at amounts 0 / 0.5 / 1 / 0.5034 / 0.45 / 0.55 and several table positions, the harmonics 1..20 against the unwarped table.
Result (2026-11-13): 0 and 1 are the table as it is; 0.5 and 0.5034 are the table read backwards (same magnitudes to 0.03 dB, phases negated exactly); 0.45 and 0.55 are 1.9 dB / 70 deg off.
The converter carries only those ends (deephouse2move.FLIP_TOLERANCE, reverse_table)."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2];sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT/"calibration")); os.chdir(ROOT)
import numpy as np
import probe_vfilter as V
import test_render_calibration as H
_, bt = H.load_preset("Keys - Loop")
t0 = H.init_tree(bt, table="S2 Tables/Default Shapes.wav", frames=9)
pool = V.SerumPool(); SR = H.SR; F0 = V.F0; NHH = 20
def cplx(x):
    m = x[0, int(0.35*SR):int(1.2*SR)]; sp = np.fft.rfft(m*np.hanning(len(m))); f = np.fft.rfftfreq(len(m), 1/SR)
    out = []
    for k in range(1, NHH+1):
        i = np.searchsorted(f, k*F0); w = sp[i-3:i+4]; out.append(w[np.argmax(np.abs(w))])
    return np.array(out)
def norm(c):  # amplitude and phase relative to fundamental
    return np.abs(c), np.angle(c * np.exp(-1j*np.angle(c[0])*np.arange(1, NHH+1)))
def go(pos, a):
    t = copy.deepcopy(t0); t["Oscillator0"]["WTOsc0"]["plainParams"] = {"kParamTablePos": pos, "kParamWarp": a, "kParamWarpMenu": "kFlip"}
    return norm(cplx(pool.render(t)))
for pos in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
    m0, p0 = go(pos, 0.0)
    for a in (0.5, 0.5034, 0.45, 0.55):
        m, p = go(pos, a)
        w = m0 > m0[0]*0.03          # harmonics worth comparing
        dm = 20*np.log10(m[w]/m0[w]); dp = np.degrees(np.angle(np.exp(1j*(p[w] + p0[w]))))   # reversed = phases negate -> p + p0 = 0
        print("pos %.1f a %.4f  mag diff max %.2f dB  rev-phase err max %.0f deg  fund %.2f dB" % (pos, a, np.abs(dm).max(), np.abs(dp).max(), 20*np.log10(m[0]/m0[0])), flush=True)
os._exit(0)
