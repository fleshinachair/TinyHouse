#!/usr/bin/env python3
"""Make the dry (FX-stripped) Serum reference render, 3 x 2.0 s, for every preset that lacks one and
that Serum can open. Same cache and key as score_patch, so fit_filter.py and the scorer share them.
One process: the plugin renders about 1200 s of audio, and this stays under it.
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import numpy as np
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/fill_refs.log"
log = H.log


def main():
    names = sorted(p.stem for p in H.PRESET_DIR.glob("*.SerumPreset"))
    holder = []
    serum = lambda: holder[0] if holder else (holder.append(H.Serum()) or holder[0])
    made = skipped = 0
    for n in names:
        header, tree = H.load_preset(n)
        if not H.usable(tree):
            log(f"  {n:30s} skipped: Serum cannot open one of its wavetables")
            skipped += 1
            continue
        notes = H.sounding_notes(tree, n)
        path = H.OUT / "refs" / f"{H.re.sub(r'[^A-Za-z0-9]+', '_', n)}_{'_'.join(map(str, notes))}_3x2.0.npy"
        if path.exists():
            continue
        try:
            H.reference(serum, n, H.strip_fx(tree), notes, 3, 2.0, 1.5)
        except RuntimeError as e:
            log(f"  {n:30s} FAILED: {str(e)[:80]}")
            if holder and holder[0].audio_seconds > 1100:
                log("render limit reached; run again for the rest")
                break
            continue
        made += 1
        log(f"  {n:30s} rendered ({holder[0].audio_seconds:.0f} s of Serum audio so far)")
    log(f"done: {made} rendered, {skipped} not renderable")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
