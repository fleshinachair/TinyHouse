#!/usr/bin/env python3
"""Make the full-chain (FX on) Serum reference render, 2 x 4.0 s with a 1.0 s hold, for every preset
that lacks one and that Serum can open. Same cache and key as autopsy.py (name + "_fx", 2, 4.0, 1.0),
so the 14 already there are reused. One process: the plugin renders about 1200 s of audio and
this stays well under it (56 x 8 s).
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter"))
os.chdir(ROOT)
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/fill_fx_refs.log"
log = H.log


def main():
    holder = []
    serum = lambda: holder[0] if holder else (holder.append(H.Serum()) or holder[0])
    made = 0
    for n in sorted(p.stem for p in H.PRESET_DIR.glob("*.SerumPreset")):
        header, tree = H.load_preset(n)
        if not H.usable(tree):
            log(f"  {n:30s} skipped: Serum cannot open one of its wavetables")
            continue
        notes = H.sounding_notes(tree, n)
        path = H.OUT / "refs" / f"{H.re.sub(r'[^A-Za-z0-9]+', '_', n + '_fx')}_{'_'.join(map(str, notes))}_2x4.0.npy"
        if path.exists():
            continue
        try:
            H.reference(serum, n + "_fx", tree, notes, 2, 4.0, 1.0)
            made += 1
            log(f"  {n:30s} rendered")
        except RuntimeError as e:
            log(f"  {n:30s} FAILED: {str(e)[:80]}")
        if holder and holder[0].audio_seconds > 1100:
            log("render limit reached; run again for the rest")
            break
    log(f"{made} new full-chain references")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
