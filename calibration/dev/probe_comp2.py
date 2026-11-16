#!/usr/bin/env python3
"""Serum's compressor static curve over a grid of threshold and ratio settings, fitted with the engine's own gain law (threshold, ratio,
6 dB soft knee, on the saw's peak = rms + 4.77 dB). Serum side cached in calibration/probe_comp2_serum.json. Output: probe_comp2.json."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import probe_vfilter as V
import test_render_calibration as H

SR = H.SR
LEVELS = (0.08, 0.12, 0.2, 0.3, 0.45, 0.65, 0.9, 1.0)
CREST_DB = 4.77
KNEE = 6.0
CACHE = ROOT / "calibration/probe_comp2_serum.json"


def rms_db(x, a=0.6, b=1.2):
    m = x[:, int(a * SR):int(b * SR)]
    return float(10 * np.log10((m ** 2).mean() + 1e-20))


def gr(x_db, T, R):
    over = x_db - T
    slope = 1 - 1 / R
    if over <= -KNEE / 2:
        return 0.0
    if over >= KNEE / 2:
        return slope * over
    k = over + KNEE / 2
    return slope * k * k / (2 * KNEE)


def fit(inp, out):
    best = (1e9, 0, 1)
    for T in np.arange(-70, 6, 0.5):
        for lr in np.arange(0, 2.01, 0.04):
            R = 10 ** lr
            err = sum((o - (i + CREST_DB - gr(i + CREST_DB, T, R) - CREST_DB - 0) - (0)) ** 2 for i, o in zip(inp, out))
            # output rms = input rms - gain reduction at the peak level (gain is applied to the whole waveform)
            err = sum((o - (i - gr(i + CREST_DB, T, R))) ** 2 for i, o in zip(inp, out))
            if err < best[0]:
                best = (err, T, R)
    return best


def main():
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t)
    holder = [H.Serum()]
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}

    def run(params, level):
        if holder[0].audio_seconds > 36:
            holder[0] = H.Serum()
        t = P.fx_tree(tree0, "Comp", params, 5) if params is not None else H.init_tree(base_t)
        t["Oscillator0"]["plainParams"] = {"kParamVolume": level}
        return rms_db(holder[0].render(t, [48], 1.3, 1.25))
    if "input" not in cache:
        cache["input"] = [run(None, l) for l in LEVELS]
        CACHE.write_text(json.dumps(cache))
    inp = cache["input"]
    print("input dB", [round(v, 1) for v in inp], flush=True)
    sets = {"default": {}}
    for t in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
        for r in (2.0, 4.0, 8.0, 20.0, 1e6):
            sets[f"t{t} r{r:g}"] = {"kParamThresh": t, "kParamRatio": r}
    for t in (0.3, 0.5, 0.7):
        sets[f"t{t} default-ratio"] = {"kParamThresh": t}
    for r in (2.0, 4.0, 8.0, 1e6):
        sets[f"default-thresh r{r:g}"] = {"kParamRatio": r}
    for t in (0.85, 0.9, 0.95):
        sets[f"t{t} r4"] = {"kParamThresh": t, "kParamRatio": 4.0}
    sets["t0.5 r4 below.75"] = {"kParamThresh": 0.5, "kParamRatio": 4.0, "kParamRatioBelow": 0.75}
    sets["t0.87 r4"] = {"kParamThresh": 0.87}
    sets["t0.87 r4 below.75"] = {"kParamThresh": 0.87, "kParamRatioBelow": 0.75}
    sets["t0.87 r4 below.5"] = {"kParamThresh": 0.87, "kParamRatioBelow": 0.5}
    sets["t0.87 below1.5"] = {"kParamThresh": 0.87, "kParamRatioBelow": 1.5}
    out = {}
    for name, p in sets.items():
        if name not in cache:
            cache[name] = [run(p, l) for l in LEVELS]
            CACHE.write_text(json.dumps(cache))
        e, T, R = fit(inp, cache[name])
        out[name] = {"T": T, "R": R, "rms_err_db": math.sqrt(e / len(inp))}
        print(f"{name:22s} T {T:6.1f} dB  R {R:7.2f}  (fit rms {math.sqrt(e / len(inp)):.2f} dB)  curve " + " ".join(f"{v:6.1f}" for v in cache[name]), flush=True)
    Path("calibration/probe_comp2.json").write_text(json.dumps(out, indent=1))
    os._exit(0)


if __name__ == "__main__":
    main()
