#!/usr/bin/env python3
"""The engine's LFO depth for each kind of destination, as ONE global constant per kind.

  calibrate_lfo_scales.py BASE_DIR

Serum stores a route's depth as a fraction of the destination's range; the engine's modulation is in its own units, and the
conversion differs by kind (filter cutoff, oscillator level, wavetable position, pitch, pan). For each kind, a scale (0.125 to 2)
is applied to every route of that kind in every preset that has one, all presets are played against Serum's full chain on two
note sets, and the scale that brings them closest as a GROUP (full-chain loss plus how close the spectrum's motion is to
Serum's, summed) is taken. No preset is left out and none can veto: this is a unit conversion, not a per-preset fit.
Output: calibration/lfo_scales.json  {"cutoff": s, "level": s, ...}
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fit_chain as C
import fit_perceptual as F
import lfo_pick as LP
import post_fit as PF
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/calibrate_lfo_scales.log"
log = H.log
CLASS_OF = {13: "cutoff", 3: "level", 4: "level", 15: "level", 16: "level", 19: "level", 1: "pos", 2: "pos", 18: "pos",
            5: "tune", 6: "tune", 11: "pan", 12: "pan"}
GRID = (0.125, 0.25, 0.5, 1.0, 2.0)


def scaled(state, scales):
    st = state
    for r in range(1, 9):
        dst = int(PF.getk(st, f"lr{r}_dst", 0))
        if PF.getk(st, f"lr{r}_src", 0) and CLASS_OF.get(dst) in scales:
            amt = PF.getk(st, f"lr{r}_amt", 64)
            st = PF.setk(st, f"lr{r}_amt", int(round(min(max(64 + (amt - 64) * scales[CLASS_OF[dst]], 0), 127))))
    return st


def main():
    base = Path(sys.argv[1])
    srcname = {H.d2m.ascii_name(p.stem): p.stem for p in H.PRESET_DIR.glob("*.SerumPreset")}
    presets = {}
    for f in sorted(base.glob("*.json")):
        st = json.loads(f.read_text(encoding="utf-8"))["state"]
        classes = {CLASS_OF.get(int(PF.getk(st, f"lr{r}_dst", 0))) for r in range(1, 9) if PF.getk(st, f"lr{r}_src", 0)} - {None}
        if classes:
            stem = srcname.get(f.stem, f.stem)
            it = C.load_items([stem])[0]
            header, tree = H.load_preset(stem)
            n5 = [n + 5 for n in H.sounding_notes(tree, stem)]
            r5 = LP.refs_for(stem, n5)
            e5 = F.sounding_end(r5)
            presets[f.stem] = {"state": st, "classes": classes, "it": it, "n5": n5, "f5": F.features(r5, e5), "e5": e5,
                               "m": (LP.motion(it["refs"]), LP.motion(r5))}
    log(f"{len(presets)} presets with LFO routes")

    def score(name, scales):
        p = presets[name]
        st = scaled(p["state"], scales)
        a = [H.run_cli(st, p["it"]["notes"], C.DUR, C.HOLD) for _ in range(3)]
        b = [H.run_cli(st, p["n5"], C.DUR, C.HOLD) for _ in range(3)]
        loss = (F.loss_of(p["it"]["ref_feat"], F.features(a, p["it"]["end"]))["loss"] + F.loss_of(p["f5"], F.features(b, p["e5"]))["loss"]) / 2
        mot = (abs(LP.motion(a) - p["m"][0]) + abs(LP.motion(b) - p["m"][1])) / 2
        return loss + 2 * mot

    scales = {c: 1.0 for c in ("cutoff", "level", "pos", "tune", "pan")}
    for cls in scales:
        names = [n for n, p in presets.items() if cls in p["classes"]]
        if not names:
            continue
        totals = {}
        for s in GRID:
            trial = dict(scales, **{cls: s})
            with ThreadPoolExecutor(max_workers=6) as pool:
                totals[s] = sum(pool.map(lambda n: score(n, trial), names))
            log(f"  {cls:7s} scale {s:5.3f}: summed score over {len(names)} presets {totals[s]:8.1f}")
        scales[cls] = min(totals, key=totals.get)
        log(f"  -> {cls} = {scales[cls]}  (at 1.0: {totals[1.0]:.1f}, best {totals[scales[cls]]:.1f})")
    Path("calibration/lfo_scales.json").write_text(json.dumps(scales, indent=1), encoding="utf-8")
    log(f"\nlfo_scales.json: {scales}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
