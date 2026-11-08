#!/usr/bin/env python3
"""What a macro route does to an effect's own knob, measured on Serum: noise through one effect, the stored value against the same effect with
a route from Macro1 (at 100 %) of a given amount, and the equivalent static value read off the result.

  probe_fx_macro.py eq        EQ band 2 (bell) gain and frequency, band 2 as a low-pass (the Camp Fire 1 case)
  probe_fx_macro.py dist      Distortion drive (harmonics of a sine are not needed: the level of noise through the stage is read at the stored drive curve)

Writes calibration/probe_fx_macro_<what>.json. Serum renders are plain (one plugin, noise source), so no render limit problem for these few cases.
"""
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H

FREQS = np.geomspace(40.0, 18000.0, 96)


def with_route(tree, kind, param, amount, macro=0, value=100.0, dest_id=0):
    t = copy.deepcopy(tree)
    t[f"Macro{macro}"]["plainParams"] = {"kParamValue": value}
    t["ModSlot0"] = {"destModuleID": dest_id, "destModuleParamID": 1, "destModuleParamName": param, "destModuleTypeString": f"FX{kind}",
                     "plainParams": {"kParamAmount": amount}, "source": [25 + macro, 0]}
    return t


def spectrum_db(dry, wet):
    f, p0 = P.psd(dry)
    _, p1 = P.psd(wet)
    out = []
    for c in FREQS:
        k = (f > c / 1.06) & (f < c * 1.06)
        out.append(10 * np.log10(p1[k].sum() / p0[k].sum()))
    return np.array(out)


def peak(curve):
    i = int(np.argmax(np.abs(curve)))
    return float(FREQS[i]), float(curve[i])


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "eq"
    serum, base = P.start()
    dry = P.render(serum, base)
    out = {}
    if what == "eq":
        for gain0 in (-12.0, 0.0, 6.0):
            for amt in (25.0, 50.0, 100.0, -50.0, -100.0):
                p = {"kParamType2": 1.0, "kParamFreq2": 800.0, "kParamGain2": gain0, "kParamReso2": 50.0}
                t = with_route(P.fx_tree(base, "EQ", p, 7), "EQ", "kParamGain2", amt)
                c = spectrum_db(dry, P.render(serum, t))
                fc, g = peak(c)
                out[f"gain|{gain0}|{amt}"] = {"peak_hz": fc, "peak_db": g}
                print(f"gain2 {gain0:+5.1f} dB, route {amt:+5.0f} % x macro 100 %: peak {g:+6.1f} dB at {fc:6.0f} Hz", flush=True)
        for f0 in (200.0, 800.0, 3000.0):
            for amt in (10.0, 25.0, 50.0, -25.0):
                p = {"kParamType2": 1.0, "kParamFreq2": f0, "kParamGain2": 12.0, "kParamReso2": 50.0}
                t = with_route(P.fx_tree(base, "EQ", p, 7), "EQ", "kParamFreq2", amt)
                c = spectrum_db(dry, P.render(serum, t))
                fc, g = peak(c)
                out[f"freq|{f0}|{amt}"] = {"peak_hz": fc, "peak_db": g}
                print(f"freq2 {f0:6.0f} Hz, route {amt:+5.0f} %: peak at {fc:7.0f} Hz ({np.log2(fc / f0):+5.2f} octaves), {g:+5.1f} dB", flush=True)
        for f0 in (21.5332, 200.0, 2000.0):
            for amt in (0.0, 25.0, 50.0, 100.0):                # band 2 as a low-pass: where is the -3 dB point?
                p = {"kParamType2": 2.0, "kParamFreq2": f0, "kParamReso2": 50.0}
                t = with_route(P.fx_tree(base, "EQ", p, 7), "EQ", "kParamFreq2", amt) if amt else P.fx_tree(base, "EQ", p, 7)
                c = spectrum_db(dry, P.render(serum, t))
                ref = np.median(c[:8])
                k = int(np.argmax(c < ref - 3.0)) if (c < ref - 3.0).any() else len(c) - 1
                out[f"lp|{f0}|{amt}"] = {"minus3_hz": float(FREQS[k]), "passband_db": float(ref)}
                print(f"LP2 {f0:7.1f} Hz, route {amt:+5.0f} %: -3 dB at {FREQS[k]:7.0f} Hz (passband {ref:+5.1f} dB)", flush=True)
    if what == "dist":
        _, src = H.load_preset("Keys - Loop")
        tone = H.init_tree(src, table="Analog/Basic Shapes.wav", frames=7)       # a sine (calibration/probe_dist.py)
        tone["Oscillator0"]["plainParams"] = {"kParamVolume": 0.5}
        F0 = 130.81
        drives = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]

        def feature(x):                                               # the 3rd harmonic against the fundamental, dB: rises with the drive
            m = x[0, int(0.5 * H.SR):int(2.5 * H.SR)]
            n = 1 << 16
            sp = np.abs(np.fft.rfft(m[:n] * np.hanning(n)))
            amp = lambda k: sp[int(round(F0 * k / (H.SR / n))) - 3:int(round(F0 * k / (H.SR / n))) + 4].max()
            return float(20 * np.log10(amp(3) / amp(1) + 1e-9))

        def render(t):
            return serum.render(t, [48], 3.0, 2.9, vel=100)
        static = {d: feature(render(P.fx_tree(tone, "Distortion", {"kParamDrive": d, "kParamWet": 100.0}, 0))) for d in drives}
        print("static drive -> 3rd harmonic dB:", {k: round(v, 1) for k, v in static.items()}, flush=True)
        out["static"] = static
        eq = lambda v: float(np.interp(v, [static[d] for d in drives], drives))     # monotonic over the grid
        for d0 in (10.0, 25.0, 50.0):
            for amt in (20.0, 40.0, -10.0):
                t = with_route(P.fx_tree(tone, "Distortion", {"kParamDrive": d0, "kParamWet": 100.0}, 0), "Distortion", "kParamDrive", amt)
                v = feature(render(t))
                out[f"drive|{d0}|{amt}"] = {"feature": v, "equivalent_drive": eq(v)}
                print(f"drive {d0:4.0f} + route {amt:+4.0f} % x 100 %: 3rd {v:6.1f} dB = static drive {eq(v):5.1f} (expect {min(max(d0 + amt, 0), 100):5.1f})", flush=True)
    Path(ROOT / f"calibration/probe_fx_macro_{what}.json").write_text(json.dumps(out, indent=1))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
