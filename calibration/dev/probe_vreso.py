#!/usr/bin/env python3
"""Serum's voice-filter resonance against the engine's, on a low saw (65 Hz: harmonics 2x denser, so a resonance peak is seen).

For each model and reso percentage the cutoff is fixed at the model's best cutoff pot (from probe_vfilter.json) and the resonance
pot that makes the harmonic transfer match is searched in steps of 4. Output: calibration/probe_vreso.json.
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import deephouse2move as d2m
import probe_vfilter as V
import test_render_calibration as H

V.NOTE = 36
V.F0 = 440 * 2 ** ((V.NOTE - 69) / 12)
V.NH = 120
CACHE = ROOT / "calibration/probe_vreso_serum.json"
MODELS = ("MgL24", "MgL18", "L12", "B24", "H24")
RESOS = (5.0, 12.0, 20.0, 30.0, 40.0)
CUTOFF = 0.5


def main():
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t)
    pool = V.SerumPool()
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if "base" not in cache:
        cache["base"] = V.harmonics(pool.render(tree0)).tolist()
    for model in MODELS:
        for r in (0.0,) + RESOS:
            key = f"{model}|{r}"
            if key not in cache:
                cache[key] = V.harmonics(pool.render(H.with_filter(tree0, CUTOFF, r, model))).tolist()
                CACHE.write_text(json.dumps(cache))
                print("serum", key, flush=True)
    base_s = np.array(cache["base"])
    x0, _ = H.tablor_render(tree0, [V.NOTE], V.DUR, V.HOLD)
    base_e = V.harmonics(x0)
    w = 1.0 / np.arange(1, V.NH + 1) ** 0.5
    ok = (np.arange(1, V.NH + 1) * V.F0 < 16000) & (base_s > base_s.max() - 70)
    out = {}
    for model in MODELS:
        _, m = H.tablor_render(H.with_filter(tree0, CUTOFF, 0.0, model), [V.NOTE], 0.3, 0.2)
        def render(ff, rr):
            m2 = {"params": dict(m["params"]), "paths": m["paths"]}
            m2["params"].update(flt_freq=ff, flt_res=rr)
            return V.transfer(V.harmonics(H.run_cli(d2m.blob(m2), [V.NOTE], V.DUR, V.HOLD)), base_e)
        def err(ts, ff, rr):
            te = render(ff, rr)
            return float((w[ok] * (te[ok] - ts[ok]) ** 2).sum() / w[ok].sum()) ** 0.5
        t0 = V.transfer(np.array(cache[f"{model}|0.0"]), base_s)
        best_f = min(range(20, 128, 3), key=lambda ff: err(t0, ff, 0))
        best_f = min(range(max(best_f - 3, 0), min(best_f + 3, 127) + 1), key=lambda ff: err(t0, ff, 0))
        row = {"freq0": best_f}
        for r in RESOS:
            ts = V.transfer(np.array(cache[f"{model}|{r}"]), base_s)
            res = min(range(0, 128, 4), key=lambda rr: min(err(ts, ff, rr) for ff in (best_f - 2, best_f, best_f + 2)))
            e = min(err(ts, ff, res) for ff in (best_f - 2, best_f, best_f + 2))
            row[str(r)] = {"res": res, "rms_db": e}
            print(f"{model:6s} cutoff pot {best_f:3d} reso {r:4.0f}% -> res pot {res:3d} (rms {e:4.1f} dB; no resonance {err(ts, best_f, 0):4.1f} dB)", flush=True)
        out[model] = row
        (ROOT / "calibration/probe_vreso.json").write_text(json.dumps(out, indent=1))
    os._exit(0)


if __name__ == "__main__":
    main()
