#!/usr/bin/env python3
"""Serum's noise oscillator: Type (unwritten / White / Pink / Brown) x Color (0 .. 1 in tenths; 0.5 is neutral), third-octave band levels from 50 Hz to 16 kHz and the
overall level in dB, all at oscillator volume 0.75 with the voice filter off. (The probe base carries Bass - Gravity's Pink / Color 0.75 noise, so its NoiseOsc3 is
removed first: that was the 'unwritten' noise that was measured before.) Output: calibration/probe_noise_color.json, a printed octave summary."""
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
CENTRES = [50 * 2 ** (k / 3) for k in range(0, 25) if 50 * 2 ** (k / 3) <= 17000]
def bands(x):
    m = x.mean(axis=0)[int(0.5 * SR):int(2.0 * SR)]
    s = np.abs(np.fft.rfft(m * np.hanning(len(m)))) ** 2
    f = np.fft.rfftfreq(len(m), 1 / SR)
    return np.array([10 * np.log10(s[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))].sum() + 1e-20) for c in CENTRES]), 10 * np.log10(s.sum() + 1e-20)
def start():
    serum, base = P.start()
    base["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.75}
    base["VoiceFilter0"]["plainParams"] = {"kParamEnable": 0.0}
    return serum, base
if __name__ == "__main__":
    serum, base = start()
    out = {"centres": [round(c) for c in CENTRES]}
    for typ in (None, "White", "Pink", "Brown"):
        for color in [None] + [round(0.1 * i, 1) for i in range(0, 11)]:
            b = copy.deepcopy(base)
            pp = {}
            if typ: pp["kParamNoiseType"] = typ
            if color is not None: pp["kParamColor"] = color
            b["Oscillator3"].pop("NoiseOsc3", None)
            if pp: b["Oscillator3"]["NoiseOsc3"] = {"plainParams": pp}
            try:
                xs = [serum.render(b, [48], 2.5, 2.4) for _ in range(2)]
            except Exception as e:
                print(typ, color, "failed", str(e)[:40], flush=True); serum = start()[0]; continue
            bs = np.mean([bands(x)[0] for x in xs], axis=0); lv = float(np.mean([bands(x)[1] for x in xs]))
            key = f"{typ or 'unwritten'}|{'unwritten' if color is None else color}"
            out[key] = {"bands": bs.round(1).tolist(), "level_db": round(lv, 1)}
            oct_ = [bs[i] for i in range(0, len(bs), 3)]
            print("%-10s color %-9s level %5.1f | octaves(50..): %s" % (typ or "unwritten", "unwritten" if color is None else color, lv, " ".join("%5.1f" % (x - oct_[4]) for x in oct_)), flush=True)
    Path("calibration/probe_noise_color.json").write_text(json.dumps(out, indent=1))
    os._exit(0)
