#!/usr/bin/env python3
"""Serum's filter key tracking, over the keyboard: the effective cutoff (as the engine cutoff pot, key tracking off, that gives the same
harmonic transfer) of MgL24 at a fixed cutoff for notes 36..84, with the key-track knob at 0, 0.5 and 1. Output: calibration/probe_vkey.json."""
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
import probe_vfilter as V
import test_render_calibration as H

NOTES = (36, 48, 60, 72, 84)
KTS = (0.0, 0.5, 1.0)
CUT = 0.5
V.NH = 80
CACHE = ROOT / "calibration/probe_vkey_serum.json"


def main():
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t)
    pool = V.SerumPool()
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    def setnote(n):
        V.NOTE = n
        V.F0 = 440 * 2 ** ((n - 69) / 12)
    def serum_h(tree, n):
        setnote(n)
        s = pool.s if pool.s.audio_seconds <= 36 else None
        if s is None:
            pool.s = H.Serum()
        return V.harmonics(pool.s.render(tree, [n], V.DUR, V.HOLD))
    for n in NOTES:
        if f"base{n}" not in cache:
            cache[f"base{n}"] = serum_h(tree0, n).tolist()
        for kt in KTS:
            k = f"{n}|{kt}"
            if k not in cache:
                t = H.with_filter(tree0, CUT, 0.0, "MgL24")
                t["VoiceFilter0"]["plainParams"]["kParamKeyTrack"] = kt
                cache[k] = serum_h(t, n).tolist()
                CACHE.write_text(json.dumps(cache))
                print("serum", k, flush=True)
    out = {}
    for n in NOTES:
        setnote(n)
        x0, _ = H.tablor_render(tree0, [n], V.DUR, V.HOLD)
        base_e = V.harmonics(x0)
        base_s = np.array(cache[f"base{n}"])
        _, m = H.tablor_render(H.with_filter(tree0, CUT, 0.0, "MgL24"), [n], 0.3, 0.2)
        w = 1.0 / np.arange(1, V.NH + 1) ** 0.5
        ok = (np.arange(1, V.NH + 1) * V.F0 < 16000) & (base_s > base_s.max() - 70)
        for kt in KTS:
            ts = V.transfer(np.array(cache[f"{n}|{kt}"]), base_s)
            def err(ff):
                m2 = {"params": dict(m["params"]), "paths": m["paths"]}
                m2["params"].update(flt_freq=ff, flt_key=0)
                te = V.transfer(V.harmonics(H.run_cli(d2m.blob(m2), [n], V.DUR, V.HOLD)), base_e)
                return float((w[ok] * (te[ok] - ts[ok]) ** 2).sum() / w[ok].sum()) ** 0.5
            best = min(range(0, 128, 3), key=err)
            best = min(range(max(best - 3, 0), min(best + 3, 127) + 1), key=err)
            out[f"{n}|{kt}"] = {"pot": best, "rms": err(best)}
            print(f"note {n} key track {kt}: effective cutoff pot {best:3d} (rms {err(best):.1f} dB)", flush=True)
    (ROOT / "calibration/probe_vkey.json").write_text(json.dumps(out, indent=1))
    os._exit(0)


main()
