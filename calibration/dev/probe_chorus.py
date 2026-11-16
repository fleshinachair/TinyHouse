#!/usr/bin/env python3
"""Serum's Chorus on white noise at Wet 100: the comb spacing gives the delay, the motion of the comb over time its rate and depth.
Prints, for default settings and single changes (Delay, Depth, Rate, Feedback, Filt), the estimated delay (ms), LFO rate (Hz) and delay swing (ms)."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H

SR = H.SR
N = 8192


def analyse(x):
    m = x.mean(axis=0)[int(0.3 * SR):]
    hop = 1024
    frames = []
    w = np.hanning(N)
    for i in range(0, len(m) - N, hop):
        frames.append(np.abs(np.fft.rfft(m[i:i + N] * w)) ** 2)
    F = np.array(frames)
    f = np.fft.rfftfreq(N, 1 / SR)
    sel = (f > 300) & (f < 6000)
    L = 10 * np.log10(F[:, sel] + 1e-20)
    L -= L.mean(axis=1, keepdims=True)
    # comb spacing from the autocorrelation of the log spectrum across frequency, averaged over frames
    ac = np.mean([np.correlate(r - r.mean(), r - r.mean(), "full")[len(r) - 1:] for r in L], axis=0)
    df = f[1] - f[0]
    lo, hi = int(30 / df), int(1500 / df)                    # comb spacings 30 Hz .. 1.5 kHz
    pk = lo + int(np.argmax(ac[lo:hi]))
    spacing = pk * df
    delay_ms = 1000.0 / spacing if spacing > 0 else float("nan")
    # how the comb moves: spectral flux over time -> modulation rate
    flux = np.abs(np.diff(L, axis=0)).mean(axis=1)
    fl = flux - flux.mean()
    sp = np.abs(np.fft.rfft(fl * np.hanning(len(fl))))
    ff = np.fft.rfftfreq(len(fl), hop / SR)
    k = 1 + int(np.argmax(sp[1:int(8 / (ff[1] + 1e-9))]))
    return delay_ms, float(ff[k]), float(flux.mean())


def main():
    _, base_t = H.load_preset("Bass - Gravity")
    base = P.noise_base(H.init_tree(base_t))
    serum = [H.Serum()]
    def render(params):
        if serum[0].audio_seconds > 36:
            serum[0] = H.Serum()
        t = P.fx_tree(base, "Chorus", params, 3) if params is not None else base
        return serum[0].render(t, [48], 4.0, 3.95)
    dry = analyse(render(None))
    print(f"dry noise: comb {dry[0]:.2f} ms, flux {dry[2]:.2f} (noise floor)")
    cases = {"default wet100": {"kParamWet": 100.0}, "Delay 1": {"kParamWet": 100.0, "kParamDelay": 1.0}, "Delay 6": {"kParamWet": 100.0, "kParamDelay": 6.0},
             "Depth 5": {"kParamWet": 100.0, "kParamDepth": 5.0}, "Depth 20": {"kParamWet": 100.0, "kParamDepth": 20.0},
             "Rate 0.5": {"kParamWet": 100.0, "kParamRate": 0.5}, "Rate 0.1": {"kParamWet": 100.0, "kParamRate": 0.1}}
    for name, p in cases.items():
        d, r, fx_ = analyse(render(p))
        print(f"{name:16s} comb delay {d:6.2f} ms | motion rate {r:5.2f} Hz | flux {fx_:5.2f}", flush=True)
    os._exit(0)


main()
