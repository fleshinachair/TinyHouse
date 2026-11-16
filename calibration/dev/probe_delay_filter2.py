#!/usr/bin/env python3
"""Serum's Delay filter, measured finely: noise through a Delay at Feedback 0 and Wet 100 (the stored Freq / BW), the gain in third-octave bands from 31 Hz to 16 kHz against the
unfiltered noise. Output: calibration/probe_delay_filter2.json and a printed table (dB)."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H
SR = H.SR
C = [31.5 * 2 ** (k / 3) for k in range(0, 28) if 31.5 * 2 ** (k / 3) < 16000]
def bands(x):
    m = x.mean(axis=0)[int(0.5 * SR):int(2.2 * SR)]
    s = np.abs(np.fft.rfft(m * np.hanning(len(m)))) ** 2
    f = np.fft.rfftfreq(len(m), 1 / SR)
    return np.array([10 * np.log10(s[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))].sum() + 1e-20) for c in C])
serum, base = P.start()
base["Oscillator3"].pop("NoiseOsc3", None)
base["Oscillator3"]["NoiseOsc3"] = {"plainParams": {"kParamNoiseType": "White"}}
dry = np.mean([bands(serum.render(base, [48], 2.5, 2.4)) for _ in range(2)], axis=0)
out = {"centres": [round(c) for c in C], "rows": {}}
print("third-octave centres:", " ".join("%5.0f" % c for c in C))
for freq, bw in ((1000.0, 0.75), (1000.0, 2.0), (1000.0, 3.6), (1000.0, 6.75), (1000.0, 9.0), (200.0, 3.6), (40.0, 3.6), (5000.0, 2.0)):
    node = {"FXDelay": {"plainParams": {"kParamTimeL": 0.0425, "kParamTimeR": 0.0425, "kParamFeedback": 0.0, "kParamWet": 100.0, "kParamFreq": freq, "kParamBW": bw}}, "type": 4}
    t = copy.deepcopy(base); t["FXRack0"]["FX"] = [node]
    g = np.mean([bands(serum.render(t, [48], 2.5, 2.4)) for _ in range(2)], axis=0) - dry
    out["rows"][f"{freq}|{bw}"] = g.round(1).tolist()
    print("f %6.0f bw %.2f:" % (freq, bw), " ".join("%5.1f" % x for x in g), flush=True)
Path("calibration/probe_delay_filter2.json").write_text(json.dumps(out, indent=1))
os._exit(0)
