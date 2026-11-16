#!/usr/bin/env python3
"""Serum's voice-filter drive against the engine's: MgL24 at cutoff 0.45 and drive 0/15/30/50 %. For each, the engine cutoff and drive pots
that match Serum's harmonic transfer (a free overall gain offset is removed and reported). Output: calibration/probe_vdrive.json."""
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

V.NH = 80
DRIVES = (0.0, 15.0, 30.0, 50.0)
CUT = 0.45


def main():
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t)
    pool = V.SerumPool()
    base_s = V.harmonics(pool.render(tree0))
    x0, _ = H.tablor_render(tree0, [V.NOTE], V.DUR, V.HOLD)
    base_e = V.harmonics(x0)
    w = 1.0 / np.arange(1, V.NH + 1) ** 0.5
    ok = (np.arange(1, V.NH + 1) * V.F0 < 16000) & (base_s > base_s.max() - 70)
    out = {}
    for d in DRIVES:
        t = H.with_filter(tree0, CUT, 0.0, "MgL24")
        t["VoiceFilter0"]["plainParams"]["kParamDrive"] = d
        hs = V.harmonics(pool.render(t))
        ts = V.transfer(hs, base_s)
        _, m = H.tablor_render(t, [V.NOTE], 0.3, 0.2)
        cur = {k: int(m["params"][k]) for k in ("flt_freq", "flt_drive")}
        def err(ff, dd):
            m2 = {"params": dict(m["params"]), "paths": m["paths"]}
            m2["params"].update(flt_freq=ff, flt_drive=dd)
            te = V.transfer(V.harmonics(H.run_cli(d2m.blob(m2), [V.NOTE], V.DUR, V.HOLD)), base_e)
            diff = (te - ts)[ok]
            off = float(np.average(diff, weights=w[ok]))
            return float(np.sqrt(np.average((diff - off) ** 2, weights=w[ok]))), off
        best = (1e9, 0, 0, 0.0)
        for dd in (0, 16, 32, 48, 64, 80, 96, 127):
            for ff in range(cur["flt_freq"] - 12, cur["flt_freq"] + 13, 3):
                e, off = err(ff, dd)
                if e < best[0]:
                    best = (e, ff, dd, off)
        for dd in range(max(best[2] - 8, 0), min(best[2] + 8, 127) + 1, 2):
            for ff in range(best[1] - 3, best[1] + 4):
                e, off = err(ff, dd)
                if e < best[0]:
                    best = (e, ff, dd, off)
        e_cur, off_cur = err(cur["flt_freq"], cur["flt_drive"])
        out[str(d)] = {"current": cur, "current_rms": e_cur, "current_offset_db": off_cur, "best_freq": best[1], "best_drive": best[2], "best_rms": best[0], "best_offset_db": best[3]}
        print(f"drive {d:4.0f}%: converter freq {cur['flt_freq']} drive {cur['flt_drive']} (rms {e_cur:.1f}, gain offset {off_cur:+.1f} dB) -> best freq {best[1]} drive {best[2]} (rms {best[0]:.1f}, gain offset {best[3]:+.1f} dB)", flush=True)
    (ROOT / "calibration/probe_vdrive.json").write_text(json.dumps(out, indent=1))
    os._exit(0)


main()
