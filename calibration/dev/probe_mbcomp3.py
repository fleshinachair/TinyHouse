#!/usr/bin/env python3
"""Serum's multiband compressor, mid band: a sine at 523 Hz at several levels through it, the output level against the input level for
several ThreshUD1 / Ratio / RatioBelow settings. Output: calibration/probe_mbcomp2.json and a printed table."""
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
import test_render_calibration as H

SR = H.SR
LEVELS = (0.005, 0.01, 0.02, 0.04, 0.08, 0.15, 0.3, 0.5, 0.75, 1.0)


def rms_db(x, a=0.6, b=1.2):
    m = x[:, int(a * SR):int(b * SR)]
    return float(10 * np.log10((m ** 2).mean() + 1e-20))


def main():
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t, table="Analog/Basic Shapes.wav")
    serum = [H.Serum()]
    def run(params, level, note=72):
        if serum[0].audio_seconds > 36:
            serum[0] = H.Serum()
        t = P.fx_tree(tree0, "Comp", params, 5) if params is not None else tree0
        t["Oscillator0"]["plainParams"] = {"kParamVolume": level}
        return rms_db(serum[0].render(t, [note], 1.3, 1.25))
    inp = [run(None, l) for l in LEVELS]
    print("input dB            ", " ".join(f"{v:6.1f}" for v in inp), flush=True)
    MB = {"kParamMultiband": 1.0}
    sets = {"mb default": MB, "mb UD1=143": MB | {"kParamThreshUD1": 143.0}}
    for rb in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 4.0):
        sets[f"UD1=143 below {rb}"] = MB | {"kParamThreshUD1": 143.0, "kParamRatioBelow": rb}
    sets["UD1=143 below 2, ratio 1.0"] = MB | {"kParamThreshUD1": 143.0, "kParamRatioBelow": 2.0, "kParamRatio": 1.0}
    out = {"input": inp}
    for name, p in sets.items():
        row = [run(p, l) for l in LEVELS]
        out[name] = row
        print(f"{name:20s}", " ".join(f"{v:6.1f}" for v in row), flush=True)
    Path("calibration/probe_mbcomp3.json").write_text(json.dumps(out, indent=1))
    os._exit(0)


main()
