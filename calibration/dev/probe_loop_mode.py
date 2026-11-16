#!/usr/bin/env python3
"""What Serum's Sample oscillator does with its loop settings: the same file and loop points under each stored Loop Mode (and none stored), held long enough to
outlast the sample. Prints the level in windows through the hold and after the release, and writes the raw renders' summary to calibration/probe_loop_mode.json.
   python calibration/dev/probe_loop_mode.py"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np, soundfile as sf
import test_render_calibration as H
import sample_export as SE
import serum_paths
from serum2_preset_loader.codec import decode_preset_file

SR = H.SR
REL = "Factory/Plucked/On Far Away Planet.flac"
NOTE, DUR, HOLD = 60, 6.0, 4.0


def env(x):
    m = x.mean(axis=0) if x.ndim == 2 else x
    out = []
    for a in (0.1, 0.5, 1.0, 1.5, 2.0, 3.0, 3.8, 4.3, 5.0):
        seg = m[int(a * SR):int((a + 0.2) * SR)]
        out.append(10 * np.log10((seg ** 2).mean() + 1e-20))
    return out


def main():
    pres = next((Path(serum_paths.SERUM_ROOT) / "Presets" / "Factory").rglob("INIT - Brass Multisample.SerumPreset"))
    header, base = decode_preset_file(pres)
    base = H.strip_fx(base)
    path = SE.find_sample(REL)
    info = sf.info(str(path)) if str(path).endswith(("wav", "flac")) else None
    x, sr = SE._read(path)
    n = len(x)
    print(f"{REL}: {n} frames at {sr} = {n / sr:.2f} s", flush=True)
    serum = H.Serum()
    out = {}
    print("window starts (s): 0.1 0.5 1.0 1.5 2.0 3.0 3.8(hold ends 4.0) 4.3 5.0", flush=True)
    for label, mode, loop in (("no loop params", None, None), ("points, no mode", None, (30.0, 40.0)), ("points, kForward", "kForward", (30.0, 40.0)),
                              ("points, kPingPong", "kPingPong", (30.0, 40.0)), ("points, kTailed", "kTailed", (30.0, 40.0)),
                              ("points, kOff", "kOff", (30.0, 40.0)), ("mode only, kForward", "kForward", None)):
        t = copy.deepcopy(base)
        pp = {"kParamType": "kOsc_Sample", "kParamVolume": 0.75}
        if mode:
            pp["kParamLoopMode"] = mode
        if loop:
            pp["kParamLoopStart"], pp["kParamLoopEnd"] = loop
        t["Oscillator0"]["plainParams"] = pp
        t["Oscillator0"]["SampleOsc0"] = {"samplePathRelative": REL, "baseNote": NOTE, "sampleRate": sr, "numFrames": n, "numChannels": 1 if x.ndim == 1 or x.shape[1] == 1 else 2, "plainParams": "default"}
        a = serum.render(t, [NOTE], DUR, HOLD)
        e = env(a)
        out[label] = e
        print(f"{label:22s} " + " ".join(f"{v:+6.1f}" for v in e), flush=True)
    Path("calibration/probe_loop_mode.json").write_text(json.dumps(out, indent=1))
    sys.stdout.flush(); os._exit(0)


if __name__ == "__main__":
    main()
