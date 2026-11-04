#!/usr/bin/env python3
"""Fit the mapper's filter constants against cached Serum renders, perceptually.

RESULT: REJECTED, kept as the record of why. Three pooled constants (cutoff offset, envelope depth,
envelope decay multiplier) fitted on 9 presets cut their loss 6 % and RAISED the loss of the 12
validation presets by 9 % (26.7 -> 29.1), with single presets 10 points worse. The filter envelope
error is per patch, not a scale. fit_filter.py fits the same loss per preset instead.

  fit_perceptual.py [--apply]

Nothing here compares samples (Serum's unison phases free-run). The loss is, per preset, summed over
the part that actually sounds (found from Serum's own envelope):
  spectrum  third-octave band curve, in three time slices of the sounding part, so a filter that
            closes at the wrong speed shows up, not just one that sits at the wrong frequency
  envelope  5 ms level trajectory, clipped 60 dB under its peak
  mid/side  |side/mid difference|, over the held part
Fitted: cutoff_offset, flt_env_scale (filter envelope depth), flt_decay_mult (filter envelope decay
time against what the amplitude envelope's calibration gives). Trained on the two anchors and the
earlier acceptance set; the validation presets are only scored, never fitted. Serum is not run:
every reference comes from calibration/refs. numpy only (Nelder-Mead below).
"""
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import numpy as np
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/fit_perceptual.log"
log = H.log
d2m, SR = H.d2m, H.SR
TRAIN = list(H.CANONICAL.values()) + H.HELD_OUT
VALID = list(H.VALIDATION)
PARAMS = {"cutoff_offset": (-30.0, 8.0), "flt_env_scale": (0.2, 1.6), "flt_decay_mult": (0.3, 2.5)}
POOL = ThreadPoolExecutor(max_workers=max(2, (os.cpu_count() or 4) - 1))


def cached_ref(name, notes):
    p = H.OUT / "refs" / f"{re.sub(r'[^A-Za-z0-9]+', '_', name)}_{'_'.join(map(str, notes))}_3x2.0.npy"
    return list(np.load(p)) if p.exists() else None


def load_set(names):
    out = []
    for n in names:
        header, tree = H.load_preset(n)
        if not H.usable(tree):
            continue
        notes = H.sounding_notes(tree, n)
        refs = cached_ref(n, notes)
        if refs is None:
            log(f"  {n}: no cached Serum render, left out")
            continue
        end = sounding_end(refs)
        out.append({"name": n, "header": header, "tree": H.strip_fx(tree), "notes": notes, "refs": refs, "end": end,
                    "ref_feat": features(refs, end)})
    return out


