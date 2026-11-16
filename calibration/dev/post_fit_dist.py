#!/usr/bin/env python3
"""Distortion on top of a finished set of preset files (Patch 7).

  post_fit_dist.py IN_DIR OUT_DIR MAPPED_DIR

The shipped files are a per-preset mix of converter runs, so a new effect cannot simply be regenerated into them. For
each preset the converter gives a Distortion (mode, drive, wet mix) from Serum's own settings: MAPPED_DIR holds that
converter output. Those three values go into the shipped file's state, drive and mix are then searched (Nelder-Mead,
same full-chain loss as the other fits) against Serum's full render, and the result is kept only if it cuts the loss
by KEEP_MARGIN against the shipped file with no distortion. Writes OUT_DIR/*.json (the others copied unchanged) and
calibration/post_fit_dist.json.
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import json
import os
import re
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fit_chain as C
import fit_perceptual as F
import post_fit as PF
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/post_fit_dist.log"
log = H.log
KEEP_MARGIN = 0.08
EXEMPT = set()                       # Bass - A Classic was exempt (approved by ear); the user chose faithful translation for every preset


def loss_of(it, state, runs=2):
    rs = [H.run_cli(state, it["notes"], C.DUR, C.HOLD) for _ in range(runs)]
    return F.loss_of(it["ref_feat"], F.features(rs, it["end"]))["loss"]


def fit_item(it, state, mapped_state):
    mode = int(PF.getk(mapped_state, "dist_mode", 0))
    d0, m0 = PF.getk(mapped_state, "dist_drive", 0), PF.getk(mapped_state, "dist_mix", 127)
    if d0 <= 0:
        return state, {"why": "no distortion to carry"}
    base_state = PF.setk(PF.setk(PF.setk(state, "dist_drive", 0), "dist_mode", mode), "dist_mix", int(m0))
    mean = lambda st: float(np.mean([loss_of(it, st, 3) for _ in range(2)]))
    before = mean(base_state)
    mk = lambda drive, mix: PF.setk(PF.setk(PF.setk(state, "dist_drive", int(round(drive))), "dist_mode", mode), "dist_mix", int(round(mix)))
    lo, hi = np.array([0.0, 0.0]), np.array([127.0, 127.0])
    to = lambda u: lo + np.clip(u, 0, 1) * (hi - lo)
    cands = [np.array([d0, m0]), np.array([max(d0 * 0.5, 8), m0]), np.array([min(d0 * 1.4, 120), max(m0 * 0.6, 20)])]
    best_u, best_v = None, 1e9
    for c in cands:
        u, v = F.nelder_mead(lambda uu: loss_of(it, mk(*to(uu))), (c - lo) / (hi - lo), None, None, iters=22, step=0.2)
        if v < best_v:
            best_u, best_v = u, v
    drive, mix = to(best_u)
    final = mk(drive, mix)
    after = mean(final)
    rec = {"mode": mode, "mapped": [d0, m0], "fit": [int(round(drive)), int(round(mix))], "loss_before": before, "loss_after": after,
           "kept": bool(after <= before * (1 - KEEP_MARGIN))}
    return (final if rec["kept"] else state), rec


def main():
    src, dst, mapped = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.glob("*.json"):
        shutil.copyfile(f, dst / f.name)
    names = set(json.loads(Path("calibration/dist_presets.json").read_text(encoding="utf-8"))) - EXEMPT
    srcname = {H.d2m.ascii_name(p.stem): p.stem for p in H.PRESET_DIR.glob("*.SerumPreset")}
    out, lock = {}, threading.Lock()

    def work(stem):
        f = src / (H.d2m.ascii_name(stem) + ".json")
        mf = mapped / f.name
        if not f.exists() or not mf.exists():
            return
        j = json.loads(f.read_text(encoding="utf-8"))
        items = C.load_items([stem])
        try:
            new, rec = fit_item(items[0], j["state"], json.loads(mf.read_text(encoding="utf-8"))["state"])
            j["state"] = new
            (dst / f.name).write_text(json.dumps(j) + "\n", encoding="utf-8")
        except Exception as e:
            rec = {"error": str(e)[:100]}
        with lock:
            out[stem] = rec
            if "fit" in rec:
                log(f"  {stem:26s} mode {rec['mode']}  loss {rec['loss_before']:5.1f} -> {rec['loss_after']:5.1f}  drive {rec['mapped'][0]:.0f}->{rec['fit'][0]}  mix {rec['mapped'][1]:.0f}->{rec['fit'][1]}  {'kept' if rec['kept'] else 'not kept'}")
            else:
                log(f"  {stem:26s} {rec.get('why') or rec.get('error')}")
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(work, sorted(names)))
    Path("calibration/post_fit_dist.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    log(f"\n{sum(1 for r in out.values() if r.get('kept'))} of {len(out)} get a distortion")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
