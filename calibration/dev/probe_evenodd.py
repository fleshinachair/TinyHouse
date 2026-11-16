#!/usr/bin/env python3
"""Serum's Even/Odd warp is a mix of the waveform with itself half a cycle later: y(p) = a1 x(p) + a2 x(p + 1/2). Fitted by least squares on two different tables (Default Shapes saw
and a Basic Shapes frame) at amounts 0..1; the coefficients must agree between the tables.  Output: calibration/evenodd_measured.json {"amounts": [...], "coef": [[a1, a2], ...], "err": [...]}."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import warpwave as W
import numpy as np
SAW = ("S2 Tables/Default Shapes.wav", 9, 0.0); OTH = ("Analog/Basic Shapes.wav", 7, 0.5)
def rend(*a, **k):
    try: return W.render(*a, **k)
    except RuntimeError: return np.full(int(W.SR * 1.3), 1e-9)
xs = W.cycle(rend(*SAW)); xo = W.cycle(rend(*OTH))
AM = [round(0.05 * i, 2) for i in range(21)]
def fit(x, y):
    A = np.c_[x, np.roll(x, -W.NB // 2)]; c = slice(30, W.NB - 30)
    co, *_ = np.linalg.lstsq(A[c], y[c], rcond=None); e = np.sqrt(np.mean((A[c] @ co - y[c]) ** 2)) / (np.sqrt(np.mean(y[c] ** 2)) + 1e-9)
    return co, e
out = {"amounts": AM, "saw": [], "other": [], "err_saw": [], "err_other": []}
for a in AM:
    ys = W.cycle(rend(SAW[0], SAW[1], SAW[2], "kEvenOdd", a)); yo = W.cycle(rend(OTH[0], OTH[1], OTH[2], "kEvenOdd", a))
    c1, e1 = fit(xs, ys); c2, e2 = fit(xo, yo)
    out["saw"].append(c1.tolist()); out["other"].append(c2.tolist()); out["err_saw"].append(float(e1)); out["err_other"].append(float(e2))
    print("a %.2f  saw coef %+.3f %+.3f (err %.3f)   other-table coef %+.3f %+.3f (err %.3f)" % (a, *c1, e1, *c2, e2), flush=True)
Path("calibration/evenodd_measured.json").write_text(json.dumps(out))
sys.stdout.flush(); os._exit(0)
