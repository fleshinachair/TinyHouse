#!/usr/bin/env python3
"""Per-preset closed-loop fit of the filter and the stereo FX against Serum's FULL chain (Patch 5).

  fit_chain.py [name ...]        these presets (default: every preset with a cached full-chain render)

fit_filter.py and fit_width.py matched Serum's DRY oscillators. Serum's designers balance a patch as
a whole (a cutoff pulled down because an EQ or HyperDimension adds top end, a wide chorus on a
narrow stack), so a dry fit stacks a second error on whatever the FX block does. Here the engine
plays the converter's complete output, FX on, and is compared with the Serum render made with all
its FX (calibration/refs/*_fx_*, made by fill_fx_refs.py). Same loss as fit_filter (third-octave
curve in three slices of the sounding part, 5 ms envelope, side/mid).

Fitted: flt_freq, flt_env, flt_d, flt_res (only if the filter does something), fx_wide and rev_width
(only if the preset has a width effect / a reverb). The start is what the converter ships today
(Patch 4 fits and the FX block on), so "before" is the deployed preset and "after" the new one.
Level is NOT fitted here (the loss is level-free); fit_level.py does that last.
Output: calibration/chain_fits.json {preset: {start, fit, loss_before, loss_after, kept}}.
"""
import copy
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter"))
sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fit_perceptual as F
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/fit_chain.log"
log = H.log
d2m = H.d2m
OUT = H.OUT / "chain_fits.json"
KEEP_MARGIN = 0.10
DUR, HOLD = 4.0, 1.0
LOCK = threading.Lock()


def fx_ref(name, notes):
    p = H.OUT / "refs" / f"{re.sub(r'[^A-Za-z0-9]+', '_', name + '_fx')}_{'_'.join(map(str, notes))}_2x4.0.npy"
    return list(np.load(p)) if p.exists() else None


def mapped(name, header=None, tree=None):
    """The converter's own mapping of one preset, FX on, as stage 2 and 3 of main() do it."""
    if tree is None:
        header, tree = H.load_preset(name)
    v = d2m.wrap_preset(d2m.physical(tree, header))
    nm = d2m.ascii_name(name)
    v["preset_name"] = nm
    v["serum2move"]["name"] = nm
    return d2m.map_to_tablor(v, {}, H.SERUM_TABLES, H.WT_OUT, "Deep House")


def load_items(names):
    out = []
    for n in names:
        header, tree = H.load_preset(n)
        if not H.usable(tree):
            continue
        notes = H.sounding_notes(tree, n)
        refs = fx_ref(n, notes)
        if refs is None:
            log(f"  {n}: no cached full-chain Serum render, left out")
            continue
        end = F.sounding_end(refs)
        out.append({"name": n, "header": header, "tree": tree, "notes": notes, "refs": refs, "end": end,
                    "ref_feat": F.features(refs, end)})
    return out


def render_loss(it, m, over, runs=2):
    q = copy.deepcopy(m)
    q["params"].update({k: int(round(v)) for k, v in over.items()})
    blob = d2m.blob(q)
    rs = [H.run_cli(blob, it["notes"], DUR, HOLD) for _ in range(runs)]
    return F.loss_of(it["ref_feat"], F.features(rs, it["end"]))


def knobs(P):
    ks = {}
    if not (P["flt_env"] == 64 and P["flt_freq"] >= 120):
        ks.update({"flt_freq": 30.0, "flt_env": 40.0, "flt_d": 45.0, "flt_res": 30.0})
    if P.get("fx_wide", 0) > 0:
        ks["fx_wide"] = 45.0
    if P.get("rev_mix", 0) > 0:
        ks["rev_width"] = 60.0
    return ks


def fit_item(it):
    m = mapped(it["name"], it["header"], it["tree"])
    P = m["params"]
    span = knobs(P)
    if not span:
        return {"kept": False, "skipped": "nothing to fit"}
    start = {k: P[k] for k in span}
    lo = {k: max(0.0, start[k] - span[k]) for k in start}
    hi = {k: min(127.0, start[k] + span[k]) for k in start}
    names = list(start)
    to_over = lambda u: {k: lo[k] + float(np.clip(u[i], 0, 1)) * (hi[k] - lo[k]) for i, k in enumerate(names)}
    mean_loss = lambda over: float(np.mean([render_loss(it, m, over, 3)["loss"] for _ in range(2)]))
    before = mean_loss({})
    u0 = np.array([(start[k] - lo[k]) / max(hi[k] - lo[k], 1e-9) for k in names])
    best, _ = F.nelder_mead(lambda u: render_loss(it, m, to_over(u))["loss"], u0, None, None, iters=50, step=0.2)
    over = {k: int(round(v)) for k, v in to_over(best).items()}
    after = mean_loss(over)
    gain = 1 - after / max(before, 1e-9)
    return {"start": start, "fit": over, "loss_before": before, "loss_after": after, "kept": bool(gain >= KEEP_MARGIN)}


def main():
    given = [a for a in sys.argv[1:] if not a.startswith("--")]
    names = given or sorted(p.stem for p in H.PRESET_DIR.glob("*.SerumPreset"))
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    d2m.APPLY_FILTER_FITS = d2m.APPLY_WIDTH_FITS = True
    d2m.APPLY_CHAIN_FITS = False                      # the start is the converter without this fit
    d2m.APPLY_TUNE_FITS = False                       # nor the tune fit that sits on top of it
    items = [it for it in load_items(names) if it["name"] not in data]
    log(f"{len(items)} presets to fit")

    def work(it):
        try:
            rec = fit_item(it)
        except Exception as e:                        # one bad preset must not stop the run
            rec = {"kept": False, "error": str(e)[:120]}
        with LOCK:
            data[it["name"]] = rec
            OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
            if "loss_after" in rec:
                gain = 1 - rec["loss_after"] / max(rec["loss_before"], 1e-9)
                log(f"  {it['name']:28s} loss {rec['loss_before']:5.1f} -> {rec['loss_after']:5.1f} ({gain * 100:+4.0f}%)  " +
                    " ".join(f"{k}:{rec['start'][k]}->{rec['fit'][k]}" for k in rec["start"]) + f"  {'kept' if rec['kept'] else 'not kept'}")
            else:
                log(f"  {it['name']:28s} {rec.get('skipped') or rec.get('error')}")
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(work, items))
    kept = [n for n, r in data.items() if r.get("kept")]
    log(f"\n{len(kept)} of {len(data)} presets get a full-chain fit")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    if "--fresh" in sys.argv and OUT.exists():
        OUT.unlink()
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
