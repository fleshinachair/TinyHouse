#!/usr/bin/env python3
"""Serum's multiband compressor: where the band edges are and what the per-band gains do. Noise through the compressor with Multiband on, one
band's Gain raised at a time; the transfer is the PSD ratio against the same noise with no compressor. Output: calibration/probe_mbcomp.json."""
import json
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
EDGES = [50, 100, 150, 200, 300, 400, 600, 800, 1200, 1600, 2400, 3200, 5000, 8000, 12000, 16000]


def band_db(x):
    f, p = P.psd(x)
    out = []
    for lo, hi in zip(EDGES[:-1], EDGES[1:]):
        m = (f >= lo) & (f < hi)
        out.append(10 * np.log10(p[m].mean() + 1e-30))
    return np.array(out)


def main():
    _, base_t = H.load_preset("Bass - Gravity")
    base = P.noise_base(H.init_tree(base_t))
    serum = [H.Serum()]
    def render(params):
        if serum[0].audio_seconds > 36:
            serum[0] = H.Serum()
        t = P.fx_tree(base, "Comp", params, 5) if params is not None else base
        return serum[0].render(t, [48], 2.0, 1.9)
    ref = band_db(render(None))
    print("band edges Hz:", EDGES)
    out = {}
    cases = {"multiband default": {"kParamMultiband": 1.0, "kParamThresh": 0.1}, "single default": {"kParamThresh": 0.1}}
    for b in range(3):
        cases[f"gain{b}=12"] = {"kParamMultiband": 1.0, "kParamThresh": 0.1, f"kParamGain{b}": 12.0}
        cases[f"gain{b}=-12"] = {"kParamMultiband": 1.0, "kParamThresh": 0.1, f"kParamGain{b}": -12.0}
    for name, p in cases.items():
        d = band_db(render(p)) - ref
        out[name] = d.tolist()
        print(f"{name:20s}", " ".join(f"{v:+5.1f}" for v in d), flush=True)
    Path("calibration/probe_mbcomp.json").write_text(json.dumps(out, indent=1))
    os._exit(0)


main()
