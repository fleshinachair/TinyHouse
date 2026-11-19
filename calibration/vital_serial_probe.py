#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""How Vital's two voice filters combine when filter 2 (or 1) takes the other's output (filter_N_filter_input), measured through the real plugin.

    python calibration/vital_serial_probe.py

Saw on osc 1, filter 1 a low-pass and filter 2 a high-pass (so the series result is a band, and the parallel one is two separate lobes). Reports the energy in
three bands for each routing, so it shows whether the second filter sees only the first one's output, or also the oscillators sent to it, and whether the first
filter's own output still reaches the mix.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "calibration"))
import vital_render as VR                                           # noqa: E402
import vital_route_probe as RP                                      # noqa: E402

SR = 44100
BANDS = ((60, 300), (300, 1500), (1500, 8000))


def bands(x):
    seg = x[:, int(0.3 * SR):int(1.5 * SR)].mean(axis=0)
    m = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    f = np.fft.rfftfreq(len(seg), 1 / SR)
    return [10 * np.log10(max(m[(f >= a) & (f < b)].sum(), 1e-20)) for a, b in BANDS]


def make(dest, f1, f2, in1=0.0, in2=0.0):
    d = RP.base_preset()
    s = d["settings"]
    s["osc_1_destination"] = float(dest)
    for fi, cfg, inp in ((1, f1, in1), (2, f2, in2)):
        s[f"filter_{fi}_on"] = 1.0 if cfg else 0.0
        if cfg:
            kind, note = cfg
            s[f"filter_{fi}_model"], s[f"filter_{fi}_style"], s[f"filter_{fi}_blend"] = 0.0, 0.0, 0.0
            s[f"filter_{fi}_style"] = {"lp": 0.0, "hp": 2.0}[kind] if False else 0.0
            s[f"filter_{fi}_blend"] = {"lp": 0.0, "bp": 1.0, "hp": 2.0}[kind]
            s[f"filter_{fi}_cutoff"] = float(note)
            s[f"filter_{fi}_resonance"] = 0.2
            s[f"filter_{fi}_mix"] = 1.0
            s[f"filter_{fi}_filter_input"] = float(inp)
    return d


def main():
    host = VR.VitalHost()
    LP, HP = ("lp", 56), ("hp", 88)
    cases = [
        ("f1 only (osc->f1)", make(0, LP, None)),
        ("f2 only (osc->f2)", make(1, None, HP)),
        ("parallel dual (osc->both)", make(2, LP, HP)),
        ("osc->f1, f2 takes f1", make(0, LP, HP, 0, 1)),
        ("osc->f2, f2 takes f1 (f1 gets nothing)", make(1, LP, HP, 0, 1)),
        ("osc->both, f2 takes f1", make(2, LP, HP, 0, 1)),
        ("osc->f2, f1 takes f2", make(1, LP, HP, 1, 0)),
        ("osc->f1, f1 takes f2 (f2 gets nothing)", make(0, LP, HP, 1, 0)),
        ("osc->f1 only, f2 on but f1 takes f2", make(0, LP, HP, 1, 0)),
    ]
    print(f"{'case':44s} " + "  ".join(f"{a}-{b}" .rjust(9) for a, b in BANDS))
    for name, d in cases:
        x = RP.render(host, d)
        print(f"{name:44s} " + "  ".join(f"{v:9.1f}" for v in bands(x)))


if __name__ == "__main__":
    main()
