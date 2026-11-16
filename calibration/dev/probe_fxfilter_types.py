#!/usr/bin/env python3
"""The low-pass Filter FX types' shape as Serum plays it: white noise through one Filter FX of each type (Reso and Drive unwritten), transfer in dB at
half-octave points from 50 Hz to 12.8 kHz, at three cutoffs to see that the shape scales with the cutoff.   Output: calibration/fxfilter_type_probe.json (--hp: the high-pass types, fxfilter_type_probe_hp.json)
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import fx_probe as P
from probe_fxfilter_drive import GRID2, tf2

HP = "--hp" in sys.argv
OUT = ROOT / ("calibration/fxfilter_type_probe_hp.json" if HP else "calibration/fxfilter_type_probe.json")


def main():
    serum, base = P.start()
    dry = P.render(serum, base)
    res = {"grid": GRID2}
    for ty in (("H24", "H18", "H12", "H6") if HP else ("MgL24", "L24", "MgL18", "L18", "MgL12", "L12", "MgL6", "L6")):
        for freq in (0.4, 0.55, 0.7):
            p = {"kParamFreq": freq, "kParamType": ty}
            g = tf2(dry, P.render(serum, P.fx_tree(base, "Filter", p, 8)))
            res[f"{ty}|f{freq}"] = g
            print(f"{ty}|f{freq}".ljust(14), " ".join("%6.1f" % x for x in g), flush=True)
    OUT.write_text(json.dumps(res, indent=1))
    print("done", flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
