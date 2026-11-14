#!/usr/bin/env python3
"""The comb / flanger voice filters against Serum on a held saw (deterministic, unlike noise): the dB change of each harmonic 1..60 against that side's own unfiltered saw.
Prints the mean-power difference (engine minus Serum, over the harmonics below 12 kHz) and the rms of the per-harmonic dB difference. Because the comb's teeth are 425 Hz apart and the
harmonics 130.8 Hz, the harmonics sample the response at many points of the teeth.   Usage: validate_comb_saw.py [model:cut:reso ...]"""
import copy, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import warpwave as W
import numpy as np
import probe_vfilter as V
import test_render_calibration as H
V.NH = 60
t0 = W.tree("S2 Tables/Default Shapes.wav", 9)
CASES = [(m, 0.5, r) for m in ("CombP", "CombN", "FlangeP", "FlangeN") for r in (0, 20, 40, 60, 80, 90)]
if len(sys.argv) > 1:
    CASES = [(a.split(":")[0], float(a.split(":")[1]), float(a.split(":")[2])) for a in sys.argv[1:]]


def tree_with(model, cut, reso):
    t = copy.deepcopy(t0)
    t["VoiceFilter0"]["plainParams"] = {"kParamEnable": 1.0, "kParamFreq": cut, "kParamReso": reso, "kParamType": model}
    return t


bs = V.harmonics(W.pool.render(t0)); be = V.harmonics(H.tablor_render(t0, [48], V.DUR, V.HOLD)[0])
use = (np.arange(1, 61) * V.F0 < 12000) & (bs > bs.max() - 60)
for model, cut, reso in CASES:
    hs = V.harmonics(W.pool.render(tree_with(model, cut, reso))) - bs
    he = V.harmonics(H.tablor_render(tree_with(model, cut, reso), [48], V.DUR, V.HOLD)[0]) - be
    ps = 10 * np.log10(np.mean(10 ** (hs[use] / 10))); pe = 10 * np.log10(np.mean(10 ** (he[use] / 10)))
    d = (he - hs)[use]
    print("%-8s cut %.2f reso %3d   mean power Serum %+6.1f dB  engine %+6.1f dB  (engine - Serum %+5.1f)   per-harmonic rms diff %5.2f dB   max %5.1f" %
          (model, cut, reso, ps, pe, pe - ps, np.sqrt(np.mean(d ** 2)), np.abs(d).max()), flush=True)
sys.stdout.flush(); os._exit(0)
