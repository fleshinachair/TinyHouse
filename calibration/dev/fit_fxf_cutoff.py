#!/usr/bin/env python3
"""Merge probe_fxf_static.py's per-model outputs into calibration/fxf_cutoff.json, which deephouse2move.apply_filter2 reads:
{serum model: {"engine": the engine model whose type stands in, "corr": [[cutoff, semitones]]}}. The correction is what the engine's Filter FX cutoff pot needs on top of
the converter's formula (cutoff x knob span + offset - 8 + the voice-filter delta) for its -10 dB point to land where Serum's does; cutoffs under 0.2 are left out
(the point falls at the first harmonic and the reading is noise), so the table is flat below its first entry.
Usage: fit_fxf_cutoff.py   (after probe_fxf_static.py <model> for each model; the default type is probed as an L12: FXF_ENGINE=L12 probe_fxf_static.py default)"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import json
from pathlib import Path
CAL = Path(__file__).resolve().parents[1]
SRC = {"default": ("L12", "default_as_L12"), "MgL24": ("MgL24", "MgL24"), "MgL18": ("MgL18", "MgL18"), "MgL12": ("MgL12", "MgL12"), "L12": ("L12", "L12")}
out = {}
for model, (engine, stem) in SRC.items():
    f = CAL / f"probe_fxf_static_{stem}.json"
    if not f.exists():
        print("missing", f.name); continue
    d = json.loads(f.read_text(encoding="utf-8"))
    rows = sorted((float(c), round(v["correction"], 1)) for c, v in d.items() if float(c) >= 0.2)
    out[model] = {"engine": engine, "corr": [[c, x] for c, x in rows]}
    print(f"{model:8s} as {engine:6s} " + " ".join(f"{c:.1f}:{x:+.1f}" for c, x in rows))
(CAL / "fxf_cutoff.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
