#!/usr/bin/env python3
"""White noise through Serum's voice filters against the engine's, end to end (the real converter): the filter's transfer is the third-octave band level of the
filtered noise minus that of the same noise with the filter off, on each side.  Cases: Filter 1 or Filter 2 (balance 100), model, cutoff knob, resonance.
Serum side cached in calibration/probe_voice_filter_noise_serum.json.
  probe_voice_filter_noise.py [--models MgL24,...] [--cutoffs .3,.5] [--resos 0,41.6] [--filters 1,2]   prints, per case, engine - Serum in dB per band."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import argparse, copy, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H
import deephouse2move as d2m

SR = H.SR
CACHE = ROOT / "calibration/probe_voice_filter_noise_serum.json"
CENTRES = [50 * 2 ** (k / 3) for k in range(0, 25) if 50 * 2 ** (k / 3) <= 17000]
NOTE = 48
DUR, HOLD = 2.0, 1.95


def bands(x):
    m = x.mean(axis=0)[int(0.5 * SR):int(1.9 * SR)]
    s = np.abs(np.fft.rfft(m * np.hanning(len(m)))) ** 2
    f = np.fft.rfftfreq(len(m), 1 / SR)
    return np.array([10 * np.log10(s[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))].sum() + 1e-20) for c in CENTRES])


def tree_for(base, which, model, cutoff, reso, keytrack=0.0):
    t = copy.deepcopy(base)
    t["Oscillator3"]["NoiseOsc3"] = {"plainParams": {"kParamNoiseType": "White"}}
    rs = {"kParamRoutingDest": "kRoutingDestFilter"}
    if which == 2:
        rs["kParamFilterBalance"] = 100.0
    t["RoutingSlot3"] = {"plainParams": rs}
    off = {"kParamEnable": 0.0}
    on = {"kParamEnable": 1.0, "kParamFreq": cutoff, "kParamReso": reso, "kParamType": model, "kParamKeyTrack": keytrack}
    if model is None:
        on = off
    if which == 1:
        t["VoiceFilter0"]["plainParams"] = on
        t["VoiceFilter1"]["plainParams"] = off
    else:
        t["VoiceFilter0"]["plainParams"] = off
        t["VoiceFilter1"]["plainParams"] = on
    return t


def engine_bands(t):
    x, m = H.tablor_render(t, [NOTE], DUR, HOLD)
    return bands(x), m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="MgL24,L24,MgL18,L18,MgL12,L12,MgL6,L6")
    ap.add_argument("--cutoffs", default="0.25,0.35,0.45,0.55,0.65,0.75")
    ap.add_argument("--resos", default="0")
    ap.add_argument("--filters", default="1")
    a = ap.parse_args()
    serum, base = P.start()
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}

    def serum_bands(key, t):
        if key not in cache:
            cache[key] = bands(serum.render(t, [NOTE], DUR, HOLD)).tolist()
            CACHE.write_text(json.dumps(cache))
        return np.array(cache[key])

    print("centres", " ".join("%5.0f" % c for c in CENTRES))
    refs = {}
    for which in map(int, a.filters.split(",")):
        t0 = tree_for(base, which, None, 0, 0)
        refs[which] = (serum_bands(f"off|{which}", t0), engine_bands(t0)[0])
        for model in a.models.split(","):
            for c in map(float, a.cutoffs.split(",")):
                for r in map(float, a.resos.split(",")):
                    t = tree_for(base, which, model, c, r)
                    ts = serum_bands(f"{which}|{model}|{c}|{r}", t) - refs[which][0]
                    be, m = engine_bands(t)
                    te = be - refs[which][1]
                    p = m["params"]
                    pre = "flt2" if which == 2 else "flt"
                    d = np.maximum(te, -90) - np.maximum(ts, -90)
                    print(f"F{which} {model:6s} c{c:.2f} r{r:4.1f} pot {p[pre + '_freq']:3d}/{p[pre + '_res']:3d}/t{p[pre + '_type']}  serum:", " ".join("%5.0f" % v for v in ts), flush=True)
                    print(f"{'':36s}diff :", " ".join("%5.0f" % v for v in d), flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
