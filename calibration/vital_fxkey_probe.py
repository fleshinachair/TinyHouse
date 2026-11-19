#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Vital's Filter FX key tracking, measured through the plugin: a saw held at several notes through the Filter FX low-pass (model Analog, 12 dB) at a fixed cutoff,
Key Track 0 and 1; the corner is read from the level of the harmonics against an ideal 1/h saw.

    python calibration/vital_fxkey_probe.py [--engine]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "calibration"))
import vital_render as VR                                           # noqa: E402
import vital_rm_probe as RM                                         # noqa: E402

SR = 44100


def harmonics(x: np.ndarray, f0: float, n: int = 40) -> np.ndarray:
    seg = x[int(0.4 * SR):int(0.4 * SR) + 32768] * np.hanning(32768)
    t = np.arange(32768) / SR
    return np.array([abs((seg * np.exp(-2j * np.pi * k * f0 * t)).sum()) for k in range(1, n + 1)])


def preset(note_cutoff: float, keytrack: float, drive: float = 0.0) -> dict:
    d = json.loads(RM.preset(0.0, 0, 24.0, 0.0)); s = d["settings"]
    d["synth_version"] = "1.5.5"
    s["osc_2_on"], s["osc_1_midi_track"] = 0.0, 1.0
    s["osc_1_wave_frame"] = 0.0
    s["env_1_attack"], s["env_1_sustain"], s["env_1_release"] = 0.0, 1.0, 0.1
    s["filter_1_on"] = 0.0
    s["filter_fx_on"], s["filter_fx_model"], s["filter_fx_style"], s["filter_fx_blend"] = 1.0, 0.0, 0.0, 0.0
    s["filter_fx_cutoff"], s["filter_fx_resonance"], s["filter_fx_mix"] = note_cutoff, 0.0, 1.0
    s["filter_fx_keytrack"], s["filter_fx_drive"] = keytrack, drive
    return d


def corner_note(h: np.ndarray, f0: float, ref: np.ndarray) -> float:
    """The frequency (as a MIDI note) where the ratio to the unfiltered harmonics falls to -3 dB."""
    r = 20 * np.log10(np.maximum(h, 1e-12) / np.maximum(ref, 1e-12))
    f = f0 * np.arange(1, len(h) + 1)
    below = np.where(r < -3.0)[0]
    if len(below) == 0:
        return float("nan")
    i = below[0]
    if i == 0:
        return 69 + 12 * np.log2(f[0] / 440.0)
    fi = f[i - 1] + (f[i] - f[i - 1]) * (-3.0 - r[i - 1]) / (r[i] - r[i - 1])
    return 69 + 12 * np.log2(fi / 440.0)


def engine_corners() -> None:
    import vital_porta_probe as PP
    for kt in (0.0, 1.0, 0.5, -1.0):
        row = []
        for n in (36, 48, 60, 72):
            f0 = 440.0 * 2 ** ((n - 69) / 12)
            ref = harmonics(PP.engine_render(preset(136.0, 0.0), [(0.0, 1.0, n)], 1.4), f0)
            h = harmonics(PP.engine_render(preset(90.0, kt), [(0.0, 1.0, n)], 1.4), f0)
            row.append(round(float(corner_note(h, f0, ref)), 1))
        print(f"ENGINE cutoff note 90, key track {kt:+.1f}: corners {row}", flush=True)


def main() -> None:
    if "--engine" in sys.argv:
        return engine_corners()
    host = VR.VitalHost()
    notes = (36, 48, 60, 72)
    for kt in (0.0, 1.0, 0.5, -1.0):
        row = []
        for n in notes:
            f0 = 440.0 * 2 ** ((n - 69) / 12)
            ref = harmonics(np.asarray(host.render(json.dumps({**preset(136.0, 0.0), }).encode(), ((n, 100, 0.0, 1.0),), 1.4))[0], f0)
            h = harmonics(np.asarray(host.render(json.dumps(preset(90.0, kt)).encode(), ((n, 100, 0.0, 1.0),), 1.4))[0], f0)
            row.append(round(corner_note(h, f0, ref), 1))
        print(f"cutoff note 90, key track {kt:+.1f}: -3 dB corner (MIDI note) at played notes {notes}: {row}", flush=True)


if __name__ == "__main__":
    main()
