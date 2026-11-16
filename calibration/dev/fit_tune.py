#!/usr/bin/env python3
"""Per-preset closed-loop fit of the amp envelope, the two shelves and Drive against Serum's FULL chain (Patch 7).

  fit_tune.py [name ...]

What fit_chain.py leaves: band_profile.py measured the 70 installed presets against Serum and found no global
spectral error (one curve for all removes 0.15 dB of 7.9), but a large per-preset one (the >3 kHz band is +7 dB
on pads, +5 on keys, -2.5 on plucks, and spreads -5..+9 dB inside each type) and a large envelope error (the
5 ms level trajectory is 10 dB off on average: nothing fitted the amp envelope). So, per preset, from the
converter's current output (every earlier fit applied):
  vca_d, vca_s, vca_r   the amp envelope's decay, sustain and release
  lo_shelf, hi_shelf    the shelves the engine now has (150 Hz and 5 kHz, +/-12 dB, 64 = flat)
  drive                 only for presets whose Serum rack has a Distortion, which the engine does not carry
Same loss as fit_chain (spectrum in three slices, 5 ms envelope, side/mid). Level is not fitted (the loss is
level-free); fit_level.py runs last. Output: calibration/tune_fits.json, same format as chain_fits.json.
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter"))
sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fit_chain as C
import fit_perceptual as F
import fxmap
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/fit_tune.log"
log = H.log
d2m = H.d2m
OUT = H.OUT / "tune_fits.json"
KEEP_MARGIN = 0.08


def knobs(P, tree):
    ks = {"vca_d": 45.0, "vca_s": 40.0, "vca_r": 45.0, "lo_shelf": 63.0, "hi_shelf": 63.0}
    if P.get("dist_drive", 0) > 0:
        ks.update({"dist_drive": 60.0, "dist_mix": 60.0})         # the distortion stage the preset carries
    elif any(fxmap.kind_of(n) == "Distortion" and fxmap.enabled(n) for n in tree.get("FXRack0", {}).get("FX", []) or []):
        ks["drive"] = 90.0                                         # a Distortion the stage did not take: the post-master Drive
    return ks


def fit_item(it):
    m = C.mapped(it["name"], it["header"], it["tree"])
    P = m["params"]
    span = knobs(P, it["tree"])
    start = {k: P[k] for k in span}
    lo = {k: max(0.0, start[k] - span[k]) for k in start}
    hi = {k: min(127.0, start[k] + span[k]) for k in start}
    names = list(start)
    to_over = lambda u: {k: lo[k] + float(np.clip(u[i], 0, 1)) * (hi[k] - lo[k]) for i, k in enumerate(names)}
    mean_loss = lambda over: float(np.mean([C.render_loss(it, m, over, 3)["loss"] for _ in range(2)]))
    before = mean_loss({})
    u0 = np.array([(start[k] - lo[k]) / max(hi[k] - lo[k], 1e-9) for k in names])
    best, _ = F.nelder_mead(lambda u: C.render_loss(it, m, to_over(u))["loss"], u0, None, None, iters=60, step=0.15)
    over = {k: int(round(v)) for k, v in to_over(best).items()}
    after = mean_loss(over)
    gain = 1 - after / max(before, 1e-9)
    return {"start": start, "fit": over, "loss_before": before, "loss_after": after, "kept": bool(gain >= KEEP_MARGIN)}


def main():
    given = [a for a in sys.argv[1:] if not a.startswith("--") and not a.endswith(".json")]
    for a in sys.argv[1:]:                                      # --names-file list.json: a JSON list of preset names
        if a.endswith(".json"):
            given += json.loads(Path(a).read_text(encoding="utf-8"))
    names = given or sorted(p.stem for p in H.PRESET_DIR.glob("*.SerumPreset"))
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    d2m.APPLY_FILTER_FITS = d2m.APPLY_WIDTH_FITS = d2m.APPLY_CHAIN_FITS = True
    d2m.APPLY_TUNE_FITS = False                      # the start is the converter without this fit
    items = [it for it in C.load_items(names) if it["name"] not in data]
    log(f"{len(items)} presets to fit")
    import threading
    lock = threading.Lock()

    def work(it):
        try:
            rec = fit_item(it)
        except Exception as e:
            rec = {"kept": False, "error": str(e)[:120]}
        with lock:
            data[it["name"]] = rec
            OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
            if "loss_after" in rec:
                gain = 1 - rec["loss_after"] / max(rec["loss_before"], 1e-9)
                log(f"  {it['name']:28s} loss {rec['loss_before']:5.1f} -> {rec['loss_after']:5.1f} ({gain * 100:+4.0f}%)  " +
                    " ".join(f"{k}:{rec['start'][k]}->{rec['fit'][k]}" for k in rec["start"]) + f"  {'kept' if rec['kept'] else 'not kept'}")
            else:
                log(f"  {it['name']:28s} {rec.get('error')}")
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(work, items))
    kept = [n for n, r in data.items() if r.get("kept")]
    log(f"\n{len(kept)} of {len(data)} presets get a tune fit")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    if "--fresh" in sys.argv and OUT.exists():
        OUT.unlink()
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
