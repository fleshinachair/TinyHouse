#!/usr/bin/env python3
"""Serum's FM / PD / RM / AM warps whose modulator is another oscillator (kFM_OSC, kFM_OSC2, kPD_OSC2, kRM_OSC, kAM_OSC, kFM_NOISE, ...): which oscillator drives which, and the law.
A sine carrier on oscillator CAR with the warp MODE at each amount; a sine modulator a perfect fifth up on oscillator MOD (or the Noise / Sub), at its own level 0 (only the warp is
heard). Lines at fc + n fm are read as in probe_warp_fm.py. Env: CAR (0..2), MODS (comma list of oscillator indexes to try as the modulator: 0,1,2 = A,B,C, 3 = noise, 4 = Sub), OUT.
Usage: probe_warp_osc.py MODE [note] [amount,...]   Output: calibration/probe_warp_osc.json"""
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
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
AMTS = [float(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "0,0.1,0.2,0.3").split(",")]
CAR = int(os.environ.get("CAR", "0"))
MODS = [int(x) for x in os.environ.get("MODS", "0,1,2,3,4").split(",") if int(x) != CAR]
SUB_ST = float(os.environ.get("SUB_ST", "7"))
LINES = [("c", 0, 0)] + [(f"p{n}", n, 1) for n in (1, 2, 3)] + [(f"m{n}", n, -1) for n in (1, 2, 3)] + [("s", 0, 0)]


def wtosc(t, i, pos, pitch, vol):
    t[f"Oscillator{i}"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": vol, "kParamOctave": 0.0, "kParamCoarsePit": pitch}
    w = t[f"Oscillator{i}"].setdefault(f"WTOsc{i}", {})
    src = t0["Oscillator0"]["WTOsc0"]
    for k in ("relativePathToWT", "numFrames", "numChannels", "sampleRate", "flex"):
        w[k] = copy.deepcopy(src[k])
    w["plainParams"] = {"kParamTablePos": pos}


def render(mod, a, with_mod=True):
    t = copy.deepcopy(t0)
    t["Oscillator0"]["plainParams"]["kParamEnable"] = 1.0 if CAR == 0 else 0.0
    if CAR != 0:
        t["Oscillator0"]["plainParams"] = {"kParamEnable": 0.0}
    wtosc(t, CAR, 1.0, 0.0, 0.75)
    t[f"Oscillator{CAR}"][f"WTOsc{CAR}"]["plainParams"] |= ({"kParamWarpMenu": MODE, "kParamWarp": a} if a is not None else {})
    if with_mod:
        if mod in (0, 1, 2):
            wtosc(t, mod, 1.0, SUB_ST, 0.0)
            if os.environ.get("MODEN") == "0":
                t[f"Oscillator{mod}"]["plainParams"]["kParamEnable"] = 0.0
        elif mod == 3:
            t["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.0}
        elif mod == 4:
            t["Oscillator4"] = {"plainParams": {"kParamEnable": 1.0, "kParamVolume": 0.0, "kParamOctave": 0.0, "kParamCoarsePit": SUB_ST}, "SubOsc4": {"plainParams": {"kParamShape": "kSine"}}}
    return pool.render(t)


def lines(x, fc):
    m = x[0, int(0.35 * SR):int(1.2 * SR)]
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)), 1 << 17)) / len(m) * 4
    f = np.fft.rfftfreq(1 << 17, 1 / SR)
    fm = fc * 2 ** (SUB_ST / 12)
    out = {}
    for name, n, sgn in LINES:
        fr = fm if name == "s" else abs(fc + sgn * n * fm)
        if fr > 18000 or fr < 20:
            out[name] = None; continue
        i = np.searchsorted(f, fr)
        out[name] = float(sp[max(i - 3, 0):i + 4].max())
    return out


V.NOTE = NOTE
fc = 440 * 2 ** ((NOTE - 69) / 12)
ref = lines(render(None, None, with_mod=False), fc)["c"]
res = {}
for mod in MODS:
    for a in AMTS:
        ln = lines(render(mod, a), fc)
        res[f"{MODE}|car{CAR}|mod{mod}|{a}"] = {k: (None if v is None else round(v / ref, 4)) for k, v in ln.items()}
        print(f"{MODE[1:]:9s} car{CAR} mod{mod} a{a:4.2f} " + " ".join(f"{k}={'-' if v is None else '%.3f' % (v / ref)}" for k, v in ln.items()), flush=True)
Path(os.environ.get("OUT", f"calibration/probe_warp_osc_{MODE}_c{CAR}.json")).write_text(json.dumps(res, indent=1))
sys.stdout.flush(); os._exit(0)
