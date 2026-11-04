#!/usr/bin/env python3
"""Does the bus send law (probe_bus.py: gain = level^3, same unit as the Direct route) hold for the other sources: osc B, noise, sub? Each alone, once Direct with no send, then route None with a
bus-1 send of 100 / 50 % into an empty rack: the level should match (100) and be 20 log10(0.125) = -18 dB under (50). Output: printed."""
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import test_render_calibration as H
import voice_survey as VS
SR = H.SR
_, bt = H.load_preset("Keys - Loop")
serum = H.Serum()
def tree(src, dest, level):
    t = H.init_tree(bt)
    t["Oscillator0"]["plainParams"] = {"kParamEnable": 0.0}
    if src == 1:
        o = copy.deepcopy(H.init_tree(bt)["Oscillator0"]); o["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.5}
        o["WTOsc1"] = o.pop("WTOsc0"); t["Oscillator1"] = o
    elif src == 3:
        t["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.5}
    elif src == 4:
        t["Oscillator4"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.5}
    pp = {"kParamRoutingDest": dest}
    if level: pp["kParamFXBus1Level"] = level
    t[f"RoutingSlot{src}"] = {"plainParams": pp}
    return t
def lvl(t):
    global serum
    try:
        xs = [serum.render(t, [48], 1.5, 1.2) for _ in range(2)]
    except RuntimeError:
        serum = H.Serum(); return float("nan")
    return 10 * np.log10(np.mean([(x.mean(axis=0)[int(0.2 * SR):int(1.2 * SR)] ** 2).mean() for x in xs]) + 1e-20)
for src, nm in ((0, "osc A"), (1, "osc B"), (3, "noise"), (4, "sub")):
    if src == 0:
        t0 = H.init_tree(bt); t0["Oscillator0"]["plainParams"] = {"kParamVolume": 0.5}
        mk = lambda dest, level: (lambda t: (t["__setitem__"] if False else t))(H.init_tree(bt))
    d = lvl(tree(src, "kRoutingDestDirect", 0)) if src else None
    if src == 0:
        def t_of(dest, level):
            t = H.init_tree(bt); t["Oscillator0"]["plainParams"] = {"kParamVolume": 0.5}
            pp = {"kParamRoutingDest": dest}
            if level: pp["kParamFXBus1Level"] = level
            t["RoutingSlot0"] = {"plainParams": pp}; return t
        d = lvl(t_of("kRoutingDestDirect", 0)); n100 = lvl(t_of("kRoutingDestNone", 100)); n50 = lvl(t_of("kRoutingDestNone", 50))
    else:
        n100 = lvl(tree(src, "kRoutingDestNone", 100)); n50 = lvl(tree(src, "kRoutingDestNone", 50))
    print("%-6s direct %6.1f dB | none+bus100 %+5.1f re direct | none+bus50 %+5.1f re direct (cube law: -18.1)" % (nm, d, n100 - d, n50 - d), flush=True)
os._exit(0)
