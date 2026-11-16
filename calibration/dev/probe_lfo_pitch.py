#!/usr/bin/env python3
"""How far a constant LFO moves an oscillator's coarse and fine pitch in Serum, unipolar and bipolar (semitones per unit of amount)."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import probe_lfo as P
import probe_lfo_level as Q
import probe_vfilter as V
import test_render_calibration as H


def f0_of(x):
    m = x[0, int(0.3 * H.SR):int(1.0 * H.SR)]
    n = 1 << 18
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)), n))
    f = np.fft.rfftfreq(n, 1 / H.SR)
    lo, hi = np.searchsorted(f, 20), np.searchsorted(f, 1500)
    i = lo + int(np.argmax(sp[lo:hi]))
    return f[i]


def main():
    pool = V.SerumPool()
    for pid, name, amts in ((6, "kParamCoarsePit", (5, 10, 25)), (5, "kParamFine", (25, 50, 100)), (3, "kParamOctave", (10, 25))):
        for bip in (0, 1):
            for amt in amts:
                t = P.tree_with(curve=Q.flat(1.0), route=("Oscillator", name, 0, pid, amt, bip), lfo_params={"kParamRate": 1.0, "kParamBeatSync": 0.0})
                t["Oscillator0"]["plainParams"] = {"kParamVolume": 0.5}
                base = P.tree_with(); base["Oscillator0"]["plainParams"] = {"kParamVolume": 0.5}
                f1 = f0_of(pool.render(t)); f0 = f0_of(pool.render(base))
                st = 12 * np.log2(f1 / f0)
                print(f"{name:16s} {'bi ' if bip else 'uni'} amount {amt:3d}: {f0:7.2f} -> {f1:7.2f} Hz = {st:+7.3f} st  ({st / (amt / 100):+.2f} st per unit)", flush=True)
    os._exit(0)


main()
