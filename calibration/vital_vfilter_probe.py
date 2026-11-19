#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Vital's voice filter (filter 1) as a transfer curve, plugin against engine: a saw at note 36 held through the filter, the level of its harmonics against the unfiltered saw.

    python calibration/vital_vfilter_probe.py [--model 0] [--style 0 1] [--blend 0] [--cutoff 80] [--res 0 .25 .5 .75] [--engine]

Prints the curve (dB) at fifteen harmonics for each resonance; --engine adds the converter's engine render beside it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "calibration"))
import vital_fxkey_probe as K                                       # noqa: E402
import vital_render as VR                                           # noqa: E402

NOTE = 36
F0 = 440.0 * 2 ** ((NOTE - 69) / 12)
K_IDX = np.array([1, 2, 3, 4, 6, 8, 10, 13, 16, 20, 25, 32, 40, 50, 60])


def saw_preset() -> dict:
    """A clean base: no effects, no modulation, one oscillator playing a band-limited saw at its own pitch."""
    import base64
    import vital_lfo_probe as LP
    d = LP.base_preset()
    d["synth_version"] = "1.5.5"
    s = d["settings"]
    x = np.arange(2048) / 2048.0
    saw = sum(np.sin(2 * np.pi * h * x) / h for h in range(1, 400)) * 0.5
    b64 = base64.b64encode(saw.astype("<f4").tobytes()).decode()
    s["wavetables"][0]["groups"][0]["components"][0]["keyframes"][:] = [{"position": 0, "wave_data": b64}, {"position": 255, "wave_data": b64}]
    s["osc_1_wave_frame"], s["osc_1_midi_track"], s["osc_1_transpose"], s["osc_1_tune"], s["osc_1_level"] = 0.0, 1.0, 0.0, 0.0, 1.0
    s["osc_1_random_phase"], s["osc_1_distortion_type"], s["osc_1_spectral_morph_type"] = 0.0, 0.0, 0.0
    s["env_1_attack"], s["env_1_sustain"], s["env_1_release"] = 0.0, 1.0, 0.1
    return d


def preset(model: int, style: int, blend: float, cutoff: float, res: float, drive: float = 0.0) -> dict:
    d = saw_preset()
    s = d["settings"]
    s["filter_fx_on"] = 0.0
    s["filter_1_on"], s["filter_1_model"], s["filter_1_style"], s["filter_1_blend"] = 1.0, float(model), float(style), float(blend)
    s["filter_1_cutoff"], s["filter_1_resonance"], s["filter_1_drive"], s["filter_1_mix"], s["filter_1_keytrack"] = cutoff, res, drive, 1.0, 0.0
    s["osc_1_destination"] = 0.0                                    # oscillator 1 goes through filter 1
    s["osc_2_on"] = s["osc_3_on"] = s["sample_on"] = 0.0
    return d


def curves(render, d: dict) -> np.ndarray:
    return K.harmonics(render(d), F0, 60)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=int, default=0)
    ap.add_argument("--style", type=int, nargs="+", default=[0, 1])
    ap.add_argument("--blend", type=float, default=0.0)
    ap.add_argument("--cutoff", type=float, default=80.0)
    ap.add_argument("--res", type=float, nargs="+", default=[0.0, 0.25, 0.5, 0.75])
    ap.add_argument("--drive", type=float, default=0.0, help="filter drive in dB (Vital: 0..30)")
    ap.add_argument("--engine", action="store_true")
    a = ap.parse_args()
    host = VR.VitalHost()
    vr = lambda d: np.asarray(host.render(json.dumps(d).encode(), ((NOTE, 100, 0.0, 1.0),), 1.4))[0]
    ref = curves(vr, preset(0, 0, 0.0, 136.0, 0.0))             # an open Analog low-pass: the unfiltered saw
    if a.engine:
        import vital_porta_probe as PP
        er = lambda d: PP.engine_render(d, [(0.0, 1.0, NOTE)], 1.4)
        eref = curves(er, preset(0, 0, 0.0, 136.0, 0.0))
    print("harmonic Hz:", [int(F0 * x) for x in K_IDX], "cutoff note", a.cutoff, f"= {440.0 * 2 ** ((a.cutoff - 69) / 12):.0f} Hz")
    for st in a.style:
        for r in a.res:
            d = preset(a.model, st, a.blend, a.cutoff, r, a.drive)
            print(f"style {st} res {r:4.2f} vital ", np.round(20 * np.log10(curves(vr, d) / ref)[K_IDX - 1]).astype(int).tolist(), flush=True)
            if a.engine:
                print(f"               engine", np.round(20 * np.log10(curves(er, d) / eref)[K_IDX - 1]).astype(int).tolist(), flush=True)


if __name__ == "__main__":
    main()
