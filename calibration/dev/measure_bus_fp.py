#!/usr/bin/env python3
"""Measure the fingerprints of the FX bus racks (and main racks) of the named presets in a folder: measure_bus_fp.py DIR name ... (the importer does this itself; this is for a pack that is converted
without it)."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import import_pack as IP
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file
d2m = H.d2m
src, names = Path(sys.argv[1]), [n.lower() for n in sys.argv[2:]]
meas = []
for p in sorted(src.rglob("*.SerumPreset")):
    nm = d2m.ascii_name(p.stem)[:40]
    if names and nm.lower() not in names:
        continue
    h, t = decode_preset_file(p)
    for attempt in range(3):
        try:
            IP.measure_fingerprints(nm, t, meas); print("measured", nm, flush=True); break
        except RuntimeError as e:
            print("  retry", nm, str(e)[:50], flush=True); meas.clear()
os._exit(0)
