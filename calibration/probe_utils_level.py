#!/usr/bin/env python3
"""Serum's Utils Level Out knob as a gain: noise through one Utils node at several Level Out values (and unwritten), the level re the same noise without it. Writes calibration/probe_utils_level.json."""
import json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P

def main():
    serum, base = P.start()
    dry = P.render(serum, base)
    rms = lambda x: float(np.sqrt(np.mean(np.square(np.asarray(x, float)))))
    out = {}
    for lo in (None, 0.02, 0.05, 0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9, 1.0):
        pp = {} if lo is None else {"kParamLevelOut": lo}
        w = P.render(serum, P.fx_tree(base, "Utils", pp, 12))
        db = 20 * np.log10((rms(w) + 1e-9) / rms(dry))
        out[str(lo)] = db
        print("Level Out %-5s %+7.2f dB" % (lo, db), flush=True)
    Path("calibration/probe_utils_level.json").write_text(json.dumps(out, indent=1))
    os._exit(0)

main()
