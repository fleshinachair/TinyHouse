#!/usr/bin/env python3
"""The engine's multiband compressor (set by the converter from Serum settings) against Serum's measured mid-band curves."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import json
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


def rms_db(x, a=0.6, b=1.2):
    m = x[:, int(a * SR):int(b * SR)]
    return float(10 * np.log10((m ** 2).mean() + 1e-20))


def main():
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t, table="Analog/Basic Shapes.wav")
    c3 = json.loads((ROOT / "calibration/probe_mbcomp3.json").read_text())
    c2 = json.loads((ROOT / "calibration/probe_mbcomp2.json").read_text())
    L3 = (0.005, 0.01, 0.02, 0.04, 0.08, 0.15, 0.3, 0.5, 0.75, 1.0)
    L2 = (0.02, 0.04, 0.08, 0.15, 0.3, 0.5, 0.75, 1.0)
    MB = {"kParamMultiband": 1.0}
    cases = [("mb default", MB, c3, L3), ("mb UD1=143", MB | {"kParamThreshUD1": 143.0}, c3, L3)]
    for rb in (0.25, 0.5, 0.75, 1.0):
        cases.append((f"UD1=143 below {rb}", MB | {"kParamThreshUD1": 143.0, "kParamRatioBelow": rb}, c3, L3))
    for u in (0, 40, 80, 120):
        cases.append((f"mb UD1={u}", MB | {"kParamThreshUD1": float(u)}, c2, L2))
    cases.append(("mb UD1=80 ratio 8", MB | {"kParamThreshUD1": 80.0, "kParamRatio": 8.0}, c2, L2))
    cases.append(("mb UD1=80 ratio 2", MB | {"kParamThreshUD1": 80.0, "kParamRatio": 2.0}, c2, L2))
    cases.append(("mb UD1=80 below .5", MB | {"kParamThreshUD1": 80.0, "kParamRatioBelow": 0.5}, c2, L2))
    tot = []
    for name, p, cache, levels in cases:
        row = []
        for l in levels:
            t = P.fx_tree(tree0, "Comp", p, 5)
            t["Oscillator0"]["plainParams"] = {"kParamVolume": l}
            x, m = H.tablor_render(t, [72], 1.3, 1.25)
            row.append(rms_db(x))
        # the engine's own level offset against Serum (no comp) is removed by comparing the change against its own no-comp render
        base = []
        for l in levels:
            t = H.init_tree(base_t, table="Analog/Basic Shapes.wav")
            t["Oscillator0"]["plainParams"] = {"kParamVolume": l}
            x, _ = H.tablor_render(t, [72], 1.3, 1.25)
            base.append(rms_db(x))
        d_e = np.array(row) - np.array(base)
        d_s = np.array(cache[name]) - np.array(cache["input"])
        e = float(np.sqrt(((d_e - d_s) ** 2).mean()))
        tot.append(e)
        print(f"{name:20s} rms diff {e:5.2f} dB | serum {' '.join(f'{v:+5.1f}' for v in d_s)} | engine {' '.join(f'{v:+5.1f}' for v in d_e)}", flush=True)
    print("mean %.2f dB" % np.mean(tot))
    os._exit(0)


main()
