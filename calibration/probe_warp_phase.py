#!/usr/bin/env python3
"""Serum's Bend / ASYM warps as phase maps: a held Default Shapes saw (random phase off) goes through the warp and the output value is read back as the phase it came from, g(p).
Result (2026-11-13): BendPosNeg and ASYMPosNeg are ONE bipolar knob - at 0.25 they equal BendPos / ASYMPos at 0.5, at 0.75 BendNeg / ASYMNeg at 0.5, at 0.5 the table as it is (the
converter's BIPOLAR_WARPS). The ASYM maps are not the Bend maps (they cross the cycle edge: g runs past 1), but their harmonics stay within 0.3 dB on a saw; on a sine they add a 2nd
harmonic at -25 dB for ASYMNeg 0.1. The converter carries them with the engine's Bend (an approximation, noted)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import warpwave as W, numpy as np, os
SAW=("S2 Tables/Default Shapes.wav",9,0.0); NB=W.NB
xs=W.cycle(W.render(*SAW[:2],SAW[2])); ph=np.arange(NB)/NB
core=slice(20,NB-20); o=np.argsort(xs[core])
def g(mode,a):
    ys=W.cycle(W.render(SAW[0],SAW[1],SAW[2],mode,a)); return np.interp(ys,xs[core][o],ph[core][o])
pts=[int(NB*q) for q in (0.1,0.25,0.4,0.5,0.6,0.75,0.9)]
print("phi          0.10  0.25  0.40  0.50  0.60  0.75  0.90")
for m in ("kASYMPos","kASYMNeg","kASYMPosNeg","kBendPos","kBendNeg","kBendPosNeg"):
    for a in (0.25,0.5,0.75,1.0):
        gg=g(m,a); print("%-12s %.2f"%(m[1:],a)," ".join("%5.2f"%gg[i] for i in pts),flush=True)
sys.stdout.flush(); os._exit(0)
