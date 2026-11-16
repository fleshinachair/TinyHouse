#!/usr/bin/env python3
"""How Serum 2's FX buses work: a saw (osc A) through a low voice filter, the bus-1 rack holding a single EQ (neutral) or a Reverb, and the routing slot's destination (Filter / None) and
FXBus1Level (0 / 50 / 100): the octave-band spectrum and level of the output. Tells whether a bus send is taken before or after the filter, whether the source also plays on the main
path, and what the level law of the send is. Output: printed."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import test_render_calibration as H
import voice_survey as VS
SR = H.SR
_, bt = H.load_preset("Keys - Loop")
serum = H.Serum()
def tree(dest, level, rack1=None, rack0=None, filt=True, bal=None, level2=0.0):
    t = H.init_tree(bt, table="S2 Tables/Default Shapes.wav", frames=9)
    t["Oscillator0"]["plainParams"] = {"kParamVolume": 0.5}
    if filt: t["VoiceFilter0"]["plainParams"] = {"kParamEnable": 1.0, "kParamFreq": 0.30, "kParamType": "MgL24"}
    pp = {"kParamRoutingDest": dest}
    if level: pp["kParamFXBus1Level"] = level
    if level2: pp["kParamFXBus2Level"] = level2
    t["RoutingSlot0"] = {"plainParams": pp}
    if rack1 is not None: t["FXRack1"]["FX"] = rack1
    if rack0 is not None: t["FXRack0"]["FX"] = rack0
    return t
EQ = {"FXEQ": {"plainParams": {}}, "type": 7}
def run(label, t):
    global serum
    try:
        xs = [serum.render(t, [48], 1.5, 1.2) for _ in range(2)]
    except RuntimeError:
        print("%-44s silent" % label, flush=True); serum = H.Serum(); return
    b = np.mean([VS.bands(x) for x in xs], axis=0); lv = 10 * np.log10(np.mean([(x.mean(axis=0)[int(0.2 * SR):int(1.2 * SR)] ** 2).mean() for x in xs]) + 1e-20)
    print("%-44s level %6.1f dB | bands %s" % (label, lv, " ".join("%5.0f" % (x - b.max()) for x in b)), flush=True)
print("bands:", VS.EDGES[:-1])
run("filter, no bus", tree("kRoutingDestFilter", 0))
run("filter + bus1 100, bus rack EQ", tree("kRoutingDestFilter", 100, [copy.deepcopy(EQ)]))
run("filter + bus1 50, bus rack EQ", tree("kRoutingDestFilter", 50, [copy.deepcopy(EQ)]))
run("none + bus1 100, bus rack EQ", tree("kRoutingDestNone", 100, [copy.deepcopy(EQ)]))
run("none + bus1 100, EMPTY bus rack", tree("kRoutingDestNone", 100, []))
run("none + bus1 50, bus rack EQ", tree("kRoutingDestNone", 50, [copy.deepcopy(EQ)]))
run("none, bus1 0 (silent expected)", tree("kRoutingDestNone", 0, [copy.deepcopy(EQ)]))
run("direct (no filter) no bus", tree("kRoutingDestDirect", 0))
run("direct + bus1 100", tree("kRoutingDestDirect", 100, [copy.deepcopy(EQ)]))
run("none + bus1 100, filter OFF", tree("kRoutingDestNone", 100, [copy.deepcopy(EQ)], filt=False))

for lv in (100, 90, 75, 60, 40, 25, 10):
    run("none + bus1 %d, EQ" % lv, tree("kRoutingDestNone", lv, [copy.deepcopy(EQ)]))
run("filter + bus1 100 + bus2 100 (no bus2 rack)", tree("kRoutingDestFilter", 100, [copy.deepcopy(EQ)], level2=100))
os._exit(0)
