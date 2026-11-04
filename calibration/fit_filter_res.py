#!/usr/bin/env python3
"""Serum's filter resonance against the engine's, model by model.

  fit_filter_res.py MODEL [MODEL ...]

For each model a held saw goes through Serum's filter at three cutoffs and several Reso settings; the engine's resonance pot is searched
(cutoff pot from the converter's own law, which fit_filter_model.py calibrated at Reso 0) for the one whose harmonic transfer is closest.
Averaged over the cutoffs, the pot for each Reso is written to calibration/filter_res.json, which the converter reads. Serum renders are
cached in calibration/fit_filter_res_serum.json."""
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

OUT = ROOT / "calibration/filter_res.json"
CACHE = ROOT / "calibration/fit_filter_res_serum.json"
CUTOFFS = (0.4, 0.55, 0.7)
RESOS = (0.0, 5.0, 12.0, 20.0, 30.0, 45.0, 60.0)
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
    for model in models:
        for c in CUTOFFS:
            for r in RESOS:
                k = f"{model}|{c}|{r}"
                if k not in cache:
                    cache[k] = V.harmonics(pool.render(H.with_filter(tree0, c, r, model))).tolist()
                    CACHE.write_text(json.dumps(cache))
        fm = d2m.FILTER_MODELS.get(model)
        ty = int(fm["type"]) if fm else d2m.FILTER_MAP[model][0]
        _, m = H.tablor_render(H.with_filter(tree0, 0.4, 0.0, "MgL24"), [V.NOTE], 0.3, 0.2)

        def err(ts, pot_f, pot_r):
            m2 = {"params": dict(m["params"]), "paths": m["paths"]}
            m2["params"].update(flt_type=ty, flt_freq=pot_f, flt_res=pot_r, flt_key=0, flt_env=64)
            te = V.transfer(V.harmonics(H.run_cli(d2m.blob(m2), [V.NOTE], V.DUR, V.HOLD)), base_e)
            return float(np.sqrt(np.average((te - ts)[ok] ** 2, weights=w[ok])))
        per = {r: [] for r in RESOS}
        for c in CUTOFFS:
            pf = int(round(min(max(c * d2m.CUTOFF_NOTES * d2m.CAL["cutoff_scale"] + d2m.CAL["cutoff_offset"] - 8 + d2m.filter_cutoff_delta(model, c), 0), 127)))
            for r in RESOS:
                ts = V.transfer(np.array(cache[f"{model}|{c}|{r}"]), base_s)
                coarse = min(range(0, 128, 8), key=lambda pr: err(ts, pf, pr))
                best = min(range(max(coarse - 7, 0), min(coarse + 7, 127) + 1), key=lambda pr: err(ts, pf, pr))
                per[r].append((best, err(ts, pf, best)))
        data[model] = {"table": [[r, float(np.median([p for p, _ in per[r]]))] for r in RESOS], "rms_db": float(np.mean([e for r in RESOS for _, e in per[r]])),
                       "by_cutoff": {str(r): per[r] for r in RESOS}}
        OUT.write_text(json.dumps(data, indent=1))
        print(model, "table", data[model]["table"], "rms %.1f dB" % data[model]["rms_db"], flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
