#!/usr/bin/env python3
"""Fit probe_filterwarp.json: each (mode, amount) as a first-order filter with corner c (Hz) on harmonics k * F0 of notes 48 and 60. Prints c per amount and the rms error."""
import json, sys
from pathlib import Path
import numpy as np
D = json.loads((Path(__file__).resolve().parent / "probe_filterwarp.json").read_text())
FLOOR = -40.0
res = {}
for mode in ("kFilterLPF", "kFilterHPF"):
    for a in sorted({float(k.split("|")[2]) for k in D if k.startswith(mode)}):
        best = None
        for lc in np.arange(0.0, 6.0, 0.01):                      # log10 corner, Hz
            c = 10 ** lc
            e = []
            for note in (48, 60):
                key = f"{mode}|{note}|{a}"
                if key not in D:
                    continue
                f0 = 440 * 2 ** ((note - 69) / 12)
                tr = np.array(D[key])
                f = f0 * np.arange(1, len(tr) + 1)
                r = f / c
                h = -10 * np.log10(1 + r * r) if mode == "kFilterLPF" else 10 * np.log10(r * r / (1 + r * r))
                m = tr > FLOOR
                e += list((np.maximum(h, FLOOR) - tr)[m & (f < 16000)])
            if len(e) < 6:
                continue
            rms = float(np.sqrt(np.mean(np.square(e))))
            if best is None or rms < best[0]:
                best = (rms, c)
        res[f"{mode}|{a}"] = best
        print(f"{mode[7:]} amt {a:4.2f}: corner {best[1]:9.1f} Hz  rms {best[0]:.2f} dB  log2(c) {np.log2(best[1]):6.2f}")
Path(__file__).with_name("filterwarp_fit.json").write_text(json.dumps({k: {"corner_hz": round(v[1], 2), "rms_db": round(v[0], 2)} for k, v in res.items()}, indent=1))
