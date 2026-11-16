#!/usr/bin/env python3
"""Per-preset closed-loop fit of the unison width: one scale on the preset's three spread knobs.

  fit_width.py [name ...]

The panning law is right on average and wrong for a given patch: measured over all 70 presets the
side/mid error of a stack is +3.7 dB for 2-3 voices and +0.9 dB for 6-9, but single presets sit
anywhere from 14 dB too narrow to 10 dB too wide, and not in step with voice count, so no law of
voice count can fix them. Each preset's Serum render (dry, cached) gives its side/mid; the engine's
spread knobs are scaled down until the engine's matches. Narrowing only: spread is already full
width, so a preset that is too narrow cannot be helped here. Unison phases are random on both
sides, so levels are pooled in power over several renders and a fit must beat the noise (about
1.5 dB) to be kept. Output: calibration/width_fits.json.
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fit_perceptual as F
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/fit_width.log"
log = H.log
d2m, SR = H.d2m, H.SR
OUT = H.OUT / "width_fits.json"
MIN_ERROR_DB = 2.5        # a preset closer than this to Serum is left alone
MIN_GAIN_DB = 1.5         # and a fit must bring it at least this much closer
RENDERS = 6


def pooled_db(runs, end):
    """Side/mid in dB, pooled in power over the renders: a single render's dB swings with the unison
    phases, and averaging dB lets one lucky cancellation dominate."""
    s = m = 0.0
    for r in runs:
        x = r[:, int(0.01 * SR):int(end * SR)]
        s += (((x[0] - x[1]) / 2) ** 2).sum()
        m += (((x[0] + x[1]) / 2) ** 2).sum()
    return float(10 * np.log10(max(s, 1e-20) / max(m, 1e-20)))


def render(it, m, scale):
    q = copy.deepcopy(m)
    for i in (1, 2, 3):
        if q["params"][f"wt{i}_uni"] > 1:
            q["params"][f"wt{i}_spread"] = int(round(m["params"][f"wt{i}_spread"] * scale))
    blob = d2m.blob(q)
    return pooled_db(list(F.POOL.map(lambda _: H.run_cli(blob, it["notes"], 2.0, 1.5), range(RENDERS))), it["end"])


def fit_item(it):
    m = d2m.map_to_tablor(d2m.to_vital(d2m.physical(it["tree"], it["header"])), {}, H.SERUM_TABLES, H.WT_OUT, "Deep House")
    P = m["params"]
    stacked = [i for i in (1, 2, 3) if P[f"wt{i}_uni"] > 1 and P[f"wt{i}_level"] > 0 and P[f"wt{i}_spread"] > 0]
    if not stacked:
        return None
    target = pooled_db(it["refs"], it["end"])
    before = render(it, m, 1.0)
    if before - target < MIN_ERROR_DB:
        return {"serum_db": target, "before_db": before, "kept": False, "why": "already close, or too narrow to fix by narrowing"}
    lo, hi = 0.05, 1.0
    for _ in range(7):
        mid = (lo + hi) / 2
        if render(it, m, mid) > target:
            hi = mid
        else:
            lo = mid
    scale = (lo + hi) / 2
    after = render(it, m, scale)
    start = {f"wt{i}_spread": P[f"wt{i}_spread"] for i in (1, 2, 3)}
    fit = {k: (int(round(v * scale)) if P[f"wt{k[2]}_uni"] > 1 else v) for k, v in start.items()}
    rec = {"serum_db": target, "before_db": before, "after_db": after, "scale": scale, "start": start, "fit": fit,
           "kept": bool(abs(after - target) <= abs(before - target) - MIN_GAIN_DB)}
    return rec


def main():
    d2m.APPLY_FILTER_FITS = False
    d2m.APPLY_WIDTH_FITS = False
    given = [a for a in sys.argv[1:] if not a.startswith("--")]
    names = given or sorted(p.stem for p in H.PRESET_DIR.glob("*.SerumPreset"))
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    for it in F.load_set(names):
        if it["name"] in data:
            continue
        rec = fit_item(it)
        data[it["name"]] = rec or {"kept": False, "why": "no unison stack"}
        if rec:
            log(f"  {it['name']:28s} serum {rec['serum_db']:+6.1f}  tablor {rec['before_db']:+6.1f}" +
                (f" -> {rec['after_db']:+6.1f} (scale {rec['scale']:.2f})  {'kept' if rec['kept'] else 'not kept'}" if "after_db" in rec else f"  {rec['why']}"))
        OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    kept = [n for n, r in data.items() if r.get("kept")]
    log(f"\n{len(kept)} of {len(data)} presets get a width fit")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
