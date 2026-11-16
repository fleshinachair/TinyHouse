#!/usr/bin/env python3
"""Static level law for the browser importer: one volume offset, fitted from the per-preset level fits of the Loopsy pack.

  fit_level_law.py     reads calibration/level_fits.json (the volume that puts the engine's full-chain RMS where Serum's is, measured per preset with Serum) and writes
                       calibration/level_law.json

The importer has no Serum, so it cannot match each preset's loudness. What the data allows: the converter's own starting volume runs hot against Serum by a median
2.7 dB (40 log10 of fitted / starting volume), with a spread of 5 dB that is NOT explained by the preset's engine parameters (a ridge regression on 18 of them,
leave-one-out, does no better than the constant). So the law is the offset, and the rest is left to the user's ear (Volume on the Move UI / remote UI).
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import json
import math
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
fits = json.loads((ROOT / "calibration/level_fits.json").read_text(encoding="utf-8"))
d = [40 * math.log10(v["volume"] / v["start_volume"]) for v in fits.values() if v.get("volume") and v.get("start_volume")]
off = statistics.median(d)
out = {"offset_db": round(off, 2), "n": len(d), "spread_db": round(statistics.pstdev(d), 2),
       "note": "volume pot is squared: new = old * 10^(offset_db / 40); median of the PC pipeline's per-preset level corrections (Loopsy pack)"}
(ROOT / "calibration/level_law.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
print(out)
