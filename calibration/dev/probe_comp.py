#!/usr/bin/env python3
"""Serum's compressor (single band) on a held saw: output level against input level, for several threshold / ratio / makeup settings,
and the attack and release from the envelope of a pulsed saw. Output: calibration/probe_comp.json and a printed table."""
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
import probe_vfilter as V
import test_render_calibration as H

SR = H.SR
LEVELS = (0.12, 0.2, 0.3, 0.45, 0.65, 0.9)


def rms_db(x, a=0.6, b=1.2):
    m = x[:, int(a * SR):int(b * SR)]
    return 10 * np.log10((m ** 2).mean() + 1e-20)


def main():
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t)
    pool = V.SerumPool()
    out = {}

    def run(params, level, dur=1.3, hold=1.25):
        t = P.fx_tree(tree0, "Comp", params, 5) if params is not None else tree0
        t["Oscillator0"]["plainParams"] = {"kParamVolume": level}
        return pool.render(t, vel=127) if False else pool.s.render(t, [48], dur, hold) if pool.s.audio_seconds <= 36 else (setattr(pool, "s", H.Serum()) or pool.s.render(t, [48], dur, hold))
    base = {l: rms_db(run(None, l)) for l in LEVELS}
    print("input dB:", {l: round(v, 1) for l, v in base.items()}, flush=True)
    sets = {"default": {}, "thresh .2": {"kParamThresh": 0.2}, "thresh .5": {"kParamThresh": 0.5}, "thresh .8": {"kParamThresh": 0.8},
            "ratio 2": {"kParamRatio": 2.0}, "ratio 8": {"kParamRatio": 8.0}, "ratio 1e6": {"kParamRatio": 1e6},
            "makeup 6": {"kParamMakeup": 6.0}, "t.5 r8 mk0": {"kParamThresh": 0.5, "kParamRatio": 8.0, "kParamMakeup": 0.0}}
    print("%-14s" % "setting" + "".join("%9.1f" % base[l] for l in LEVELS) + "   (output dB at those input dB)")
    for name, p in sets.items():
        row = [rms_db(run(p, l)) for l in LEVELS]
        out[name] = row
        print("%-14s" % name + "".join("%9.1f" % v for v in row), flush=True)
    out["input"] = [base[l] for l in LEVELS]
    Path("calibration/probe_comp.json").write_text(json.dumps(out, indent=1))
    os._exit(0)


main()
