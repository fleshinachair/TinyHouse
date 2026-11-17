#!/usr/bin/env python3
"""Source 23 into an oscillator's Pan, on a plain saw: the left / right level for a few amounts and unison counts, against a stored Pan of the same oscillator.
Output: printed."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file
SR = H.SR
serum, base = P.start()
FACT = Path("C:/Users/Mat/OneDrive/Documents/Xfer/Serum 2 Presets/Presets/Factory")
_, pre = decode_preset_file(next(FACT.rglob("EP - Gentle DX Synth Keys.SerumPreset")))
slot = copy.deepcopy(pre["ModSlot2"])
print("base osc0:", base["Oscillator0"].get("plainParams"))
def lr(x):
    l, r = x[0, int(0.3 * SR):int(1.2 * SR)], x[1, int(0.3 * SR):int(1.2 * SR)]
    return 10 * np.log10((l ** 2).mean() / ((r ** 2).mean() + 1e-20) + 1e-20), 10 * np.log10((l ** 2).mean() + (r ** 2).mean() + 1e-20)
def run(uni=1, pan=None, amt=None, src=23, bip=1.0):
    b = copy.deepcopy(base)
    pp = {"kParamEnable": 1.0, "kParamVolume": 0.5, "kParamUnison": float(uni), "kParamDetune": 0.0}
    if pan is not None: pp["kParamPan"] = pan
    b["Oscillator0"]["plainParams"] = dict(b["Oscillator0"].get("plainParams", {}), **pp)
    if amt is not None:
        s = copy.deepcopy(slot); s["source"] = [src, 0]; s["destModuleID"] = 0
        s["plainParams"] = {"kParamAmount": amt, "kParamBipolar": bip}; b["ModSlot0"] = s
    return lr(serum.render(b, [48], 1.5, 1.2, vel=100))
import math
def t_of(db):
    lo, hi = 0.0, 1.0
    for _ in range(40):
        t = (lo + hi) / 2
        d = 20 * math.log10(((1 - t) * (1 + 0.172 * t)) / ((1 + t) * (1 - 0.172 * t)) + 1e-12)
        if d > db: lo = t
        else: hi = t
    return (lo + hi) / 2
print("stored pan p -> t:")
for pan in (10.0, 25.0, 40.0): print(" pan %5.1f  L/R %.2f dB  t=%.2f (pan/50=%.2f)" % ((pan,) + (run(1, pan=pan)[0], t_of(run(1, pan=pan)[0]), pan / 50)))
print("src 23 bipolar, unison 1, by amount:")
for amt in (10.0, 20.0, 30.0, 50.0, 75.0, -30.0):
    db = run(1, amt=amt)[0]; print(" amt %6.1f  L/R %.2f dB  t=%s" % (amt, db, ("%.2f" % t_of(db)) if db < 0 else ("-%.2f" % t_of(-db))))
os._exit(0)
