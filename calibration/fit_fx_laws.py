#!/usr/bin/env python3
"""Static reverb and width laws: Serum's own effect settings -> the engine's pots, with no fingerprint and no render.

  fit_fx_laws.py --collect     (needs Serum 2's presets, calibration/fx_fingerprints.json and calibration/tablor_cli.exe) rebuilds calibration/fx_law_data.json
  fit_fx_laws.py               fits calibration/fx_laws.json from that data

The converter's closed loop (fxmap.map_reverb / map_wide) measures each factory preset's reverb and width in Serum and bisects the engine to match. That needs Serum
and the native engine, so a user importing from a browser cannot run it. Here the closed loop's answers for every factory preset are the training set: the Serum node's
stored parameters (after the macros are folded, as map_fx sees them) are regressed on what the loop chose, so the same mapping runs from the parameters alone.

  reverb   Serum's Size / Delay (its decay control) / FreqB / Freq / Feedback -> log RT60 (one small ridge model per Type, cross-validated), RT60 -> the engine's decay pot
           through the engine's own measured decay table; damping and width are per-Type constants of the loop's answers (Hall and Vintage keep their stereo width, the default
           type follows Serum's Width); mix is a constant fraction of Serum's Wet.
  width    Serum's HyperD Wet and Dimension Wet -> side added in dB (a two-term amplitude law), -> fx_wide through the engine's measured width table.
"""
import glob
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "calibration"))
import numpy as np

DATA = ROOT / "calibration/fx_law_data.json"
OUT = ROOT / "calibration/fx_laws.json"
TYPES = ["kHall", "none", "kVintage", "kAbyss"]
import fxmap as FX

FEATURES = FX.REVERB_FEATURES


def reverb_inputs(pp: dict) -> dict:
    return {k: float(pp.get(v, FX.REVERB_DEFAULTS[k])) for k, v in FX.REVERB_KEYS.items()}


MODEL = {"kHall": "quad", "none": "size", "kVintage": "size", "kAbyss": "sds"}


def collect() -> None:
    import fxmap as F
    import fxmetrics as M
    import deephouse2move as d2m
    from serum2_preset_loader.codec import decode_preset_file
    idx = {}
    for root, _, fs in os.walk(str(Path.home() / "OneDrive/Documents/Xfer/Serum 2 Presets/Presets")):
        for f in fs:
            if f.endswith(".SerumPreset"):
                idx.setdefault(f[:-12], Path(root) / f)
    for f in glob.glob(str(ROOT / "out/_extracted/Presets/**/*.SerumPreset"), recursive=True):
        idx.setdefault(Path(f).stem, Path(f))
    fps = json.loads(F.FINGERPRINTS.read_text(encoding="utf-8"))
    cache = json.loads(F.TABLOR_CACHE.read_text(encoding="utf-8")) if F.TABLOR_CACHE.exists() else {}
    rows = []
    for name, rec in fps.items():
        if name not in idx or not ("reverb" in rec or "wide" in rec):
            continue
        h, tree = decode_preset_file(idx[name])
        ph = d2m.physical(tree, h)
        nodes = F.fold_macro_knobs(F.fold_macro_wets(ph["fx_nodes"], ph["mods"], ph["macros"]), ph["mods"], ph["macros"])
        inside = F.branch_nodes(nodes)
        top = [n for i, n in enumerate(nodes) if i not in inside and F.enabled(n)]
        row = {"name": name, "rev": [F.plain(n) for n in top if F.kind_of(n) == "Reverb"], "conv": any(F.kind_of(n) == "Conv" for n in top),
               "hyd": [F.plain(n) for n in top if F.kind_of(n) == "HyperD"], "fp": {k: rec[k] for k in ("reverb", "wide") if k in rec}}
        if "reverb" in rec and len(row["rev"]) == 1 and not row["conv"]:
            P, _, _ = F.map_reverb(rec, cache)
            row["eng"] = {k: P[k] for k in ("rev_mix", "rev_decay", "rev_damp", "rev_width")}
        rows.append(row)
        print(len(rows), name, flush=True)
    dry = F.render_cli(F.noise_patch({}), 2.0, 1.9)
    wtab = {v: M.side_added_db(F.render_cli(F.noise_patch({"fx_wide": v}), 2.0, 1.9), dry, 0.3, 1.8) for v in list(range(4, 128, 4)) + [127]}

    def tail(decay):
        return M.reverb_tail(F.render_cli(F.noise_patch({"rev_mix": 127, "rev_decay": decay, "rev_damp": 120, "rev_width": 127}), 5.0, 0.12))["rt60"]
    dtab = {d: tail(d) for d in list(range(0, 128, 4)) + [127]}
    DATA.write_text(json.dumps({"rows": rows, "wide_table": wtab, "decay_table": dtab}), encoding="utf-8")


