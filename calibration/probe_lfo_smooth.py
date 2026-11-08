#!/usr/bin/env python3
"""What does Serum's LFO Smooth do? Lead - Loop's own tree (effects stripped; its LFO 1, a custom shape, drives oscillator A's level from knob 0, so the
level trace is the LFO) with Smooth set to 0 / 16 / 40 / 70 / 100 at two synced rates. The knob (square root of the amplitude, in 5 ms windows of a
1 kHz note) is fitted by the shape's ideal LFO through a one-pole smoother of time constant tau (phase, scale and offset free); tau is reported in
ms and as a fraction of the LFO period, against Smooth.
(Superseded fit: the ideal shape did not match Serum's own trace, so the Smooth-0 trace of the same render is smoothed instead, with a one-pole and with a boxcar.)
Output: calibration/probe_lfo_smooth.json and a printed table.
"""
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import deephouse2move as d2m
import test_render_calibration as H

SR = H.SR
WIN = 0.005
NOTE = 84
BPM_BEATS = 0.5          # seconds per beat at the harness's 120 BPM


def trace(s, tree, rate, smooth):
    t = copy.deepcopy(tree); pp = t["LFO0"]["plainParams"]
    pp["kParamRate"] = rate; pp["kParamSmooth"] = smooth
    x = s.render(t, [NOTE], 3.2, 3.1, vel=100)
    m = x[0, int(0.02 * SR):int(3.0 * SR)]; n = int(WIN * SR); k = len(m) // n
    return np.sqrt(np.sqrt((m[:k * n].reshape(k, n) ** 2).mean(axis=1)))


def onepole(v, tau, acc0=None):
    """Causal one-pole low-pass of v (5 ms steps), started at acc0 (default v[0])."""
    if tau <= 0: return v.copy()
    a = 1 - np.exp(-WIN / tau); y = np.empty_like(v); acc = v[0] if acc0 is None else acc0
    for i in range(len(v)):
        acc += a * (v[i] - acc); y[i] = acc
    return y


def boxcar(v, width):
    """Centred moving average over `width` seconds."""
    k = max(int(round(width / WIN)), 1)
    return np.convolve(np.pad(v, (k // 2, k - 1 - k // 2), mode="edge"), np.ones(k) / k, mode="valid")


def fit(u0, us):
    """The time constant (one-pole) and the width (boxcar) that turn the Smooth-0 trace into this one, and their errors (relative to the trace's range)."""
    rng = max(us.max() - us.min(), 1e-9)
    taus = [0.0] + list(np.geomspace(0.002, 3.0, 70))
    best_p = min(((float(np.sqrt(np.mean((onepole(u0, t, a0) - us) ** 2)) / rng), t, a0 is None) for t in taus for a0 in (None, 0.0)))
    best_b = min(((float(np.sqrt(np.mean((boxcar(u0, w) - us) ** 2)) / rng), w) for w in np.arange(0.005, 0.60, 0.005)))
    return best_p, best_b


def main():
    tree = H.strip_fx(H.load_preset("Lead - Loop")[1])
    out = {}
    print("period  smooth | one-pole tau (ms)  tau/period  rms err | boxcar width (ms)  width/period  rms err")
    for rate, beats in ((14.68575551876285, 0.5), (5.0, 2.0)):
        period = beats * BPM_BEATS
        u0 = trace(H.Serum(), tree, rate, 0.0)
        for sm in (8.0, 16.0, 25.0, 40.0, 55.0, 70.0, 85.0, 100.0):
            us = trace(H.Serum(), tree, rate, sm)
            (ep, tau, from_first), (eb, w) = fit(u0, us)
            out[f"beats{beats}_s{sm}"] = {"tau_s": tau, "tau_over_period": tau / period, "err_pole": ep, "starts_at_first_value": bool(from_first), "box_s": w, "box_over_period": w / period, "err_box": eb}
            print(f"{period * 1000:5.0f} ms  {sm:5.0f} | {tau * 1000:9.0f}        {tau / period:7.3f}    {ep:.3f} | {w * 1000:9.0f}          {w / period:7.3f}      {eb:.3f}  {'starts at the first value' if from_first else 'starts at 0'}")
            sys.stdout.flush()
    Path("calibration/probe_lfo_smooth.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    os._exit(0)


if __name__ == "__main__":
    main()
