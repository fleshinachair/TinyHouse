#!/usr/bin/env python3
"""The Filter FX moved by an envelope: how far Serum's closes over time, against the engine's FX Filter stage for the converter's
translation of the same patch. A held saw, no voice filter, one L12 Filter FX at a base cutoff, Env2 -> its cutoff at 50 % (or the case's own amount).
Reports, per time, the harmonic where the transfer first drops 10 dB below the unfiltered saw (higher = more open)."""
import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import deephouse2move as d2m
import probe_vfilter as V
import test_render_calibration as H
import fx_fingerprint as FP

SR = H.SR
TYPE = sys.argv[1] if len(sys.argv) > 1 else "L12"          # the Filter FX model: probe_fxfilter_env.py [L12|MgL24|...]
NOTE, F0 = 48, 440 * 2 ** ((48 - 69) / 12)
NH, WIN = 60, 0.06
TIMES = (0.03, 0.09, 0.15, 0.24, 0.36, 0.54, 0.80, 1.20)
CASES = {"base .3 D1.0 S0": (0.3, 0.001, 1.0, 0.0, 0.2), "base .1 D1.0 S0": (0.1, 0.001, 1.0, 0.0, 0.2),
         "base .1 D2.0 S.15": (0.1, 0.004, 2.0, 0.15, 0.2), "base .05 D0.5 S0": (0.05, 0.001, 0.5, 0.0, 0.2),
         # higher bases and the amount most presets carry (Lead - Space & Time: MgL18 at .497, Env 2 -> cutoff 29 %): the last field is the route's amount in %
         "base .5 D1.0 S0 a29": (0.5, 0.001, 1.0, 0.0, 0.2, 29), "base .5 D1.0 S.5 a29": (0.5, 0.001, 1.0, 0.5, 0.2, 29), "base .4 D1.0 S0 a50": (0.4, 0.001, 1.0, 0.0, 0.2, 50),
         "base .6 D1.0 S0 a29": (0.6, 0.001, 1.0, 0.0, 0.2, 29)}


def harm(x, t0):
    m = x[0, int(t0 * SR):int((t0 + WIN) * SR)]
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)), 1 << 15))
    f = np.fft.rfftfreq(1 << 15, 1 / SR)
    return np.array([20 * np.log10(sp[max(np.searchsorted(f, k * F0) - 3, 0):np.searchsorted(f, k * F0) + 4].max() + 1e-12) for k in range(1, NH + 1)])


def cutoff_harm(h, base):
    t = h - base
    below = np.where(t < -10)[0]
    return int(below[0]) + 1 if below.size else NH


def main():
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t)
    _, oskar = H.load_preset("Bass - oskar med k")
    node = next(n for n in FP.rack0(oskar) if FP.kind_of(n) == "Filter")
    pool = V.SerumPool()
    base_s = harm(pool.render(tree0), 0.6)
    x0, _ = H.tablor_render(tree0, [NOTE], 1.4, 1.3)
    base_e = harm(x0, 0.6)
    for name, (c, a, d, s, r, *amt) in CASES.items():
        t = copy.deepcopy(tree0)
        nd = copy.deepcopy(node)
        nd["FXFilter"]["plainParams"] = {"kParamFreq": c, "kParamType": TYPE, "kParamReso": 10.0}
        t["FXRack0"]["FX"] = [nd]
        t["Env1"]["plainParams"] = {"kParamAttack": a, "kParamDecay": d, "kParamSustain": s, "kParamRelease": r}
        t["ModSlot0"] = {"destModuleID": 0, "destModuleParamID": 3, "destModuleParamName": "kParamFreq", "destModuleTypeString": "FXFilter",
                         "plainParams": {"kParamAmount": amt[0] if amt else 50}, "source": [3, 0]}
        xs = pool.render(t)
        _, m = H.tablor_render(t, [NOTE], 0.3, 0.2)
        xe = H.run_cli(d2m.blob(m), [NOTE], 1.4, 1.3)
        p = m["params"]
        print(f"{name:18s} engine fxf_on {p.get('fxf_on')} freq {p.get('fxf_freq')} env {p.get('fxf_env')} flt_d {p.get('flt_d')} flt_s {p.get('flt_s')}")
        print("   serum  " + " ".join(f"{cutoff_harm(harm(xs, tt), base_s):3d}" for tt in TIMES))
        print("   engine " + " ".join(f"{cutoff_harm(harm(xe, tt), base_e):3d}" for tt in TIMES), flush=True)
    os._exit(0)


main()
