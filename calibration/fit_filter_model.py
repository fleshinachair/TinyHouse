#!/usr/bin/env python3
"""Which of the engine's filters, and at which cutoff pot, is each Serum filter model?

  fit_filter_model.py MODEL [MODEL ...]

For each model a held saw goes through Serum's filter at several cutoffs (no resonance); the engine's eight filter types (LP, HP, BP, notch; 12 and 24 dB) are searched
(cutoff pot, resonance 0) for the one whose harmonic transfer is closest; the best type and, per cutoff, the best pot (as an offset to the
converter's closed-form cutoff) are written to calibration/filter_models.json, which the converter reads. A model whose best match is
still more than 6 dB off is recorded as poor and the converter flags the preset. Serum renders are cached.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import deephouse2move as d2m
import probe_vfilter as V
import test_render_calibration as H

OUT = ROOT / "calibration/filter_models.json"
CACHE = ROOT / "calibration/fit_filter_model_serum.json"
CUTOFFS = (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)
TYPES = {0: "LP12", 1: "LP24", 2: "HP12", 3: "HP24", 4: "BP12", 5: "BP24", 6: "N12", 7: "N24"}
V.NH = 60


def main():
    models = [a for a in sys.argv[1:] if not a.startswith("--")]
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t)
    pool = V.SerumPool()
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if "base" not in cache:
        cache["base"] = V.harmonics(pool.render(tree0)).tolist()
    base_s = np.array(cache["base"])
    x0, _ = H.tablor_render(tree0, [V.NOTE], V.DUR, V.HOLD)
    base_e = V.harmonics(x0)
    w = 1.0 / np.arange(1, V.NH + 1) ** 0.5
    ok = (np.arange(1, V.NH + 1) * V.F0 < 16000) & (base_s > base_s.max() - 70)
    data = json.loads(OUT.read_text()) if OUT.exists() else {}
    for model in models:                                  # "LH6@80" is LH6 with its Var knob at 80
        name, _, var = model.partition("@")
        def filt(c):
            t = H.with_filter(tree0, c, 0.0, name)
            if var:
                t["VoiceFilter0"]["plainParams"]["kParamVar"] = float(var)
            return t
        for c in CUTOFFS:
            k = f"{model}|{c}"
            if k not in cache:
                cache[k] = V.harmonics(pool.render(filt(c))).tolist()
                CACHE.write_text(json.dumps(cache))
        _, m = H.tablor_render(H.with_filter(tree0, 0.4, 0.0, "MgL24"), [V.NOTE], 0.3, 0.2)
        def err(ts, ty, ff):
            m2 = {"params": dict(m["params"]), "paths": m["paths"]}
            m2["params"].update(flt_type=ty, flt_freq=ff, flt_res=0, flt_key=0, flt_env=64)
            te = V.transfer(V.harmonics(H.run_cli(d2m.blob(m2), [V.NOTE], V.DUR, V.HOLD)), base_e)
            return float(np.sqrt(np.average((te - ts)[ok] ** 2, weights=w[ok])))
        score = {}
        best_pots = {}
        for ty in TYPES:
            errs, pots = [], []
            for c in CUTOFFS:
                ts = V.transfer(np.array(cache[f"{model}|{c}"]), base_s)
                coarse = min(range(0, 128, 6), key=lambda ff: err(ts, ty, ff))
                ff = min(range(max(coarse - 5, 0), min(coarse + 5, 127) + 1), key=lambda f: err(ts, ty, f))
                errs.append(err(ts, ty, ff)); pots.append(ff)
            score[ty] = float(np.mean(errs)); best_pots[ty] = pots
        ty = min(score, key=score.get)
        # leaving the filter open is a candidate too: when no engine filter is closer to Serum's than "no filter" is, open it
        bypass = float(np.mean([np.sqrt(np.average(V.transfer(np.array(cache[f"{model}|{c}"]), base_s)[ok] ** 2, weights=w[ok])) for c in CUTOFFS]))
        closed = [min(max(c * d2m.CUTOFF_NOTES * d2m.CAL["cutoff_scale"] + d2m.CAL["cutoff_offset"] - 8, 0), 127) for c in CUTOFFS]
        data[model] = {"type": ty, "type_name": TYPES[ty], "rms_db": score[ty], "poor": score[ty] > 6.0, "bypass": bypass < score[ty], "bypass_rms_db": bypass,
                       "delta": [[c, float(p - cl)] for c, p, cl in zip(CUTOFFS, best_pots[ty], closed)],
                       "all_types_rms": {TYPES[t]: round(s, 1) for t, s in score.items()}}
        OUT.write_text(json.dumps(data, indent=1))
        print(f"{model:12s} best engine filter {TYPES[ty]} (rms {score[ty]:.1f} dB, filter open {bypass:.1f} dB; all: {data[model]['all_types_rms']}); pot offsets by cutoff {[round(d, 0) for _, d in data[model]['delta']]}", flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
