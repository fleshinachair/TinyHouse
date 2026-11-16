#!/usr/bin/env python3
"""Serum's second voice filter and the per-source Filter Balance: white noise (the noise oscillator) through Filter 1 and Filter 2, transfer in dB at
half-octave points, for a few balances and for the balance left unwritten. Filter 1 is an MgL24 at 0.5, Filter 2 an MgL12 at 0.3 (so the two curves
differ at every point); noise routed with balance 0..100 and unwritten, with a low-pass in one filter and a transparent other.
Output: calibration/filter2_probe.json
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import fx_probe as P
from probe_fxfilter_drive import GRID2, tf2

OUT = ROOT / "calibration/filter2_probe.json"


def tree_with(base, f1, f2, route):
    t = copy.deepcopy(base)
    t["VoiceFilter0"] = {"plainParams": f1}
    t["VoiceFilter1"] = {"plainParams": f2}
    t["RoutingSlot3"] = {"plainParams": route}
    return t


def main():
    serum, base = P.start()
    OFF = {"kParamEnable": 0.0}
    dry = P.render(serum, tree_with(base, OFF, OFF, {"kParamRoutingDest": "kRoutingDestMaster"}))
    res = {"grid": GRID2}
    # two arrangements: the low-pass in Filter 1 and a transparent Filter 2, and the other way round (the weight of the transparent one is what
    # the stop band of the low-pass leaves)
    LP = {"kParamEnable": 1.0, "kParamFreq": 0.5, "kParamType": "MgL24"}
    OPEN = {"kParamEnable": 1.0, "kParamFreq": 1.0, "kParamType": "MgL6"}
    for arr, f1, f2 in (("LP1_OPEN2", LP, OPEN), ("OPEN1_LP2", OPEN, LP)):
        cases = [("balance %g" % b, {"kParamRoutingDest": "kRoutingDestFilter", "kParamFilterBalance": b}) for b in (0.0, 10.0, 25.0, 50.0, 75.0, 90.0, 100.0)]
        cases.append(("balance unwritten", {"kParamRoutingDest": "kRoutingDestFilter"}))
        for name, route in cases:
            g = tf2(dry, P.render(serum, tree_with(base, f1, f2, route)))
            res[f"{arr}|{name}"] = g
            print(f"{arr}|{name}".ljust(30), " ".join("%6.1f" % x for x in g), flush=True)
    OUT.write_text(json.dumps(res, indent=1))
    print("done", flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
