#!/usr/bin/env python3
"""A macro route into an oscillator's pitch and warp knobs in Serum (the converter's static fold, deephouse2move.physical). Usage: probe_macro_osc.py pitch|warp.
Measured: Coarse pitch 128 semitones per unit (route amount x source), the Pitch knob 25 in whole semitones, Octave 10 octaves in whole octaves, Fine 2 semitones (stops at +-1),
a Warp knob adds amount x source on its 0..1 scale (bipolar routes: the source minus a half)."""
import os, sys, copy
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration")); os.chdir(ROOT)
import numpy as np
import test_render_calibration as H
import probe_vfilter as V
import voice_survey as VS
pool = V.SerumPool()
_, base_t = H.load_preset("Keys - Loop")
def tree(route=None, macro=100.0, osc=None, wt=None):
    t = H.init_tree(base_t)
    t["Oscillator0"]["plainParams"] = {"kParamVolume": 0.5, **(osc or {})}
    if wt: t["Oscillator0"]["WTOsc0"]["plainParams"] = dict(wt)
    if route:
        dt, name, pid, amt, bip = route
        t["Macro0"] = {"name": "M", "plainParams": {"kParamValue": macro}}
        pp = {"kParamAmount": amt}
        if bip: pp["kParamBipolar"] = 1.0
        t["ModSlot0"] = {"destModuleID": 0, "destModuleParamID": pid, "destModuleParamName": name, "destModuleTypeString": dt, "plainParams": pp, "source": [25, 0]}
    return t
def f0_of(x):
    m = x[0, int(0.3 * H.SR):int(1.0 * H.SR)]
    n = 1 << 18
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)), n)); f = np.fft.rfftfreq(n, 1 / H.SR)
    lo, hi = np.searchsorted(f, 20), np.searchsorted(f, 3000)
    return f[lo + int(np.argmax(sp[lo:hi]))]
def bands(x): return VS.bands(x)
def dist(a, b):
    a, b = bands(a), bands(b); a, b = a - a.max(), b - b.max()
    return float(np.sqrt(np.mean(np.maximum(a - b, -999) ** 2)))

def run_pitch():
    base = f0_of(pool.render(tree()))
    print("base", base, flush=True)
    for name, pid, amts in (("kParamPitch", 4, (5, 10, 25, 50)), ("kParamCoarsePit", 6, (5, 10, 25)), ("kParamOctave", 3, (5, 10)), ("kParamFine", 5, (25, 50, 100))):
        for macro in (100.0, 50.0):
            for amt in amts:
                for bip in (0, 1):
                    f1 = f0_of(pool.render(tree(("Oscillator", name, pid, amt, bip), macro)))
                    st = 12 * np.log2(f1 / base)
                    print(f"{name:16s} macro {macro:5.1f} amount {amt:3d} {'bi ' if bip else 'uni'}: {st:+8.3f} st  ({st / (amt / 100 * (macro / 100 - (0.5 if bip else 0))):+.2f} st per unit-source)", flush=True)
    
    

def run_warp():
    def wt(mode, a, second=False):
        return {"kParamWarpMenu2" if second else "kParamWarpMenu": mode, "kParamWarp2" if second else "kParamWarp": a}
    for mode in ("kDistTube", "kSync", "kFlip", "kBendP"):
        for second in (False,):
            name, pid = "kParamWarp", 0
            statics = {a: pool.render(tree(wt=wt(mode, a))) for a in (0.0, 0.2, 0.3, 0.4, 0.6, 0.8, 1.0)}
            for macro, amt, bip, sbase in ((100.0, 60.0, 0, 0.0), (50.0, 60.0, 0, 0.0), (100.0, 40.0, 1, 0.0), (100.0, 40.0, 0, 0.3), (100.0, 60.0, 0, 0.5)):
                r = pool.render(tree(("WTOsc", name, pid, amt, bip), macro, wt=wt(mode, sbase)))
                ds = {a: dist(r, s) for a, s in statics.items()}
                best = min(ds, key=ds.get)
                x = macro / 100.0; eff = amt / 100.0 * ((x - 0.5) if bip else x)
                print(f"{mode:10s} base {sbase} macro {macro:5.1f} amount {amt:4.0f} {'bi ' if bip else 'uni'}: effective {eff:+.2f} -> nearest static {best} (dist {ds[best]:.2f}); at base+eff {sbase+eff:.2f}: {ds.get(round(sbase+eff,1), float('nan')):.2f}", flush=True)
    
    

if __name__ == '__main__':
    {'pitch': run_pitch, 'warp': run_warp}[sys.argv[1]]()
    sys.stdout.flush(); os._exit(0)
