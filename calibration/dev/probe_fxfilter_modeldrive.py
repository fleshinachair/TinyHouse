#!/usr/bin/env python3
"""Filter FX Drive on the non-ladder models: B24 and PP12 at Freq 0.55 / Reso 0, Drive 0 / 50 / 100, Wet 100, transfer in dB (the same grid as probe_fxfilter_models.py). Is Drive a plain gain here as on the
ladders (fxmap.FX_FILTER_DRIVE_DB)?  Output: calibration/fxfilter_modeldrive_probe.json"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import fx_probe as P
from probe_fxfilter_models import tf
serum, base = P.start()
dry = P.render(serum, base, 4.0)
res = {}
for ty in ("B24", "PP12", "BandReject"):
    for drive in (0, 50, 100):
        p = {"kParamFreq": 0.55, "kParamReso": 0.0, "kParamType": ty, "kParamDrive": float(drive)}
        g = tf(dry, P.render(serum, P.fx_tree(base, "Filter", p, 8), 4.0)); res[f"{ty}|d{drive}"] = g
        print(f"{ty}|d{drive}".ljust(14), " ".join("%5.0f" % x for x in g[::3]), flush=True)
Path("calibration/fxfilter_modeldrive_probe.json").write_text(json.dumps(res, indent=1))
sys.stdout.flush(); os._exit(0)
