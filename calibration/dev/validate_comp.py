#!/usr/bin/env python3
"""The engine's compressor, set by the converter from Serum Comp settings, against Serum's measured output curves (probe_comp2_serum.json)."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import probe_comp2 as C2
import test_render_calibration as H

LEVELS, SR = C2.LEVELS, H.SR


def main():
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t)
    cache = json.loads((ROOT / "calibration/probe_comp2_serum.json").read_text())
    inp = cache["input"]
    def eng(params):
        row = []
        for l in LEVELS:
            t = P.fx_tree(tree0, "Comp", params, 5) if params is not None else H.init_tree(base_t)
            t["Oscillator0"]["plainParams"] = {"kParamVolume": l}
            x, m = H.tablor_render(t, [48], 1.3, 1.25)
            row.append(C2.rms_db(x))
        return row, m
    none, _ = eng(None)
    print("no comp: serum", " ".join(f"{v:6.1f}" for v in inp), "\n         engine", " ".join(f"{v:6.1f}" for v in none), "\n   offset", " ".join(f"{a - b:+6.1f}" for a, b in zip(none, inp)))
    tot = []
    for name in ("default", "t0.5 r8", "t0.6 r4", "t0.6 r20", "t0.7 r2", "t0.7 r8", "t0.8 r4", "t0.8 r20", "t0.5 r1e+06", "t0.7 r1e+06"):
        p = {} if name == "default" else dict(zip(("kParamThresh", "kParamRatio"), (float(name.split()[0][1:]), float(name.split()[1][1:]))))
        row, m = eng(p)
        # compare the compression done (level change against no comp), which removes the engine's overall level offset
        d_e = [a - b for a, b in zip(row, none)]
        d_s = [a - b for a, b in zip(cache[name], inp)]
        err = np.array(d_e) - np.array(d_s)
        tot.append(np.sqrt((err ** 2).mean()))
        print(f"{name:14s} serum GR " + " ".join(f"{v:5.1f}" for v in d_s) + "\n               engine GR " + " ".join(f"{v:5.1f}" for v in d_e) + f"   rms diff {tot[-1]:.2f} dB  (pots thr {m['params']['comp_thresh']} ratio {m['params']['comp_ratio']})")
    print("mean rms difference in gain reduction: %.2f dB" % np.mean(tot))
    os._exit(0)


main()
