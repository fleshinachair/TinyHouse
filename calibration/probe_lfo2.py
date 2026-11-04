#!/usr/bin/env python3
"""Serum LFO rate, default shape and depth on the oscillator level, measured without clipping.

A quiet base level (so the LFO cannot push the oscillator past full scale), the LFO routed to the oscillator volume,
the envelope of the output read in 10 ms windows: the dominant frequency of that envelope is the LFO's rate, its harmonics
say the shape, its range says the depth. Run for: every setting at default; rate 2 / 5 / 10 Hz set; unipolar and bipolar.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import probe_lfo as P
import test_render_calibration as H

SR = H.SR


def env_stats(x):
    m = x[0, int(0.3 * SR):int(3.0 * SR)]
    n = int(0.005 * SR)
    k = len(m) // n
    e = np.sqrt((m[:k * n].reshape(k, n) ** 2).mean(axis=1))                  # linear amplitude envelope, 5 ms
    d = e - e.mean()
    sp = np.abs(np.fft.rfft(d * np.hanning(len(d))))
    f = np.fft.rfftfreq(len(d), 0.005)
    lo = np.searchsorted(f, 0.5)
    i = lo + int(np.argmax(sp[lo:np.searchsorted(f, 60)]))
    f0 = f[i]
    h2 = sp[min(int(round(2 * f0 / (f[1]))), len(sp) - 1)] / sp[i]
    h3 = sp[min(int(round(3 * f0 / (f[1]))), len(sp) - 1)] / sp[i]
    return f0, h2, h3, e.min(), e.max(), e.mean()


def main():
    s = H.Serum()
    OSC = ("Oscillator", "kParamVolume", 0, 1)

    def tree(lfo, amt, bip):
        t = P.tree_with(lfo_params=lfo, route=OSC + (amt, bip))
        t["Oscillator0"]["plainParams"] = {"kParamVolume": 0.25}
        return t
    flat = s.render(P.tree_with(), [48], 3.2, 3.1, vel=100)
    t0 = P.tree_with(); t0["Oscillator0"]["plainParams"] = {"kParamVolume": 0.25}
    base = env_stats(s.render(t0, [48], 3.2, 3.1, vel=100))
    print(f"no LFO at volume 0.25: amplitude {base[5]:.4f}")
    print(f"{'case':44s} {'freq Hz':>8s} {'2nd/1st':>8s} {'3rd/1st':>8s} {'min':>8s} {'max':>8s} {'mean':>8s}")
    for lab, lfo in (("default LFO", None), ("rate 2 Hz", {"kParamRate": 2.0, "kParamBeatSync": 0.0}), ("rate 5 Hz", {"kParamRate": 5.0, "kParamBeatSync": 0.0}),
                     ("rate 10 Hz", {"kParamRate": 10.0, "kParamBeatSync": 0.0})):
        for amt, bip in ((50, 0), (50, 1)):
            f0, h2, h3, lo, hi, mean = env_stats(s.render(tree(lfo, amt, bip), [48], 3.2, 3.1, vel=100))
            print(f"{lab + ', amount ' + str(amt) + (' bipolar' if bip else ' unipolar'):44s} {f0:8.2f} {h2:8.2f} {h3:8.2f} {lo:8.4f} {hi:8.4f} {mean:8.4f}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
