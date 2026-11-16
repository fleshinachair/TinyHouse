#!/usr/bin/env python3
"""Serum's voice filter against the engine's, measured on a saw: which engine pots reproduce Serum's filter?

For each (model, cutoff, resonance, key tracking) the transfer of Serum's filter is read off a held saw as the dB change of each
harmonic against the unfiltered saw. The engine's filter is then searched (cutoff pot, resonance pot) for the pair whose transfer
matches. The Serum side is cached in calibration/probe_vfilter_serum.json, so re-analysis needs no Serum.
  probe_vfilter.py            measure and fit; writes calibration/probe_vfilter.json
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import deephouse2move as d2m
import test_render_calibration as H

SR = H.SR
CACHE = ROOT / "calibration/probe_vfilter_serum.json"
NOTE = 48
F0 = 440 * 2 ** ((NOTE - 69) / 12)
NH = 60                                  # harmonics read
DUR, HOLD = 1.3, 1.25
FLOOR = -45.0                            # transfer below this is "closed"; no point matching how deep


def harmonics(x):
    m = x[0, int(0.35 * SR):int(1.2 * SR)]
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m))))
    f = np.fft.rfftfreq(len(m), 1 / SR)
    out = []
    for k in range(1, NH + 1):
        if k * F0 > 18000:
            out.append(-120.0); continue
        i = np.searchsorted(f, k * F0)
        out.append(20 * np.log10(sp[max(i - 2, 0):i + 3].max() + 1e-12))
    return np.array(out)


class SerumPool:
    def __init__(self):
        self.s = H.Serum()

    def render(self, tree, vel=100):
        if self.s.audio_seconds > 36:
            self.s = H.Serum()
        return self.s.render(tree, [NOTE], DUR, HOLD, vel=vel)


def transfer(filt, base):
    return np.maximum(filt - base, FLOOR)


def main():
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t)
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    pool = SerumPool()
    if "base" not in cache:
        cache["base"] = harmonics(pool.render(tree0)).tolist()
    cases = []
    for model in ("MgL24", "MgL18", "L12", "MgL6", "H24", "B24"):
        for c in (0.35, 0.45, 0.55, 0.7):
            for r in (0.0, 12.0, 30.0):                   # kParamReso is a percentage
                cases.append((model, c, r, 0.0))
    for model in ("MgL24",):
        for c in (0.35, 0.55):
            cases.append((model, c, 0.0, 1.0))               # key tracking
    for case in cases:
        key = "|".join(map(str, case))
        if key in cache:
            continue
        model, c, r, kt = case
        t = H.with_filter(tree0, c, r, model)
        if kt:
            t["VoiceFilter0"]["plainParams"]["kParamKeyTrack"] = kt
        cache[key] = harmonics(pool.render(t)).tolist()
        CACHE.write_text(json.dumps(cache))
        print("serum", key, flush=True)
    # ---- engine side
    x0, m0 = H.tablor_render(tree0, [NOTE], DUR, HOLD)
    base_e = harmonics(x0)
    base_s = np.array(cache["base"])
    out = {}
    def render_pots(m, **pots):
        m2 = {"params": dict(m["params"]), "paths": m["paths"]}
        m2["params"].update(pots)
        return harmonics(H.run_cli(d2m.blob(m2), [NOTE], DUR, HOLD))
    for case in cases:
        model, c, r, kt = case
        key = "|".join(map(str, case))
        ts = transfer(np.array(cache[key]), base_s)
        t = H.with_filter(tree0, c, r, model)
        if kt:
            t["VoiceFilter0"]["plainParams"]["kParamKeyTrack"] = kt
        _, m = H.tablor_render(t, [NOTE], 0.3, 0.2)
        cur = {k: int(m["params"][k]) for k in ("flt_freq", "flt_res", "flt_key", "flt_type")}
        w = 1.0 / np.arange(1, NH + 1) ** 0.5
        ok = (np.arange(1, NH + 1) * F0 < 16000) & (base_s > base_s.max() - 70)
        def err(ff, rr):
            te = transfer(render_pots(m, flt_freq=ff, flt_res=rr), base_e)
            return float((w[ok] * (te[ok] - ts[ok]) ** 2).sum() / w[ok].sum())
        best = (1e9, cur["flt_freq"], cur["flt_res"])
        e_cur = err(cur["flt_freq"], cur["flt_res"])
        rr = cur["flt_res"]
        for step in (8, 2):
            lo = 0 if step == 8 else max(best[1] - 8, 0)
            hi = 127 if step == 8 else min(best[1] + 8, 127)
            for ff in range(lo, hi + 1, step):
                e = err(ff, rr)
                if e < best[0]:
                    best = (e, ff, rr)
            if step == 8:
                best = (best[0], best[1], rr)
        for rr in sorted({0, 16, 32, 48, 64, 80, 96, 112, 127}):
            e = err(best[1], rr)
            if e < best[0]:
                best = (e, best[1], rr)
        for ff in range(max(best[1] - 4, 0), min(best[1] + 4, 127) + 1):
            e = err(ff, best[2])
            if e < best[0]:
                best = (e, ff, best[2])
        out[key] = {"current": cur, "current_rms_db": e_cur ** 0.5, "best_freq": best[1], "best_res": best[2], "best_rms_db": best[0] ** 0.5}
        print(f"{key:24s} converter freq {cur['flt_freq']:3d} res {cur['flt_res']:3d} type {cur['flt_type']} (rms {e_cur ** 0.5:5.1f} dB)  ->  best freq {best[1]:3d} res {best[2]:3d} (rms {best[0] ** 0.5:5.1f} dB)", flush=True)
        (ROOT / "calibration/probe_vfilter.json").write_text(json.dumps(out, indent=1))
    os._exit(0)


if __name__ == "__main__":
    main()
