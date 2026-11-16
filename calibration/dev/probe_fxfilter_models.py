#!/usr/bin/env python3
"""Serum's Filter FX models other than the low-pass / high-pass ladders, as a magnitude response: white noise through one Filter FX of each model, transfer in dB on a quarter-octave
grid (40 Hz to 16 kHz), at Freq 0.4 / 0.55 / 0.7 with Reso 0 and at Freq 0.55 with Reso 50 / 100 (Drive and Wet unwritten, Wet 100).  The shape is read against the cutoff the preset states (fxmap.serum_filter_hz).
Output: calibration/fxfilter_models_probe.json {"grid": [...], "<model>|f<freq>|r<reso>": [dB, ...]}.  Usage: probe_fxfilter_models.py [model ...]"""
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

OUT = ROOT / "calibration/fxfilter_models_probe.json"
MODELS = ["LadderMg", "BandReject", "B24", "B12", "LB12", "LH12", "LPH12", "LPH24", "LNH24", "LBH24", "LN12", "NN12", "N12", "N24", "BN12", "PN12", "PP12", "P12", "PZ_SVF", "HP12", "HEQ6", "HEQ12", "LadderEMS", "ExpBPF"]
GRID = [round(40 * 2 ** (k / 4)) for k in range(37)]


def tf(dry, wet):
    f, p0 = P.psd(dry, nfft=32768)
    _, p1 = P.psd(wet, nfft=32768)
    out = []
    for c in GRID:
        m = (f > c / 2 ** 0.125) & (f < c * 2 ** 0.125)
        out.append(round(float(10 * np.log10(p1[m].sum() / p0[m].sum())), 1))
    return out


def main():
    models = [a for a in sys.argv[1:] if not a.startswith("-")] or MODELS
    serum, base = P.start()
    dry = P.render(serum, base, 4.0)
    res = json.loads(OUT.read_text()) if OUT.exists() else {}
    res["grid"] = GRID
    for ty in models:
        for freq, reso in ((0.4, 0), (0.55, 0), (0.7, 0), (0.55, 50), (0.55, 100)):
            p = {"kParamFreq": freq, "kParamReso": float(reso), "kParamType": ty}
            g = tf(dry, P.render(serum, P.fx_tree(base, "Filter", p, 8), 4.0))
            res[f"{ty}|f{freq}|r{reso}"] = g
            print(f"{ty}|f{freq}|r{reso}".ljust(22), " ".join("%5.0f" % x for x in g[::3]), flush=True)
        OUT.write_text(json.dumps(res, indent=1))
    print("done", flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
