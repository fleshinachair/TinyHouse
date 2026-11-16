#!/usr/bin/env python3
"""What type a voice filter is when its Type is not written: noise through Filter 2 (balance 100) and through Filter 1, each with no type, against
the same filters at explicit types. Output: calibration/filter2_default_probe.json"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import fx_probe as P
from probe_fxfilter_drive import GRID2, tf2
from probe_filter2 import tree_with


def main():
    serum, base = P.start()
    OFF = {"kParamEnable": 0.0}
    dry = P.render(serum, tree_with(base, OFF, OFF, {"kParamRoutingDest": "kRoutingDestMaster"}))
    res = {"grid": GRID2}
    to_f2 = {"kParamRoutingDest": "kRoutingDestFilter", "kParamFilterBalance": 100.0}
    to_f1 = {"kParamRoutingDest": "kRoutingDestFilter"}
    rows = [("F2 unwritten type", OFF, {"kParamEnable": 1.0, "kParamFreq": 0.5}, to_f2),
            ("F1 unwritten type", {"kParamEnable": 1.0, "kParamFreq": 0.5}, OFF, to_f1)]
    for ty in ("MgL24", "MgL18", "MgL12", "MgL6", "L24"):
        rows.append((f"F2 {ty}", OFF, {"kParamEnable": 1.0, "kParamFreq": 0.5, "kParamType": ty}, to_f2))
    for ty in ("MgL12", "L12", "LN12", "MgL24"):
        rows.append((f"F1 {ty}", {"kParamEnable": 1.0, "kParamFreq": 0.5, "kParamType": ty}, OFF, to_f1))
    for name, f1, f2, route in rows:
        g = tf2(dry, P.render(serum, tree_with(base, f1, f2, route)))
        res[name] = g
        print(name.ljust(20), " ".join("%6.1f" % x for x in g), flush=True)
    (ROOT / "calibration/filter2_default_probe.json").write_text(json.dumps(res, indent=1))
    print("done", flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
