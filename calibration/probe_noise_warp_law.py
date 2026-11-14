#!/usr/bin/env python3
"""The depth law of Serum's warps driven by the Noise oscillator (kFM_NOISE, kFMX_NOISE, kPD_NOISE, kRM_NOISE), averaged over REPS renders (the modulator is random).
A held Basic Shapes sine with the warp at each amount, the Noise on at level 0 (it modulates all the same).  Per amount: the carrier line's share of the power (dB; for a phase modulation by a
Gaussian noise that is -4.34 s^2 dB with s the rms phase deviation in radians), the total power against the unwarped note (dB; 0 for a phase modulation, which keeps the power), and the
skirt's share (power outside the carrier's +-4 bins, within +-45 % of its frequency, over the whole).
Usage: probe_noise_warp_law.py MODE [note] [amount,...]   Env: REPS (default 3).  Output: calibration/probe_noise_warp_law_<MODE>.json"""
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration")); os.chdir(ROOT)
import numpy as np
import probe_vfilter as V
import test_render_calibration as H
SR = H.SR
_, bt = H.load_preset("Keys - Loop")
t0 = H.init_tree(bt, table="Analog/Basic Shapes.wav", frames=7)
pool = V.SerumPool()
MODE = sys.argv[1]
NOTE = int(sys.argv[2]) if len(sys.argv) > 2 else 48
AMTS = [float(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "0.5,0.6,0.7,0.8,0.9,1").split(",")]
REPS = int(os.environ.get("REPS", "3"))
V.NOTE = NOTE
fc = 440 * 2 ** ((NOTE - 69) / 12)


def render(a):
    t = copy.deepcopy(t0)
    t["Oscillator0"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.75, "kParamOctave": 0.0, "kParamCoarsePit": 0.0}
    w = t["Oscillator0"].setdefault("WTOsc0", {})
    w["plainParams"] = {"kParamTablePos": 1.0, "kParamWarpMenu": MODE, "kParamWarp": a}
    t["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.0}
    return pool.render(t)


def stats(x):
    m = x[0, int(0.35 * SR):int(1.5 * SR)]
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)), 1 << 17)) ** 2
    f = np.fft.rfftfreq(1 << 17, 1 / SR); df = f[1]
    car = sp[(f > fc - 4 * df) & (f < fc + 4 * df)].sum()
    band = (f > fc * 0.55) & (f < fc * 1.45) & ~((f > fc - 4 * df) & (f < fc + 4 * df))
    return car, sp.sum(), sp[band].sum()


c0 = np.mean([stats(render(0.0)) for _ in range(2)], axis=0)
res = {}
for a in AMTS:
    rows = np.array([stats(render(a)) for _ in range(REPS)])
    cdb = 10 * np.log10(rows[:, 0] / c0[0] + 1e-30); tdb = 10 * np.log10(rows[:, 1] / c0[1] + 1e-30)
    sk = rows[:, 2] / rows[:, 1]
    res[str(a)] = {"carrier_db": [round(float(v), 2) for v in cdb], "total_db": [round(float(v), 2) for v in tdb], "skirt_share": [round(float(v), 4) for v in sk]}
    print(f"{MODE[1:]:9s} a {a:4.2f} carrier dB {np.round(cdb, 1)} (mean power {10 * np.log10(np.mean(10 ** (cdb / 10))):6.2f})  total dB {np.round(tdb, 2)}  skirt share {np.round(sk, 3)}", flush=True)
Path(f"calibration/probe_noise_warp_law_{MODE}.json").write_text(json.dumps(res, indent=1))
sys.stdout.flush(); os._exit(0)
