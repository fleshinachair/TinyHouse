#!/usr/bin/env python3
"""The Filter FX's Drive and Resonance as Serum plays them: white noise through one Filter FX, transfer in dB at half-octave points from 50 Hz
to 12.8 kHz.  Cases: the default type (6 dB/octave) over resonance 0..100 at drive 0 and over drive 0..100 at resonance 0; MgL18 and L12 over
drive.  The first run (fxfilter_drive_probe.json, level 0.75 and 0.3) showed the drive is a plain gain, independent of the level.
Output: calibration/fxfilter_drive_probe2.json
"""
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

OUT = ROOT / "calibration/fxfilter_drive_probe2.json"
TYPE_FILTER = 8
GRID2 = [round(50 * 2 ** (k / 2)) for k in range(17)]


def tf2(dry, wet):
    f, p0 = P.psd(dry)
    _, p1 = P.psd(wet)
    return [round(float(10 * np.log10(p1[(f > c / 1.19) & (f < c * 1.19)].sum() / p0[(f > c / 1.19) & (f < c * 1.19)].sum())), 1) for c in GRID2]


def main():
    serum, base = P.start()
    dry = P.render(serum, base)
    res = {"grid": GRID2}
    cases = []
    for freq in (0.7,):
        for reso in (0, 10, 20, 35, 50, 70, 100):
            cases.append(("default", freq, reso, 0))
        for drive in (0, 10, 25, 50, 75, 100):
            cases.append(("default", freq, 0, drive))
    for ty in ("MgL18", "L12"):
        for drive in (0, 10, 25, 50, 100):
            cases.append((ty, 0.7, 10, drive))
    for ty, freq, reso, drive in cases:
        p = {"kParamFreq": freq, "kParamReso": float(reso), "kParamDrive": float(drive)}
        if ty != "default":
            p["kParamType"] = ty
        g = tf2(dry, P.render(serum, P.fx_tree(base, "Filter", p, TYPE_FILTER)))
        key = f"{ty}|f{freq}|r{reso}|d{drive}"
        res[key] = g
        print(key.ljust(22), " ".join("%6.1f" % x for x in g), flush=True)
    OUT.write_text(json.dumps(res, indent=1))
    print("done", flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
