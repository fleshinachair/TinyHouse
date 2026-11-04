#!/usr/bin/env python3
"""Serum's Formant voice filters (FormantONE / TWO / TWB): where do their peaks sit, and how do they move with Var and Freq?

A held saw (note 48, 60 harmonics) through Voice Filter 1 of the init patch set to a Formant model; the transfer (dB against the unfiltered saw) is read off every harmonic and the
peaks of its smoothed shape are listed.   probe_formant.py [model ...]     writes calibration/probe_formant.json (resumable)
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import probe_vfilter as V
import test_render_calibration as H

OUT = ROOT / "calibration/probe_formant.json"


def peaks(tr, f0, n=4):
    """The strongest local maxima of the transfer (Hz, dB) after a 3-harmonic smoothing."""
    s = np.convolve(tr, np.ones(3) / 3, mode="same")
    idx = [i for i in range(2, len(s) - 2) if s[i] >= s[i - 1] and s[i] > s[i + 1] and s[i] > np.median(s) + 3]
    idx = sorted(idx, key=lambda i: -s[i])[:n]
    return sorted((round((i + 1) * f0), round(float(s[i]), 1)) for i in idx)


def main():
    models = sys.argv[1:] or ["FormantTWO", "FormantONE"]
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t)
    pool = V.SerumPool()
    base = V.harmonics(pool.render(tree0))
    res = json.loads(OUT.read_text()) if OUT.exists() else {}
    for model in models:
        for cut in (0.1277, 0.35, 0.55):
            for var in (0.0, 12.85, 25.0, 50.0, 75.0, 100.0):
                key = f"{model}|{cut}|{var}"
                if key not in res:
                    t = H.with_filter(tree0, cut, 0.0, model)
                    t["VoiceFilter0"]["plainParams"]["kParamVar"] = var
                    res[key] = V.transfer(V.harmonics(pool.render(t)), base).tolist()
                    OUT.write_text(json.dumps(res))
                tr = np.array(res[key])
                print(f"{model:11s} freq {cut:6.4f} var {var:5.1f}: peaks Hz/dB {peaks(tr, V.F0)}   mean {tr.mean():+.1f} dB", flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
