#!/usr/bin/env python3
"""The output of a Serum LFO over time, read off Serum: the LFO is routed bipolar at 100 % to an oscillator's level (base 0.5), which makes the level = the LFO's value v (the route adds
v - 0.5), and the level is read from the render's amplitude through the level -> amplitude curve measured in the same run. Prints v(t) for an LFO of any stored preset (any mode: Trig, Free,
Envelope) with the rate forced to a free Hz value so the period is long enough to read.

  python calibration/dev/probe_lfo_env.py "<preset file>" <lfo index 0..9> [rate_hz=2] [hold_s=3]
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import serum_paths
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file

SR = H.SR
STEP = 0.01


def find(name):
    p = Path(name)
    if p.is_file():
        return p
    return next(q for q in serum_paths.SERUM_ROOT.joinpath("Presets").rglob("*") if q.name.lower() == name.lower() or q.stem.lower() == name.lower())


def base_tree(t):
    t = copy.deepcopy(t)
    for r in ("FXRack0", "FXRack1", "FXRack2"):
        t[r]["FX"] = []
    for k in (1, 2):
        t[f"Oscillator{k}"]["plainParams"] = {"kParamEnable": 0.0, "kParamVolume": 0.0}
    t["Oscillator3"]["plainParams"] = {"kParamEnable": 0.0}
    t["Oscillator0"] = copy.deepcopy(H.init_tree(t, table="Analog/Basic Shapes.wav")["Oscillator0"])
    t["VoiceFilter0"]["plainParams"] = {"kParamEnable": 0.0}
    for i in range(64):
        t[f"ModSlot{i}"] = {"plainParams": "default"}
    return t


def main():
    path, idx = find(sys.argv[1]), int(sys.argv[2])
    rate = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
    hold = float(sys.argv[4]) if len(sys.argv) > 4 else 3.0
    header, src = decode_preset_file(path)
    lfo = src[f"LFO{idx}"]
    print("LFO", idx, "params", lfo.get("plainParams"), "name", lfo.get("curveDisplayName"))
    cd = lfo.get("curveData") or {}
    print("  x", cd.get("xVals"), "\n  y", cd.get("yVals"), "\n  c", cd.get("curveVals"))
    t0 = base_tree(src)
    S = H.Serum()

    def env(tree, dur=None):
        x = S.render(tree, [60], (dur or hold + 1.0), hold).mean(0)
        n = int(STEP * SR)
        return np.array([np.sqrt(np.mean(x[i:i + n] ** 2)) for i in range(0, int(hold * SR) - n, n)])

    def routed(lfo_node, vol_route=True):
        t = copy.deepcopy(t0)
        t[f"LFO{idx}"] = copy.deepcopy(lfo_node)
        pp = dict(t[f"LFO{idx}"]["plainParams"]) if isinstance(t[f"LFO{idx}"].get("plainParams"), dict) else {}
        if not os.environ.get("KEEP_RATE"):                   # KEEP_RATE=1: the LFO's stored rate and sync (a note division at the host's tempo when BeatSync is on or unwritten)
            pp.update(kParamBeatSync=0.0, kParamRate=rate)
        if os.environ.get("LFO_MODE"):                       # force the mode: Trig (the default, no key), Free or Envelope
            pp.pop("kParamMode", None)
            if os.environ["LFO_MODE"] != "Trig":
                pp["kParamMode"] = os.environ["LFO_MODE"]
        t[f"LFO{idx}"]["plainParams"] = pp
        t["Oscillator0"]["plainParams"] = dict(t["Oscillator0"]["plainParams"], kParamVolume=0.5, kParamEnable=1.0)
        t["ModSlot0"] = {"destModuleID": 0, "destModuleParamID": 1, "destModuleParamName": "kParamVolume", "destModuleTypeString": "Oscillator",
                         "plainParams": {"kParamAmount": 100.0, "kParamBipolar": 1.0}, "source": [6 + idx, 0]}
        return t
    # level -> amplitude, from constant levels
    levels = np.linspace(0.05, 1.0, 20)
    amps = []
    for v in levels:
        t = copy.deepcopy(t0)
        t["Oscillator0"]["plainParams"] = dict(t["Oscillator0"]["plainParams"], kParamVolume=float(v), kParamEnable=1.0)
        amps.append(env(t, 2.0)[20:60].mean())
    amps = np.array(amps)
    e = env(routed(lfo))
    v = np.interp(e, amps, levels, left=0.0, right=1.0)
    print(f"v(t) at rate {rate} Hz (period {1 / rate:.2f} s), every {STEP * 1000:.0f} ms (0 = silent .. 1):")
    for i in range(0, len(v), 10):
        print(f"{i * STEP:5.2f}s: " + " ".join(f"{x:4.2f}" for x in v[i:i + 10]))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
