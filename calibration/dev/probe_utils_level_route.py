#!/usr/bin/env python3
"""A route (macro at 100 %) into Serum's Utils Level Out: where does it add, in the knob (0..1, gain = (L / 0.5)^2) or in dB? Level re the unrouted Utils. Writes calibration/probe_utils_level_route.json."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import probe_fx_macro as PM

def main():
    serum, base = P.start()
    dry = P.render(serum, base)
    rms = lambda x: float(np.sqrt(np.mean(np.square(np.asarray(x, float)))))
    out = {}
    for lo in (0.25, 0.5):
        for amt in (-40.0, 20.0, 40.0, 60.0, 100.0):
            if lo + amt / 100.0 <= 0.0:
                continue
            t = PM.with_route(P.fx_tree(base, "Utils", {"kParamLevelOut": lo}, 12), "Utils", "kParamLevelOut", amt, dest_id=0)
            db = 20 * np.log10((rms(P.render(serum, t)) + 1e-9) / rms(dry))
            L = min(max(lo + amt / 100.0, 0.0), 1.0)
            print("L %.2f amount %+5.0f: %+7.2f dB   (knob-domain add -> L %.2f = %+6.2f dB)" % (lo, amt, db, L, 40 * np.log10(max(L, 1e-4) / 0.5) / 2 * 1.0 if L > 0 else -99), flush=True)
            out[f"{lo}|{amt}"] = db
    Path("calibration/probe_utils_level_route.json").write_text(json.dumps(out, indent=1))
    os._exit(0)

main()
