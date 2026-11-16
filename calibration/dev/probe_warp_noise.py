#!/usr/bin/env python3
"""Serum's warps driven by the Noise oscillator (kFM_NOISE, kFMX_NOISE, kPD_NOISE, kRM_NOISE, kFMP_NOISE): the modulator is random, so the probe reads the carrier's skirt, not a sideband.
A sine carrier on oscillator A with the warp MODE at each amount, the Noise on at level NVOL (env NVOLS, comma list). The skirt is the power within +-45 % of the carrier but outside +-4 bins of it,
as a share of the carrier's power, minus the same number with the warp at 0 (the noise's own sound).
Usage: probe_warp_noise.py MODE [note] [amount,...]   Output: calibration/probe_warp_noise_<MODE>.json"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import probe_vfilter as V
import test_render_calibration as H
SR = H.SR
_, bt = H.load_preset("Keys - Loop")
t0 = H.init_tree(bt, table="Analog/Basic Shapes.wav", frames=7)
pool = V.SerumPool()
MODE = sys.argv[1]
NOTE = int(sys.argv[2]) if len(sys.argv) > 2 else 48
AMTS = [float(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "0,0.1,0.2,0.3,0.5").split(",")]
NVOLS = [float(x) for x in os.environ.get("NVOLS", "0,0.5").split(",")]
V.NOTE = NOTE
fc = 440 * 2 ** ((NOTE - 69) / 12)


def render(nvol, a):
    t = copy.deepcopy(t0)
    t["Oscillator0"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.75, "kParamOctave": 0.0, "kParamCoarsePit": 0.0}
    w = t["Oscillator0"].setdefault("WTOsc0", {})
    w["plainParams"] = {"kParamTablePos": 1.0, "kParamWarpMenu": MODE, "kParamWarp": a}
    t["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": nvol}
    return pool.render(t)


def skirt(x):
    m = x[0, int(0.35 * SR):int(1.5 * SR)]
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)), 1 << 17)) ** 2
    f = np.fft.rfftfreq(1 << 17, 1 / SR)
    df = f[1]
    car = sp[(f > fc - 4 * df) & (f < fc + 4 * df)].sum()
    band = (f > fc * 0.55) & (f < fc * 1.45) & ~((f > fc - 4 * df) & (f < fc + 4 * df))
    return float(sp[band].sum() / car), float(10 * np.log10(car + 1e-30))


res = {}
for nv in NVOLS:
    base = skirt(render(nv, 0.0))
    for a in AMTS:
        s, cdb = skirt(render(nv, a))
        res[f"{MODE}|nvol{nv}|{a}"] = {"skirt": round(s, 5), "excess": round(s - base[0], 5), "carrier_db": round(cdb - base[1], 2)}
        print(f"{MODE[1:]:9s} nvol {nv:4.2f} a {a:4.2f} skirt {s:9.5f} excess {s - base[0]:9.5f} carrier {cdb - base[1]:+6.2f} dB", flush=True)
Path(f"calibration/probe_warp_noise_{MODE}.json").write_text(json.dumps(res, indent=1))
sys.stdout.flush(); os._exit(0)
