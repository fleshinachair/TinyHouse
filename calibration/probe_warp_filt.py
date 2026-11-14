#!/usr/bin/env python3
"""Serum's FM / PD / RM warps driven by Filter 1 (kFM_FILT1, kPD_FILT1, kRM_FILT1, kFMX_FILT1 ...): what the modulator is, and the law.
A sine carrier on oscillator A (warp MODE at each amount, routed Direct), a sine a perfect fifth up on oscillator B (level LEVEL, routed to Filter 1, which is on, wide open, LP unless FILT
says otherwise). Lines at fc + n fm are read as in probe_warp_osc.py, against the carrier with the warp at 0.  Env: LEVEL (B's level, default 0.5), FILT (Filter 1 model), CUT (Filter 1 cutoff),
NOMOD=1 leaves B out.   Usage: probe_warp_filt.py MODE [note] [amount,...]"""
import copy, os, sys
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
AMTS = [float(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "0,0.1,0.2,0.3,0.5,0.7,1").split(",")]
LEVEL = float(os.environ.get("LEVEL", "0.5")); SUB_ST = 7.0
LINES = [("c", 0, 0)] + [(f"p{n}", n, 1) for n in (1, 2, 3)] + [(f"m{n}", n, -1) for n in (1, 2, 3)] + [("s", 0, 0)]


def wtosc(t, i, pos, pitch, vol):
    t[f"Oscillator{i}"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": vol, "kParamOctave": 0.0, "kParamCoarsePit": pitch}
    w = t[f"Oscillator{i}"].setdefault(f"WTOsc{i}", {})
    src = t0["Oscillator0"]["WTOsc0"]
    for k in ("relativePathToWT", "numFrames", "numChannels", "sampleRate", "flex"):
        w[k] = copy.deepcopy(src[k])
    w["plainParams"] = {"kParamTablePos": pos}


def render(a, with_mod=True):
    t = copy.deepcopy(t0)
    wtosc(t, 0, 1.0, 0.0, 0.75)
    if a is not None:
        t["Oscillator0"]["WTOsc0"]["plainParams"] |= {"kParamWarpMenu": MODE, "kParamWarp": a}
    if with_mod:
        wtosc(t, 1, 1.0, SUB_ST, LEVEL)
        t["RoutingSlot1"] = {"plainParams": {"kParamRoutingDest": "kRoutingDestFilter"}}
    fp = {"kParamEnable": 1.0, "kParamFreq": float(os.environ.get("CUT", "1.0")), "kParamReso": 0.0}
    if os.environ.get("FILT"):
        fp["kParamType"] = os.environ["FILT"]
    t["VoiceFilter0"]["plainParams"] = fp
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
ref = lines(render(None, with_mod=False), fc)["c"]
for a in AMTS:
    ln = lines(render(a, with_mod=os.environ.get("NOMOD") != "1"), fc)
    print(f"{MODE[1:]:9s} a{a:4.2f} " + " ".join(f"{k}={'-' if v is None else '%.3f' % (v / ref)}" for k, v in ln.items()), flush=True)
sys.stdout.flush(); os._exit(0)
