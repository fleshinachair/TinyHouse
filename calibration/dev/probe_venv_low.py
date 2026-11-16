#!/usr/bin/env python3
"""The filter envelope over time: Serum's effective cutoff (the static cutoff with the same harmonic transfer, read in 60 ms windows) while
Env2 moves it, against the engine's for the converter's translation of the same patch. MgL24 at a base cutoff, Env2 -> cutoff at 50 %.
Reported in semitones above the base cutoff, so the two are comparable whatever the pot scales. Output: calibration/probe_venv_low.json."""
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

SR = H.SR
NOTE, F0 = 48, 440 * 2 ** ((48 - 69) / 12)
NH = 60
WIN = 0.06
TIMES = (0.03, 0.09, 0.15, 0.24, 0.36, 0.54, 0.80, 1.20)
BASE = 0.085
CASES = {"oskar D2.0 S.15": (0.004, 2.0, 0.147, 0.8), "D1.0 S0": (0.001, 1.0, 0.0, 0.2), "D2.0 S.5": (0.001, 2.0, 0.5, 0.2)}


def harm(x, t0):
    m = x[0, int(t0 * SR):int((t0 + WIN) * SR)]
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)), 1 << 15))
    f = np.fft.rfftfreq(1 << 15, 1 / SR)
    out = []
    for k in range(1, NH + 1):
        i = np.searchsorted(f, k * F0)
        out.append(20 * np.log10(sp[max(i - 3, 0):i + 4].max() + 1e-12))
    return np.array(out)


def main():
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t)
    pool = V.SerumPool()
    cache_p = ROOT / "calibration/probe_venv_low_serum.json"
    cache = json.loads(cache_p.read_text()) if cache_p.exists() else {}
    def serum(tree):
        return pool.render(tree)
    # static references, both systems
    if "base" not in cache:
        cache["base"] = harm(serum(tree0), 0.6).tolist()
    for c in [round(0.05 * i, 2) for i in range(1, 20)]:
        if f"s{c}" not in cache:
            cache[f"s{c}"] = harm(serum(H.with_filter(tree0, c, 0.0, "L24")), 0.6).tolist()
    cache_p.write_text(json.dumps(cache))
    base_s = np.array(cache["base"])
    grid = [round(0.05 * i, 2) for i in range(1, 20)]
    ok = (np.arange(1, NH + 1) * F0 < 16000) & (base_s > base_s.max() - 70)
    def tr(h, b):
        return np.maximum(h - b, V.FLOOR)
    ref_s = {c: tr(np.array(cache[f"s{c}"]), base_s) for c in grid}
    def eq_s(h):
        t = tr(h, base_s)
        errs = np.array([np.sqrt(((ref_s[c][ok] - t[ok]) ** 2).mean()) for c in grid])
        i = int(np.argmin(errs))
        if 0 < i < len(grid) - 1:
            a, b, c3 = errs[i - 1], errs[i], errs[i + 1]
            return grid[i] + (a - c3) / (2 * (a - 2 * b + c3) + 1e-12) * 0.05
        return grid[i]
    # engine statics
    x0, m0 = H.tablor_render(tree0, [NOTE], 1.3, 1.25)
    base_e = harm(x0, 0.6)
    _, mt = H.tablor_render(H.with_filter(tree0, BASE, 0.0, "L24"), [NOTE], 0.3, 0.2)
    pots = list(range(0, 128, 2))
    ref_e = {}
    for p in pots:
        m2 = {"params": dict(mt["params"]), "paths": mt["paths"]}
        m2["params"].update(flt_freq=p, flt_env=64, flt_key=0)
        ref_e[p] = tr(harm(H.run_cli(d2m.blob(m2), [NOTE], 1.3, 1.25), 0.6), base_e)
    def eq_e(h):
        t = tr(h, base_e)
        errs = np.array([np.sqrt(((ref_e[p][ok] - t[ok]) ** 2).mean()) for p in pots])
        i = int(np.argmin(errs))
        if 0 < i < len(pots) - 1:
            a, b, c3 = errs[i - 1], errs[i], errs[i + 1]
            return pots[i] + (a - c3) / (2 * (a - 2 * b + c3) + 1e-12) * 2
        return pots[i]
    out = {}
    for name, (a, d, s, r) in CASES.items():
        t = H.with_filter(tree0, BASE, 0.0, "L24")
        t["Env1"]["plainParams"] = {"kParamAttack": a, "kParamDecay": d, "kParamSustain": s, "kParamRelease": r}
        t["ModSlot0"] = {"destModuleID": 0, "destModuleParamID": 3, "destModuleParamName": "kParamFreq", "destModuleTypeString": "VoiceFilter",
                         "plainParams": {"kParamAmount": 48.6}, "source": [3, 0]}
        xs = serum(t)
        _, m = H.tablor_render(t, [NOTE], 0.3, 0.2)
        xe = H.run_cli(d2m.blob(m), [NOTE], 1.4, 1.3)
        rows = []
        for tt in TIMES:
            cs = eq_s(harm(xs, tt))
            pe = eq_e(harm(xe, tt))
            rows.append((tt, (cs - BASE) * d2m.CUTOFF_NOTES, pe))
        pe0 = float(m["params"]["flt_freq"])
        out[name] = {"flt_env": int(m["params"]["flt_env"]), "base_pot": pe0, "rows": rows}
        print(f"{name:16s} (engine flt_env {m['params']['flt_env']}, flt_d {m['params']['flt_d']}, flt_s {m['params']['flt_s']}):  "
              + "  ".join(f"{tt:.2f}s S{ds:+5.1f}/E{pe - pe0:+5.1f}" for tt, ds, pe in rows), flush=True)
    (ROOT / "calibration/probe_venv_low.json").write_text(json.dumps(out, indent=1))
    os._exit(0)


main()
