#!/usr/bin/env python3
"""Are the factory's plain Split nodes (no stored params: ModuleCount1/2 unwritten = 0, an empty frequency split) and its parameter-less / zero-mix Bode nodes transparent?
A saw through the node (copied from a factory preset) against the same saw with no node; octave-band and level difference in dB, and the same render against itself (the floor).
Usage: probe_split_plain.py"""
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fxmap
import fx_probe as P
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file
SR = H.SR
import serum_paths
FACT = serum_paths.SERUM_ROOT / "Presets" / "Factory"
EDGES = [60, 120, 250, 500, 1000, 2000, 4000, 8000, 16000]
def bands(x):
    m = x.mean(axis=0)[int(0.4 * SR):int(1.8 * SR)]
    s = np.abs(np.fft.rfft(m * np.hanning(len(m)))) ** 2; f = np.fft.rfftfreq(len(m), 1 / SR)
    return np.array([10 * np.log10(s[(f >= a) & (f < b)].sum() + 1e-20) for a, b in zip(EDGES[:-1], EDGES[1:])]), 10 * np.log10(np.sum(m ** 2) + 1e-20)
def rack_of(preset):
    f = next(FACT.rglob(preset + ".SerumPreset"))
    h, t = decode_preset_file(f)
    return copy.deepcopy(t["FXRack0"]["FX"]), [fxmap.kind_of(n) + ("" if fxmap.enabled(n) else "(off)") + ("" if fxmap.plain(n).get("kParamWet", 100.0) >= 1.0 or fxmap.kind_of(n) not in fxmap.DRY_KINDS else "(wet 0)") for n in t["FXRack0"]["FX"]]
serum, base = P.start()
base = copy.deepcopy(base); base["Oscillator0"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.6}
nb, nl = bands(serum.render(base, [48], 2.0, 1.9))
print("band edges", EDGES, "  last column: level dB")
nodes, kinds = rack_of("BA - Fonk")
split, dist0 = nodes[0], nodes[1]          # [Split(Freq, ModuleCount2 = 1), Distortion at Wet 0]
print("Fonk's split:", split, "| module:", dist0)
def with_counts(c1, c2, mods):
    s = copy.deepcopy(split); pp = s["FXSplit"]["plainParams"]; pp.pop("kParamModuleCount1", None); pp.pop("kParamModuleCount2", None)
    if c1: pp["kParamModuleCount1"] = float(c1)
    if c2: pp["kParamModuleCount2"] = float(c2)
    return [s] + [copy.deepcopy(dist0) for _ in range(mods)]
for lab, rack in [("Split + dry Distortion in branch 2", with_counts(0, 1, 1)), ("Split + dry Distortion in branch 1", with_counts(1, 0, 1)), ("Split, no modules, counts 0", with_counts(0, 0, 0)), ("dry Distortion alone", [copy.deepcopy(dist0)])]:
    t = copy.deepcopy(base); t["FXRack0"]["FX"] = rack
    b, l = bands(serum.render(t, [48], 2.0, 1.9))
    print("%-36s %s  %+6.1f" % (lab, " ".join("%+6.1f" % v for v in b - nb), l - nl), flush=True)
b, l = bands(serum.render(base, [48], 2.0, 1.9))
print("floor (same patch again)             ", " ".join("%+6.1f" % v for v in b - nb), " %+6.1f" % (l - nl))
sys.stdout.flush(); os._exit(0)
