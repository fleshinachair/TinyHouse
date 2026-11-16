#!/usr/bin/env python3
"""Serum's distortion drive (per mode) -> the engine's distortion drive, from the harmonics Serum was measured to make.

calibration/probe_dist.json holds, for each Serum mode and drive, the 2nd/3rd/5th harmonic against the fundamental for a sine.
The engine's stage (fx.h processDist: gain g = 1 + 14 a^2 into a tanh / biased tanh / clip, scaled 1/sqrt(g)) is evaluated here on
the same sine, and for every (mode, Serum drive) the engine drive whose 2nd and 3rd harmonics are closest (floored at -60 dB, where
nothing is audible) is taken. Output: calibration/dist_scales.json  {mode: {"drives": [...], "pots": [...], "engine_mode": n}}
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
A = 0.2                      # the sine's peak reaching the stage: the probe's -18 dB rms, 1.414x, and the master gain's ~0.87
ENGINE_MODE = {"None": 0, "kSoftClip": 0, "kTapeSat": 0, "kSineShaper": 0, "kAsym": 1, "kDiode2": 1, "kOverdrive": 1, "kHardClip": 2}


def engine_harm(pot, mode, n=1 << 14):
    a = pot / 127.0
    g = 1.0 + 14.0 * a * a
    k = 1.0 / math.sqrt(g)
    x = A * np.sin(2 * np.pi * 8 * np.arange(n) / n)                 # 8 cycles in the window: harmonics land on exact bins
    if mode == 1:
        b = 0.25
        y = (np.tanh(g * (x + b)) - math.tanh(g * b)) * k
    elif mode == 2:
        y = np.clip(g * x, -1, 1) * k
    else:
        y = np.tanh(g * x) * k
    sp = np.abs(np.fft.rfft(y * np.hanning(n)))
    f1 = sp[8] + 1e-12
    return [20 * np.log10(sp[8 * h] / f1 + 1e-9) for h in (2, 3)]


def main():
    probe = json.loads((ROOT / "calibration/probe_dist.json").read_text(encoding="utf-8"))
    drives = [10, 25, 50, 75, 100]
    out = {}
    for md, em in ENGINE_MODE.items():
        pots = []
        for d in drives:
            h = probe[f"{md}_{d}"]["h"]
            target = [max(h[0], -60.0), max(h[1], -60.0)]
            best = (1e9, 0)
            for pot in range(0, 128):
                e = engine_harm(pot, em)
                err = abs(max(e[0], -60) - target[0]) + abs(max(e[1], -60) - target[1])
                if err < best[0]:
                    best = (err, pot)
            pots.append(best[1])
        out["default" if md == "None" else md] = {"drives": drives, "pots": pots, "engine_mode": em}
        print(f"{md:12s} engine mode {em}:  Serum drive {drives}  ->  engine drive pot {pots}")
    (ROOT / "calibration/dist_scales.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
