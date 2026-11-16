#!/usr/bin/env python3
"""For every preset, switch one translated feature off at a time and see whether the match to Serum's full-chain render gets better.
A feature whose removal helps many presets is a likely mistranslation. Output: calibration/ablate_all.json and a printed summary."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fit_chain as C
import fit_perceptual as F
import post_fit as PF
import test_render_calibration as H

SRC = Path(sys.argv[1])
src = {H.d2m.ascii_name(p.stem): p.stem for p in H.PRESET_DIR.glob("*.SerumPreset")}


def variants(st):
    def lfo_off(s):
        for r in range(1, 9):
            s = PF.setk(s, f"lr{r}_src", 0)
        return s

    def eq_off(s):
        for b in range(1, 5):
            s = PF.setk(s, f"eq{b}_type", 0)
        return s
    return {"LFO routes": lfo_off, "filter envelope": lambda s: PF.setk(s, "flt_env", 64), "compressor": lambda s: PF.setk(s, "comp_mix", 0),
            "EQ bands": eq_off, "width stage": lambda s: PF.setk(s, "fx_wide", 0), "distortion": lambda s: PF.setk(s, "dist_drive", 0),
            "reverb": lambda s: PF.setk(s, "rev_mix", 0), "delay": lambda s: PF.setk(s, "dly_mix", 0), "filter resonance": lambda s: PF.setk(s, "flt_res", 0),
            "filter drive": lambda s: PF.setk(s, "flt_drive", 0)}


def main():
    out = {}
    for f in sorted(SRC.glob("*.json")):
        n = f.stem
        if n == "Bass - A Classic":
            continue
        it = C.load_items([src.get(n, n)])
        if not it:
            continue
        it = it[0]
        st = json.loads(f.read_text(encoding="utf-8"))["state"]
        def loss(s):
            rs = [H.run_cli(s, it["notes"], C.DUR, C.HOLD) for _ in range(2)]
            return F.loss_of(it["ref_feat"], F.features(rs, it["end"]))["loss"]
        base = loss(st)
        row = {"base": base}
        for name, fn in variants(st).items():
            s2 = fn(st)
            if s2 != st:
                row[name] = loss(s2) - base
        out[n] = row
        print(n, {k: round(v, 1) for k, v in row.items()}, flush=True)
        Path("calibration/ablate_all.json").write_text(json.dumps(out, indent=1))
    os._exit(0)


main()
