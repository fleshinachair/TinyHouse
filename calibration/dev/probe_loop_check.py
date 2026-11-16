#!/usr/bin/env python3
"""The converter's loop handling against Serum: one sample under each stored Loop Mode, rendered by Serum and by the engine from the converted set. Prints the
level in windows through a long hold and the waveform correlation (1.0 = the same samples), so a loop that wraps, reflects or does not loop shows up.
   python calibration/dev/probe_loop_check.py"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import test_render_calibration as H
import post_fit as PF
import sample_export as SE
import serum_paths
from serum2_preset_loader.codec import decode_preset_file

d2m = H.d2m
SR = H.SR
REL = "Factory/Plucked/On Far Away Planet.flac"
NOTE, DUR, HOLD = 60, 6.0, 4.0
WIN = (0.1, 0.5, 1.0, 1.8, 2.3, 2.8, 3.3, 3.8)


def lvl(m, a):
    return 10 * np.log10((m[int(a * SR):int((a + 0.2) * SR)] ** 2).mean() + 1e-20)


def main():
    pres = next((Path(serum_paths.SERUM_ROOT) / "Presets" / "Factory").rglob("INIT - Brass Multisample.SerumPreset"))
    header, base = decode_preset_file(pres)
    base = H.strip_fx(base)
    x, sr = SE._read(SE.find_sample(REL))
    n = len(x)
    serum = H.Serum()
    print("windows (s):                      " + " ".join(f"{a:6.1f}" for a in WIN) + "   corr  ", flush=True)
    for label, pp in (("no mode, points 30-40", {"kParamLoopStart": 30.0, "kParamLoopEnd": 40.0}),
                      ("kForward 30-40", {"kParamLoopMode": "kForward", "kParamLoopStart": 30.0, "kParamLoopEnd": 40.0}),
                      ("kPingPong 30-40", {"kParamLoopMode": "kPingPong", "kParamLoopStart": 30.0, "kParamLoopEnd": 40.0}),
                      ("kPingPong 55-95", {"kParamLoopMode": "kPingPong", "kParamLoopStart": 55.0, "kParamLoopEnd": 95.0}),
                      ("kTailed 30-40", {"kParamLoopMode": "kTailed", "kParamLoopStart": 30.0, "kParamLoopEnd": 40.0}),
                      ("kForward, default points", {"kParamLoopMode": "kForward"})):
        t = copy.deepcopy(base)
        t["Oscillator0"]["plainParams"] = {"kParamType": "kOsc_Sample", "kParamVolume": 0.75, **pp}
        t["Oscillator0"]["SampleOsc0"] = {"samplePathRelative": REL, "baseNote": NOTE, "sampleRate": sr, "numFrames": n, "numChannels": 1, "plainParams": "default"}
        s = serum.render(t, [NOTE], DUR, HOLD).mean(axis=0)
        m = d2m.map_to_tablor(d2m.to_vital(d2m.physical(t, header)), {}, H.SERUM_TABLES, H.WT_OUT, "Probe")
        e = H.run_cli(PF.setk(d2m.blob(m), "fx_on", 0), [NOTE], DUR, HOLD)
        e = e.mean(axis=0) if e.ndim == 2 else e
        k = int(1.0 * SR); a, b = s[:int(4.0 * SR)], e[:int(4.0 * SR)]
        c = float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-20))
        print(f"{label:24s} serum  " + " ".join(f"{lvl(s, w):+6.1f}" for w in WIN), flush=True)
        print(f"{'':24s} engine " + " ".join(f"{lvl(e, w):+6.1f}" for w in WIN) + f"   {c:+.3f}", flush=True)
    # the loop crossfade: a low-passed noise sample (embedded in the preset) so that every sample of the output tells where in the file it came from
    N = 44100
    xs = np.random.default_rng(1).normal(size=N)
    F = np.fft.rfft(xs); F[int(2500 / 44100 * N):] = 0
    xs = np.fft.irfft(F, N); xs = xs / np.abs(xs).max() * 0.8
    print("loop crossfade on a 1 s noise sample, 4 s hold: waveform correlation of engine with Serum (1.0 = identical)", flush=True)
    base_e = {}
    for label, pp in (("kForward 30-60, xf 0", {"kParamLoopMode": "kForward", "kParamLoopStart": 30.0, "kParamLoopEnd": 60.0}),
                      ("kForward 30-60, xf 50", {"kParamLoopMode": "kForward", "kParamLoopStart": 30.0, "kParamLoopEnd": 60.0, "kParamLoopCrossfade": 50.0}),
                      ("kForward 30-60, xf 100", {"kParamLoopMode": "kForward", "kParamLoopStart": 30.0, "kParamLoopEnd": 60.0, "kParamLoopCrossfade": 100.0}),
                      ("kForward 5-60, xf 100", {"kParamLoopMode": "kForward", "kParamLoopStart": 5.0, "kParamLoopEnd": 60.0, "kParamLoopCrossfade": 100.0}),
                      ("kPingPong 30-60, xf 0", {"kParamLoopMode": "kPingPong", "kParamLoopStart": 30.0, "kParamLoopEnd": 60.0}),
                      ("kPingPong 30-60, xf 50", {"kParamLoopMode": "kPingPong", "kParamLoopStart": 30.0, "kParamLoopEnd": 60.0, "kParamLoopCrossfade": 50.0}),
                      ("kPingPong 30-60, xf 100", {"kParamLoopMode": "kPingPong", "kParamLoopStart": 30.0, "kParamLoopEnd": 60.0, "kParamLoopCrossfade": 100.0})):
        t = copy.deepcopy(base)
        t["Oscillator0"]["plainParams"] = {"kParamType": "kOsc_Sample", "kParamVolume": 0.75, **pp}
        t["Oscillator0"]["SampleOsc0"] = {"embedded_audio": {"data": [xs.tolist()], "sampleRate": 44100, "table_name": "xfnoise"}, "baseNote": NOTE, "sampleRate": 44100,
                                          "numFrames": N, "numChannels": 1, "plainParams": "default"}
        s = serum.render(t, [NOTE], DUR, HOLD).mean(axis=0)
        m = d2m.map_to_tablor(d2m.to_vital(d2m.physical(t, header)), {}, H.SERUM_TABLES, H.WT_OUT, "Probe")
        e = H.run_cli(PF.setk(d2m.blob(m), "fx_on", 0), [NOTE], DUR, HOLD)
        e = e.mean(axis=0) if e.ndim == 2 else e
        xf = pp.get("kParamLoopCrossfade", 0.0) / 100.0
        ls_, le_ = pp["kParamLoopStart"] / 100.0, pp["kParamLoopEnd"] / 100.0
        L = min(xf * (le_ - ls_), ls_)
        if xf == 0.0:
            base_e[pp["kParamLoopMode"]] = e
        turns = [le_ + k * (le_ - ls_) for k in range(12) if le_ + k * (le_ - ls_) < 3.7] if pp["kParamLoopMode"] == "kPingPong" or True else []
        if L > 0:                                    # the correlation inside the crossfade windows only, against the same render with no crossfade
            idx = np.concatenate([np.arange(int((tt - L) * SR), int(tt * SR)) for tt in turns])
            cw = lambda u, v: float((u[idx] * v[idx]).sum() / (np.sqrt((u[idx] ** 2).sum() * (v[idx] ** 2).sum()) + 1e-20))
            print(f"{'':26s} in the crossfade windows: engine {cw(s, e):+.3f}   engine without crossfade {cw(s, base_e[pp['kParamLoopMode']]):+.3f}", flush=True)
        a, b = s[int(0.2 * SR):int(3.9 * SR)], e[int(0.2 * SR):int(3.9 * SR)]
        c = float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-20))
        g = float(np.sqrt((a * a).mean() / ((b * b).mean() + 1e-20)))
        print(f"{label:26s} corr {c:+.4f}   serum/engine level {20 * np.log10(g):+.2f} dB", flush=True)
    sys.stdout.flush(); os._exit(0)


if __name__ == "__main__":
    main()
