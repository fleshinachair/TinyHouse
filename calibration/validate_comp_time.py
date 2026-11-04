#!/usr/bin/env python3
"""Gain reduction over time after a note starts: Serum's compressor against the engine's (through the converter), for several settings."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H

SR = H.SR
W = 0.02


def env_db(x, n=40):
    m = x[0]
    k = int(W * SR)
    return np.array([10 * np.log10((m[i * k:(i + 1) * k] ** 2).mean() + 1e-20) for i in range(n)])


def main():
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t)
    serum = H.Serum()
    cases = {"deep t.87 default": {"kParamThresh": 0.87}, "t.7 r8 att 20 rel 200": {"kParamThresh": 0.7, "kParamRatio": 8.0, "kParamAttack": 20.0, "kParamRelease": 200.0},
             "t.6 r4 default": {"kParamThresh": 0.6}}
    for name, p in cases.items():
        res = {}
        for who in ("serum", "engine"):
            for comp in (False, True):
                t = P.fx_tree(tree0, "Comp", p, 5) if comp else H.init_tree(base_t)
                t["Oscillator0"]["plainParams"] = {"kParamVolume": 0.6}
                if who == "serum":
                    if serum.audio_seconds > 40:
                        serum = H.Serum()
                    x = serum.render(t, [84], 1.2, 1.0)
                else:
                    x, _ = H.tablor_render(t, [84], 1.2, 1.0)
                res[(who, comp)] = env_db(x)
        gs = res[("serum", False)] - res[("serum", True)]
        ge = res[("engine", False)] - res[("engine", True)]
        print(name)
        print("  t(ms)   " + " ".join(f"{(i + 1) * 20:5d}" for i in range(0, 30, 2)))
        print("  serum GR" + " ".join(f"{gs[i]:5.1f}" for i in range(0, 30, 2)))
        print("  engine GR" + " ".join(f"{ge[i]:5.1f}" for i in range(0, 30, 2)), flush=True)
    os._exit(0)


main()
