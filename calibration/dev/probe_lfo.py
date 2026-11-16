#!/usr/bin/env python3
"""What does a Serum LFO do? Play test patches through Serum itself and measure.

One saw oscillator, no filter (unless the case adds one), one LFO routed to one destination, a held note. The LFO's rate, shape
and a route's amount, polarity and destination are set; the level (or the spectral centroid, for the filter) is read over time:
its period gives the rate, its range the depth, the way it moves the shape and polarity.
Output: calibration/probe_lfo.json and a printed table.
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import test_render_calibration as H

SR = H.SR


def tree_with(lfo_params=None, curve=None, route=None, filt=None, base=None):
    header, base_t = H.load_preset("Keys - Loop")
    t = H.init_tree(base_t)
    if filt:
        t = H.with_filter(t, filt, 0.0, "MgL24")
    if lfo_params is not None or curve is not None:
        n = {"curveData": curve or {}, "pathData": {}, "plainParams": lfo_params if lfo_params is not None else "default"}
        if curve:
            n["curveDisplayName"] = "Custom"
        t["LFO0"] = n
    if route:
        dest_type, dest_param, dest_id, pid, amount, bip = route
        pp = {"kParamAmount": amount}
        if bip:
            pp["kParamBipolar"] = 1.0
        t["ModSlot0"] = {"destModuleID": dest_id, "destModuleParamID": pid, "destModuleParamName": dest_param, "destModuleTypeString": dest_type,
                         "plainParams": pp, "source": [6, 0]}
    return t


def level_env(x, win=0.01):
    m = x[0, int(0.2 * SR):int(3.0 * SR)]
    n = int(win * SR)
    k = len(m) // n
    return 20 * np.log10(np.sqrt((m[:k * n].reshape(k, n) ** 2).mean(axis=1)) + 1e-9), win


def centroid_env(x, win=0.02):
    m = x[0, int(0.2 * SR):int(3.0 * SR)]
    n = int(win * SR)
    f = np.fft.rfftfreq(2048, 1 / SR)
    out = []
    for i in range(0, len(m) - 2048, n):
        p = np.abs(np.fft.rfft(m[i:i + 2048] * np.hanning(2048))) ** 2
        out.append((f * p).sum() / (p.sum() + 1e-20))
    return np.array(out), win


def period_of(v, win):
    d = v - v.mean()
    ac = np.correlate(d, d, mode="full")[len(d) - 1:]
    lo, hi = int(0.02 / win), int(1.5 / win)
    if hi <= lo + 2 or ac[0] <= 0:
        return None
    k = lo + int(np.argmax(ac[lo:hi]))
    return k * win


def main():
    s = H.Serum()
    notes = [48]

    def play(t):
        return s.render(t, notes, 3.2, 3.1, vel=100)
    out = {}
    OSC = ("Oscillator", "kParamVolume", 0, 1)
    base = play(tree_with())
    e0, w = level_env(base)
    print(f"no LFO: level {e0.mean():.1f} dB (flat to {e0.max() - e0.min():.1f} dB)")
    print("\n--- LFO with every setting at its default, routed to oscillator volume, amount 100 (unipolar): what rate, shape, range?")
    for amt, bip in ((100, 0), (100, 1), (50, 0)):
        x = play(tree_with(route=OSC + (amt, bip)))
        e, w = level_env(x)
        per = period_of(e, w)
        out[f"default_vol_{amt}_{bip}"] = {"mean": float(e.mean()), "min": float(e.min()), "max": float(e.max()), "period": per}
        print(f"  amount {amt:3d} bipolar {bip}: level min {e.min():6.1f} max {e.max():6.1f} mean {e.mean():6.1f} dB   period {per if per is None else round(per, 3)} s")
    print("\n--- the same, rate 5 Hz set explicitly, default shape")
    for amt in (100, 50, 25):
        x = play(tree_with(lfo_params={"kParamRate": 5.0, "kParamBeatSync": 0.0, "kParamDefaultMode": 0.0}, route=OSC + (amt, 0)))
        e, w = level_env(x)
        per = period_of(e, w)
        print(f"  amount {amt:3d}: level min {e.min():6.1f} max {e.max():6.1f} dB   period {per if per is None else round(per, 3)} s")
        out[f"r5_vol_{amt}"] = {"min": float(e.min()), "max": float(e.max()), "period": per}
    print("\n--- the filter: base cutoff 0.5 (MgL24), LFO 5 Hz default shape, amount a unipolar / bipolar: spectral centroid range")
    for amt, bip in ((10, 0), (25, 0), (50, 0), (25, 1)):
        x = play(tree_with(lfo_params={"kParamRate": 5.0, "kParamBeatSync": 0.0, "kParamDefaultMode": 0.0}, route=("VoiceFilter", "kParamFreq", 0, 3, amt, bip), filt=0.5))
        c, w = centroid_env(x)
        c0 = np.median(centroid_env(play(tree_with(filt=0.5)))[0])
        print(f"  amount {amt:3d} bipolar {bip}: centroid min {c.min():7.0f} max {c.max():7.0f} Hz (no LFO: {c0:.0f})  = {12 * np.log2(c.min() / c0):+.1f} .. {12 * np.log2(c.max() / c0):+.1f} semitones")
        out[f"flt_{amt}_{bip}"] = {"min_st": float(12 * np.log2(c.min() / c0)), "max_st": float(12 * np.log2(c.max() / c0))}
    Path("calibration/probe_lfo.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
