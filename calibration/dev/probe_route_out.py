#!/usr/bin/env python3
"""A mod route's kParamOut: what it does in Serum.  A route saved with kParamOut (40 of 951 slots in the Deep House pack; Lead - Let It Go, Self Control,
Billboard carry it on their oscillator coarse-pitch routes at 0.86 to 7.4) scales its depth: amount x Out / 100, Out defaulting to 100.  Without the scale the
converter built full-depth vibrato (+/- 6 st) where Serum is flat, which was the sub-bass excess and dull centroid on those presets.
Measured: a 4.3 Hz sine LFO into Oscillator coarse pitch (pitch swing, max - min over the held note), and into the voice filter cutoff (centroid swing)."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import probe_lfo as P
import probe_vfilter as V
import test_render_calibration as H

SR = H.SR


def pitch_swing(x):
    m, n = x[0], 1 << 16
    f = np.fft.rfftfreq(n, 1 / SR)
    lo, hi = np.searchsorted(f, 40), np.searchsorted(f, 700)
    out = []
    for k in range(80):
        t = 0.25 + k * 0.01
        seg = m[int(t * SR):int((t + 0.06) * SR)]
        out.append(f[lo + int(np.argmax(np.abs(np.fft.rfft(seg * np.hanning(len(seg)), n))[lo:hi]))])
    st = 12 * np.log2(np.array(out) / np.median(out))
    return st.max() - st.min()


def main():
    pool = V.SerumPool()
    for out in (None, 1.0, 5.0, 20.0, 60.0):
        for amt in (10, 50, 100):
            t = P.tree_with(route=("Oscillator", "kParamCoarsePit", 0, 6, amt, 1), lfo_params={"kParamRate": 4.3, "kParamBeatSync": 0.0})
            t["Oscillator0"]["plainParams"] = {"kParamVolume": 0.5}
            if out is not None:
                t["ModSlot0"]["plainParams"]["kParamOut"] = out
            print(f"coarse pitch  Out {out if out is not None else 'none':>5}  amount {amt:3d} bipolar: swing {pitch_swing(pool.render(t)):5.1f} st", flush=True)
    for out in (None, 50.0, 10.0):
        t = P.tree_with(lfo_params={"kParamRate": 5.0, "kParamBeatSync": 0.0, "kParamDefaultMode": 0.0}, route=("VoiceFilter", "kParamFreq", 0, 3, 30, 1), filt=0.5)
        if out is not None:
            t["ModSlot0"]["plainParams"]["kParamOut"] = out
        c, _ = P.centroid_env(pool.render(t))
        print(f"filter cutoff Out {out if out is not None else 'none':>5}  amount  30 bipolar: centroid swing {np.percentile(c, 95) - np.percentile(c, 5):5.0f} Hz", flush=True)
    os._exit(0)


main()
