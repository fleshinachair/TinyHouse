#!/usr/bin/env python3
"""Serum's static waveshaper warps as transfer curves. A held note of the Basic Shapes sine (random phase off) goes through the warp; folded into one cycle and set against
the unwarped sine it gives out = f(in) over the whole -1..1 range (input in table units: the sine's peak is 1). The curve is then used to predict the Default Shapes saw through the
same warp (cross-check: relative rms error).  Output: calibration/warp_curves_measured.json {mode: {"amounts": [...], "x": [...], "f": [[...]], "err": [...]}}.
Usage: probe_warp_curves.py [mode ...]"""
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import warpwave as W
import numpy as np
SAW = ("S2 Tables/Default Shapes.wav", 9, 0.0); SIN = ("Analog/Basic Shapes.wav", 7, 0.0)
MODES = sys.argv[1:] or ["kDistSoftClip", "kDistDiode2", "kDistAsym", "kDistZeroSquare", "kDistRectify", "kDistSineShaper", "kQuantize"]
AMTS = [round(0.025 * i, 3) for i in range(41)]
XG = np.linspace(-1.0, 1.0, 129)
def rend(*a, **k):
    try:
        return W.render(*a, **k)
    except RuntimeError:                      # silent render (a warp that removes everything)
        return np.full(int(W.SR * 1.3), 1e-9)
xs = W.cycle(rend(*SAW)); xi = W.cycle(rend(*SIN))          # the first cycle() call fixes the phase origin: it must be the unwarped saw
S = np.abs(xi).max(); print("sine peak", S, flush=True)
def curve(yi):
    """f on XG from one sine cycle: rising and falling branches averaged."""
    n = len(xi); x = xi / S; y = yi / S
    up = np.arange(n)[np.gradient(x) > 0]; dn = np.arange(n)[np.gradient(x) <= 0]
    out = []
    for ix in (up, dn):
        o = np.argsort(x[ix]); out.append(np.interp(XG, x[ix][o], y[ix][o]))
    return (out[0] + out[1]) / 2, np.abs(out[0] - out[1]).max()
res = {}
for md in MODES:
    r = {"amounts": AMTS, "x": XG.tolist(), "f": [], "err": [], "branch": []}
    for a in AMTS:
        yi = W.cycle(rend(SIN[0], SIN[1], SIN[2], md, a)); ys = W.cycle(rend(SAW[0], SAW[1], SAW[2], md, a))
        f, br = curve(yi)
        pred = np.interp(xs / S, XG, f); core = slice(40, W.NB - 40)
        e = float(np.sqrt(np.mean((pred - ys / S)[core] ** 2)) / (np.sqrt(np.mean((ys / S)[core] ** 2)) + 1e-9))
        r["f"].append(f.tolist()); r["err"].append(round(e, 4)); r["branch"].append(round(float(br), 4))
        print("%-16s %.2f saw-prediction err %.3f  branch gap %.3f  f(+-0.5,+-1) = %.3f %.3f %.3f %.3f" % (md, a, e, br, f[32], f[96], f[0], f[128]), flush=True)
    res[md] = r
    Path("calibration/warp_curves_measured.json").write_text(json.dumps(res))
sys.stdout.flush(); os._exit(0)
