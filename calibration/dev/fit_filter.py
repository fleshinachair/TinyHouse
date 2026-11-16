#!/usr/bin/env python3
"""Per-preset closed-loop fit of the filter: base cutoff, envelope depth, envelope decay, resonance.

  fit_filter.py [name ...]          these presets (default: every preset with a cached Serum render)
  fit_filter.py --anchors           the two anchors only, and print the 1-3 kHz trajectory

The same idea as the FX: the converter's closed-form filter envelope is right on average and wrong
for any one patch, because Serum's cutoff envelope has a curve and a depth range the mapper cannot
read off one number. So the mapped values are the starting point and the engine's three knobs are
searched until the engine's third-octave spectrum, in three slices of the part that sounds, and its
5 ms envelope agree with the cached Serum render. Serum is not run here. Output:
calibration/filter_fits.json, {preset: {start, fit, loss_before, loss_after, kept}}.
Only a fit that lowers the loss by a clear margin is kept.

Resonance is the fourth knob (Patch 5). Tablor's resonance takes the level out of the lows (its
feedback loop loses DC gain), Serum's does not as much, so a patch with a high mapped resonance has a
light fundamental. The third-octave loss has 30 bands and the 40-80 Hz fundamental is three of them,
so a LOW term is added: |ref - tablor| over the 40-80 Hz bands, in the part that sounds, for presets
whose Serum render has energy there. A previous (Patch 4) three-knob fit is the warm start and a
candidate: the new fit replaces it only if it scores better.
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter"))
os.chdir(ROOT)
import numpy as np
import fit_perceptual as F
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/fit_filter.log"
log = H.log
d2m = H.d2m
OUT = H.OUT / "filter_fits.json"
LOW_WEIGHT = 0.25
LOW = slice(3, 6)           # third-octave bands 40, 50 and 63 Hz (see H._BANDS)
KEEP_MARGIN = 0.12          # a fit must cut the loss by this fraction, or the mapper's value stays


def run_loss(it, m, over, runs=2):
    q = copy.deepcopy(m)
    q["params"].update({k: int(round(v)) for k, v in over.items()})
    blob = d2m.blob(q)
    rs = list(F.POOL.map(lambda _: H.run_cli(blob, it["notes"], 2.0, 1.5), range(runs)))
    feat = F.features(rs, it["end"])
    out = F.loss_of(it["ref_feat"], feat)
    out["low"] = low_err(it["ref_feat"], feat)
    out["total"] = out["loss"] + LOW_WEIGHT * out["low"]
    return out, rs


def low_err(ref, tab):
    """Mean |dB| error over the 40-80 Hz bands, in slices where Serum has energy there (else 0)."""
    e = []
    for a, b in zip(ref["bands"], tab["bands"]):
        x, y = a[LOW], b[LOW]
        k = np.isfinite(x) & np.isfinite(y) & (x > -45)
        if k.any():
            e.append(np.mean(np.abs(x[k] - y[k])))
    return float(np.mean(e)) if e else 0.0


def fit_item(it, prev=None):
    m = d2m.map_to_tablor(d2m.to_vital(d2m.physical(it["tree"], it["header"])), {}, H.SERUM_TABLES, H.WT_OUT, "Deep House")
    P = m["params"]
    if P["flt_env"] == 64 and P["flt_freq"] >= 120:      # filter wide open and unmodulated: nothing to fit
        return None
    start = {"flt_freq": P["flt_freq"], "flt_env": P["flt_env"], "flt_d": P["flt_d"], "flt_res": P["flt_res"]}
    span = {"flt_freq": 30.0, "flt_env": 40.0, "flt_d": 45.0, "flt_res": 45.0}
    lo = {k: max(0.0, start[k] - span[k]) for k in start}
    hi = {k: min(127.0, start[k] + span[k]) for k in start}
    names = list(start)
    to_over = lambda u: {k: lo[k] + float(np.clip(u[i], 0, 1)) * (hi[k] - lo[k]) for i, k in enumerate(names)}
    mean_of = lambda over, key: float(np.mean([run_loss(it, m, over, 3)[0][key] for _ in range(2)]))
    base = {k: mean_of({}, k) for k in ("loss", "low", "total")}
    warm = dict(start)
    if prev and prev.get("kept"):
        warm.update(prev["fit"])                         # the Patch 4 filter, resonance still the mapper's
    u0 = np.array([(min(max(warm[k], lo[k]), hi[k]) - lo[k]) / max(hi[k] - lo[k], 1e-9) for k in names])
    best, val = F.nelder_mead(lambda u: run_loss(it, m, to_over(u))[0]["total"], u0, None, None, iters=55, step=0.2)
    over = {k: int(round(v)) for k, v in to_over(best).items()}
    cands = [over]
    if prev and prev.get("kept"):
        cands.append({**start, **prev["fit"]})           # fall back to the Patch 4 fit if the new one is not better
    scored = [(mean_of(c, "total"), c) for c in cands]
    tot, chosen = min(scored, key=lambda t: t[0])
    return {"start": start, "fit": chosen, "loss_before": base["loss"], "low_before": base["low"], "total_before": base["total"],
            "loss_after": mean_of(chosen, "loss"), "low_after": mean_of(chosen, "low"), "total_after": tot,
            "from": "new" if chosen is over else "patch4"}, m


def main():
    anchors = "--anchors" in sys.argv
    given = [a for a in sys.argv[1:] if not a.startswith("--")]
    names = list(H.CANONICAL.values()) if anchors else given or sorted(
        p.stem for p in H.PRESET_DIR.glob("*.SerumPreset"))
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() and not anchors else {}
    d2m.APPLY_FILTER_FITS = False            # start from the mapper's own values, with the table that ships
    items = F.load_set(names)
    log(f"{len(items)} presets with a usable cached render")
    for it in items:
        if "flt_res" in data.get(it["name"], {}).get("start", {}) or "skipped" in data.get(it["name"], {}):
            continue
        r = fit_item(it, data.get(it["name"]))
        if r is None:
            data[it["name"]] = {"skipped": "filter open and unmodulated"}
            continue
        rec, _ = r
        gain = 1 - rec["total_after"] / max(rec["total_before"], 1e-9)
        rec["kept"] = bool(gain >= KEEP_MARGIN)
        data[it["name"]] = rec
        log(f"  {it['name']:28s} loss {rec['loss_before']:5.1f} -> {rec['loss_after']:5.1f} ({gain * 100:+4.0f}%)  "
            f"freq {rec['start']['flt_freq']}->{rec['fit']['flt_freq']}  env {rec['start']['flt_env']}->{rec['fit']['flt_env']}  "
            f"d {rec['start']['flt_d']}->{rec['fit']['flt_d']}  res {rec['start']['flt_res']}->{rec['fit']['flt_res']}  "
            f"low {rec['low_before']:.1f}->{rec['low_after']:.1f} dB  [{rec['from']}] {'kept' if rec['kept'] else 'not kept'}")
        if not anchors:
            OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    if anchors:
        (H.OUT / "filter_fits_anchors.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
