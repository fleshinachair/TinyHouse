#!/usr/bin/env python3
"""Serum's comb / flanger / phaser voice-filter models measured on white noise: the power spectrum of the filtered noise against the unfiltered noise is |H(f)|^2 on a fine grid, so a
comb's teeth and a phaser's notches show where a saw's harmonics would skip them.
  probe_comb.py MODEL [cutoff reso [var]] ...      one response per setting, printed as a coarse table and cached in calibration/probe_comb_serum.json
Usage example: probe_comb.py CombP 0.5 0.0   CombP 0.5 0.6"""
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import warpwave as W
import numpy as np
import test_render_calibration as H
SR = H.SR
CACHE = Path("calibration/probe_comb_serum.json")
DUR, HOLD = 3.2, 3.1
NFFT = 8192


def noise_tree(model=None, cutoff=0.5, reso=0.0, var=None, extra=None):
    t = H.init_tree(W.bt, table="S2 Tables/Default Shapes.wav", frames=9)
    t["Oscillator0"]["plainParams"] = {"kParamEnable": 0.0}
    t["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.5}
    t["Oscillator3"]["NoiseOsc3"] = {"plainParams": {"kParamNoiseType": "White"}}
    t["RoutingSlot3"] = {"plainParams": {"kParamRoutingDest": "kRoutingDestFilter"}}      # the noise reaches Filter 1 only when routed there
    if model:
        pp = {"kParamEnable": 1.0, "kParamFreq": cutoff, "kParamReso": reso, "kParamType": model}
        if var is not None:
            pp["kParamVar"] = var
        if extra:
            pp.update(extra)
        t["VoiceFilter0"]["plainParams"] = pp
    return t


def serum(tree):
    if W.pool.s.audio_seconds > 28:
        W.pool.s = H.Serum()
    return W.pool.s.render(tree, [48], DUR, HOLD)


def spectrum(x):
    seg = x[:, int(0.4 * SR):int(3.0 * SR)]
    acc = np.zeros(NFFT // 2 + 1)
    n = 0
    for ch in range(seg.shape[0]):
        for s in range(0, seg.shape[1] - NFFT, NFFT // 2):
            acc += np.abs(np.fft.rfft(seg[ch, s:s + NFFT] * np.hanning(NFFT))) ** 2; n += 1
    return acc / n


def response(model, cutoff, reso, var=None, extra=None):
    key = f"{model}|{cutoff}|{reso}|{var}|{extra}"
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if key in cache:
        return np.array(cache[key])
    if "base" not in cache:
        cache["base"] = spectrum(serum(noise_tree())).tolist()
    sp = spectrum(serum(noise_tree(model, cutoff, reso, var, extra)))
    h = 10 * np.log10(sp / np.array(cache["base"]) + 1e-12)
    cache[key] = h.tolist(); cache.setdefault("base", cache.get("base"))
    CACHE.write_text(json.dumps(cache))
    return h


def smooth(h, k=5):
    return np.convolve(h, np.ones(k) / k, mode="same")


if __name__ == "__main__":
    a = sys.argv[1:]
    runs = []
    i = 0
    while i < len(a):
        runs.append((a[i], float(a[i + 1]), float(a[i + 2]))); i += 3
    f = np.fft.rfftfreq(NFFT, 1 / SR)
    for m, c, r in runs:
        h = response(m, c, r)
        print(f"{m} cutoff {c} reso {r}")
        edges = [50, 100, 150, 200, 300, 400, 500, 650, 800, 1000, 1300, 1600, 2000, 2500, 3200, 4000, 5000, 6500, 8000, 10000, 13000, 16000]
        print("   Hz  " + " ".join("%6d" % e for e in edges[:-1]))
        print("   dB  " + " ".join("%6.1f" % h[(f >= lo) & (f < hi)].mean() for lo, hi in zip(edges[:-1], edges[1:])))
        # strongest peaks / notches of the smoothed response
        hs = smooth(h, 9)
        pk = [i for i in range(20, len(hs) - 20) if hs[i] == hs[i - 20:i + 20].max() and hs[i] > hs[20:-20].mean() + 3]
        print("   peaks Hz:", [int(f[i]) for i in pk[:14]])
    sys.stdout.flush(); os._exit(0)