def sounding_end(runs, floor=-35.0):
    m = np.mean([(r ** 2).mean(axis=0)[:len(r[0]) // 220 * 220].reshape(-1, 220).mean(axis=1) for r in runs], axis=0)
    e = 10 * np.log10(np.maximum(m, 1e-14))
    w = np.where(e > e.max() + floor)[0]
    return float(np.clip((w[-1] + 1) * 220 / SR, 0.12, 1.5)) if w.size else 0.12


def env5(runs, n):
    return np.mean([10 * np.log10(np.maximum((r[:, :n * 220] ** 2).mean(axis=0).reshape(n, 220).mean(axis=1), 1e-14)) for r in runs], axis=0)


def features(runs, end):
    """Per-slice third-octave curves, the 5 ms envelope and mid/side: everything the loss reads."""
    n = min(r.shape[1] for r in runs) // 220
    e = env5(runs, n)
    cuts = np.linspace(0.01, end, 4)
    slices = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        x = [r[:, int(a * SR):int(b * SR)] for r in runs]
        slices.append(np.mean([H.band_db(v) for v in x], axis=0))
    h0, h1 = int(0.01 * SR), int(end * SR)
    return {"env": np.maximum(e - e.max(), -60.0), "bands": slices, "sm": float(np.mean([H.side_mid_db(r[:, h0:h1]) for r in runs])),
            "n": n}


def loss_of(ref, tab):
    n = min(ref["n"], tab["n"])
    k = (ref["env"][:n] > -60) | (tab["env"][:n] > -60)
    env = float(np.sqrt(np.mean((ref["env"][:n] - tab["env"][:n])[k] ** 2))) if k.any() else 0.0
    spec = []
    for a, b in zip(ref["bands"], tab["bands"]):
        keep = np.isfinite(a) & np.isfinite(b) & ((a > -60) | (b > -60))
        if keep.any():
            spec.append(np.sqrt(np.mean((a[keep] - b[keep]) ** 2)))
    spec = float(np.mean(spec)) if spec else 0.0
    ms = abs(ref["sm"] - tab["sm"])
    return {"spec": spec, "env": env, "ms": ms, "loss": spec + 0.5 * env + 0.25 * ms}


def score(cal, items, runs=2):
    """Mean loss parts for `cal` over `items`. Mapping is serial (it reads the global table),
    the engine renders are parallel."""
    d2m.CAL.clear()
    d2m.CAL.update(cal)
    jobs = []
    for it in items:
        m = d2m.map_to_tablor(d2m.to_vital(d2m.physical(it["tree"], it["header"])), {}, H.SERUM_TABLES, H.WT_OUT, "Deep House")
        blob = d2m.blob(m)
        for _ in range(runs):
            jobs.append((it, blob))
    renders = list(POOL.map(lambda j: H.run_cli(j[1], j[0]["notes"], 2.0, 1.5), jobs))
    per = {}
    for (it, _), r in zip(jobs, renders):
        per.setdefault(it["name"], []).append(r)
    res = {}
    for it in items:
        rs = per[it["name"]]
        res[it["name"]] = loss_of(it["ref_feat"], features(rs, it["end"]))
    return res


def mean_parts(res):
    return {k: float(np.mean([v[k] for v in res.values()])) for k in ("spec", "env", "ms", "loss")}


def nelder_mead(f, x0, lo, hi, iters=70, step=0.25):
    """Compact Nelder-Mead in the unit box, one start, bounds by clipping."""
    n = len(x0)
    clip = lambda x: np.clip(x, 0.0, 1.0)
    sim = [clip(x0)] + [clip(x0 + step * np.eye(n)[i] * (1 if x0[i] < 0.7 else -1)) for i in range(n)]
    vals = [f(x) for x in sim]
    for it in range(iters):
        o = np.argsort(vals)
        sim, vals = [sim[i] for i in o], [vals[i] for i in o]
        c = np.mean(sim[:-1], axis=0)
        xr = clip(c + (c - sim[-1]))
        fr = f(xr)
        if fr < vals[0]:
            xe = clip(c + 2 * (c - sim[-1]))
            fe = f(xe)
            sim[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            sim[-1], vals[-1] = xr, fr
        else:
            xc = clip(c + 0.5 * (sim[-1] - c))
            fc = f(xc)
            if fc < vals[-1]:
                sim[-1], vals[-1] = xc, fc
            else:
                sim = [sim[0]] + [clip(sim[0] + 0.5 * (s - sim[0])) for s in sim[1:]]
                vals = [vals[0]] + [f(s) for s in sim[1:]]
        if max(np.abs(sim[i] - sim[0]).max() for i in range(1, n + 1)) < 0.01:
            break
    o = int(np.argmin(vals))
    return sim[o], vals[o]


def main():
    base = json.loads((H.OUT / "cal_final.json").read_text(encoding="utf-8"))
    base.setdefault("flt_decay_mult", 1.0)
    train, valid = load_set(TRAIN), load_set(VALID)
    log(f"train {len(train)}: " + ", ".join(i["name"] for i in train))
    log(f"valid {len(valid)}: " + ", ".join(i["name"] for i in valid))
    names = list(PARAMS)
    lo = np.array([PARAMS[k][0] for k in names])
    hi = np.array([PARAMS[k][1] for k in names])
    to_cal = lambda u: base | dict(zip(names, (lo + np.clip(u, 0, 1) * (hi - lo)).tolist()))
    u0 = (np.array([base[k] for k in names]) - lo) / (hi - lo)

    b_tr, b_va = score(base, train), score(base, valid)
    log(f"\nbaseline  train {mean_parts(b_tr)}\n          valid {mean_parts(b_va)}")
    anchors = [n for n in H.CANONICAL.values()]

    n_eval = [0]

    def f(u):
        n_eval[0] += 1
        r = mean_parts(score(to_cal(u), train))
        c = to_cal(u)
        log(f"  eval {n_eval[0]:3d}  " + " ".join(f"{k}={c[k]:7.3f}" for k in names) +
            f"   loss {r['loss']:.3f} (spec {r['spec']:.2f} env {r['env']:.2f} ms {r['ms']:.2f})")
        return r["loss"]

    u, best = nelder_mead(f, u0, lo, hi)
    fit = to_cal(u)
    log("\nfitted: " + ", ".join(f"{k} {base[k]:.3f} -> {fit[k]:.3f}" for k in names))
    a_tr, a_va = score(fit, train), score(fit, valid)
    log(f"\nfitted    train {mean_parts(a_tr)}\n          valid {mean_parts(a_va)}")
    log(f"\n{'preset':28s}{'set':>7s}{'before':>9s}{'after':>9s}   spec / env / ms after")
    better = 0
    for label, bb, aa in (("train", b_tr, a_tr), ("valid", b_va, a_va)):
        for n in bb:
            x, y = bb[n]["loss"], aa[n]["loss"]
            better += (label == "valid") and y < x
            log(f"{n:28s}{label:>7s}{x:9.2f}{y:9.2f}   {aa[n]['spec']:.1f} / {aa[n]['env']:.1f} / {aa[n]['ms']:.1f}" + ("   *anchor" if n in anchors else ""))
    log(f"\nvalidation presets improved: {better} of {len(b_va)}")
    (H.OUT / "cal_perceptual.json").write_text(json.dumps(fit, indent=1), encoding="utf-8")
    (H.OUT / "fit_perceptual.json").write_text(json.dumps({
        "before": {"train": b_tr, "valid": b_va}, "after": {"train": a_tr, "valid": a_va}, "fit": {k: fit[k] for k in names}}, indent=1), encoding="utf-8")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