def ridge(A, y, lam):
    return np.linalg.solve(A.T @ A + lam * np.eye(A.shape[1]), A.T @ y)


def fit() -> None:
    d = json.loads(DATA.read_text(encoding="utf-8"))
    rows = d["rows"]
    law = {"note": "fitted by calibration/fit_fx_laws.py from the closed loop's answers for the factory presets", "reverb": {}, "wide": {}}
    # ---- reverb
    rv = [r for r in rows if "reverb" in r["fp"] and "eng" in r and r["fp"]["reverb"]["centroid"] == r["fp"]["reverb"]["centroid"]]
    typ = lambda r: r["rev"][0].get("kParamType", "none")
    report = {}
    law["reverb"]["rt60"] = {}
    for t in TYPES:
        D = [r for r in rv if typ(r) == t and math.isfinite(r["fp"]["reverb"]["rt60"]) and 0 < r["fp"]["reverb"]["rt60"] < 30]
        A = np.array([FEATURES[MODEL[t]](reverb_inputs(r["rev"][0])) for r in D])
        y = np.log([r["fp"]["reverb"]["rt60"] for r in D])
        lam = 1e-3
        err = []
        for i in range(len(y)):
            m = np.ones(len(y), bool)
            m[i] = False
            err.append(A[i] @ ridge(A[m], y[m], lam) - y[i])
        law["reverb"]["rt60"][t] = {"features": MODEL[t], "coef": [round(float(c), 6) for c in ridge(A, y, lam)]}
        report[t] = (len(D), float(np.sqrt(np.mean(np.square(err)))))
    dec = sorted((int(k), v) for k, v in d["decay_table"].items() if v == v and math.isfinite(v))
    law["reverb"]["decay_table"] = [[k, round(v, 4)] for k, v in dec]
    law["reverb"]["damp"] = {t: int(np.median([r["eng"]["rev_damp"] for r in rv if typ(r) == t and r["eng"]["rev_mix"] > 0] or [110])) for t in TYPES}
    mix = [r["eng"]["rev_mix"] / 127 * 100 / r["rev"][0].get("kParamWet", 25.0) for r in rv if r["rev"][0].get("kParamWet", 25.0) >= 3 and r["eng"]["rev_mix"] > 0]
    law["reverb"]["mix_per_wet"] = round(float(np.median(mix)), 3)
    nw = [(r["rev"][0].get("kParamWidth", 20.0), r["eng"]["rev_width"]) for r in rv if typ(r) == "none" and r["eng"]["rev_mix"] > 0 and r["eng"]["rev_width"] < 127]
    law["reverb"]["none_width_per_w"] = round(float(np.median([b / a for a, b in nw if a > 5])), 3) if nw else 1.3
    # ---- width
    wd = [r for r in rows if "wide" in r["fp"] and len(r["hyd"]) == 1]
    X = [(r["hyd"][0].get("kParamWet", 25.0), r["hyd"][0].get("kParamDimEWet", 0.0)) for r in wd]
    y = np.array([max(r["fp"]["wide"]["side_added_db"], -30.0) for r in wd])      # the fingerprint floors anything below -30 dB at a silent -168
    best = None
    A0 = np.array(X) / 100.0
    for p in (0.5, 0.75, 1.0, 1.5, 2.0):
        for q in (0.5, 0.75, 1.0, 1.5, 2.0):
            for a in np.linspace(0.05, 2, 40):
                for b in np.linspace(0.05, 2, 40):
                    pr = np.maximum(20 * np.log10(np.maximum(a * A0[:, 0] ** p + b * A0[:, 1] ** q, 1e-6)), -30)
                    e = float(np.sqrt(np.mean((pr - y) ** 2)))
                    if best is None or e < best[0]:
                        best = (e, p, q, a, b)
    law["wide"] = {"p": best[1], "q": best[2], "a": round(float(best[3]), 3), "b": round(float(best[4]), 3), "floor_db": -28.0,
                   "table": [[int(k), round(v, 2)] for k, v in sorted(d["wide_table"].items(), key=lambda kv: int(kv[0]))]}
    OUT.write_text(json.dumps(law, indent=1), encoding="utf-8")
    print("reverb rt60 models (n, leave-one-out rms of ln rt60):", {k: (n, round(e, 3)) for k, (n, e) in report.items()})
    print("damp", law["reverb"]["damp"], "mix/wet", law["reverb"]["mix_per_wet"], "none width/Width", law["reverb"]["none_width_per_w"])
    print("width: rms", round(best[0], 2), "dB over", len(y), "presets (std", round(float(y.std()), 2), ")", {k: law["wide"][k] for k in "pqab"})


if __name__ == "__main__":
    collect() if "--collect" in sys.argv else fit()
