#!/usr/bin/env python3
"""The engine's compressor attack and release times that reproduce Serum's gain-reduction trajectory, case by case.
Serum side: a held saw (note 84) through a compressor, gain reduction in 10 ms windows, onset and then release after a drop in level.
Engine side: the converter's translation with the attack / release pot swept. Prints the best engine time against Serum's setting."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import deephouse2move as d2m
import fx_probe as P
import test_render_calibration as H

SR = H.SR
W = 0.01
N = 50


def env_db(x, n=N):
    m = x[0]
    k = int(W * SR)
    return np.array([10 * np.log10((m[i * k:(i + 1) * k] ** 2).mean() + 1e-20) for i in range(n)])


def main():
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t)
    serum = [H.Serum()]
    def sr(t):
        if serum[0].audio_seconds > 40:
            serum[0] = H.Serum()
        return serum[0].render(t, [84], 1.2, 1.0)
    base = lambda: (lambda q: (q["Oscillator0"].__setitem__("plainParams", {"kParamVolume": 0.6}), q)[1])(H.init_tree(base_t))
    ref_s = env_db(sr(base()))
    x0, _ = H.tablor_render(base(), [84], 1.2, 1.0)
    ref_e = env_db(x0)
    if "--makeup" in sys.argv:
        cases = [("deep, attack 90, makeup 10.8", {"kParamThresh": 0.87, "kParamMakeup": 10.82}, 90.0)]
        t = (lambda q: (q["Oscillator0"].__setitem__("plainParams", {"kParamVolume": 0.6}), q)[1])(P.fx_tree(tree0, "Comp", cases[0][1], 5))
        xs = sr(t)
        x1, m1 = H.tablor_render(t, [84], 1.2, 1.0)
        es, ee = env_db(xs), env_db(x1)
        print("net gain (out - unprocessed input), dB, in 10 ms windows from the onset")
        print("serum  ", " ".join(f"{es[i] - ref_s[i]:5.1f}" for i in range(0, 30, 2)))
        print("engine ", " ".join(f"{ee[i] - ref_e[i]:5.1f}" for i in range(0, 30, 2)))
        os._exit(0)
    cases = [("deep, attack 90", {"kParamThresh": 0.87}, 90.0), ("deep, attack 30", {"kParamThresh": 0.87, "kParamAttack": 30.0}, 30.0),
             ("deep, attack 10", {"kParamThresh": 0.87, "kParamAttack": 10.0}, 10.0), ("moderate, attack 90", {"kParamThresh": 0.7, "kParamRatio": 8.0}, 90.0),
             ("moderate, attack 20", {"kParamThresh": 0.7, "kParamRatio": 8.0, "kParamAttack": 20.0}, 20.0),
             ("shallow, attack 90", {"kParamThresh": 0.6}, 90.0)]
    for name, p, A in cases:
        gs = ref_s - env_db(sr(P.fx_tree(tree0, "Comp", p, 5) | {} if False else (lambda q: (q["Oscillator0"].__setitem__("plainParams", {"kParamVolume": 0.6}), q)[1])(P.fx_tree(tree0, "Comp", p, 5))))
        t = (lambda q: (q["Oscillator0"].__setitem__("plainParams", {"kParamVolume": 0.6}), q)[1])(P.fx_tree(tree0, "Comp", p, 5))
        _, m = H.tablor_render(t, [84], 0.3, 0.2)
        best = None
        for ms in (3, 6, 10, 15, 20, 30, 40, 55, 75, 100):
            m2 = {"params": dict(m["params"]), "paths": m["paths"]}
            m2["params"]["comp_attack"] = round(min(max(math.log10(ms / 0.1) / 3, 0), 1) * 127)
            ge = ref_e - env_db(H.run_cli(d2m.blob(m2), [84], 1.2, 1.0))
            e = float(np.sqrt(((ge[:30] - gs[:30]) ** 2).mean()))
            if best is None or e < best[0]:
                best = (e, ms)
        print(f"{name:22s} final GR {np.median(gs[-10:]):5.1f} dB: best engine attack {best[1]:3d} ms (rms diff {best[0]:.2f} dB) = {best[1] / A:.2f} x Serum's setting", flush=True)
    os._exit(0)


main()
