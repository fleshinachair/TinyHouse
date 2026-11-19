#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Vital's unison stack styles, measured through the plugin: a sine with zero detune, so each unison voice is a separate spectral line at its own pitch.

    python calibration/vital_stack_probe.py [--voices 2 3 4 5 8 16] [--detune 0.0]

Prints, per style and voice count, the lines found (semitones from the played note, relative level in dB).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "calibration"))
import vital_render as VR                                           # noqa: E402
import vital_rm_probe as RM                                         # noqa: E402

SR = 44100


def lines(x: np.ndarray, note: int, floor_db: float = -35.0) -> list[tuple[float, float]]:
    n = 1 << 16
    seg = x[int(0.3 * SR):int(0.3 * SR) + n] * np.hanning(n)
    sp = np.abs(np.fft.rfft(seg))
    db = 20 * np.log10(sp / sp.max() + 1e-12)
    f0 = 440.0 * 2 ** ((note - 69) / 12)
    out = []
    for k in range(2, len(sp) - 2):
        if db[k] > floor_db and db[k] >= db[k - 1] and db[k] > db[k + 1] and db[k] >= db[k - 2] and db[k] > db[k + 2]:
            f = k * SR / n
            out.append((round(12 * np.log2(f / f0), 2), round(float(db[k]), 1)))
    return out


def compare(a) -> None:
    """Vital and the engine side by side: the lines (semitones from the note) of the same stack."""
    import vital_porta_probe as PP
    host = VR.VitalHost()
    worst = 0.0
    for st in a.styles:
        for nv in a.voices:
            d = json.loads(RM.preset(0.0, 0, 24.0, 0.0)); s = d["settings"]
            s["osc_2_on"], s["osc_1_midi_track"] = 0.0, 1.0
            s["osc_1_unison_voices"], s["osc_1_unison_detune"], s["osc_1_stack_style"] = float(nv), a.detune, float(st)
            if a.range:
                s["osc_1_detune_range"], s["osc_1_detune_power"], s["osc_1_stereo_spread"], s["osc_1_unison_blend"] = a.range, a.power, 0.0, 1.0
            s["env_1_attack"], s["env_1_sustain"], s["env_1_release"] = 0.0, 1.0, 0.05
            v = np.asarray(host.render(json.dumps(d).encode(), ((60, 100, 0.0, 1.5),), 2.0))[0]
            e = PP.engine_render(d, [(0.0, 1.5, 60)], 2.0)
            lv, le = [x[0] for x in lines(v, 60)], [x[0] for x in lines(e, 60)]
            ok = len(lv) == len(le) and all(abs(p - q) < 0.15 for p, q in zip(lv, le))
            if not ok:
                print(f"style {st:2d} voices {nv:2d}:\n   vital  {[float(x) for x in lv]}\n   engine {[float(x) for x in le]}")
            else:
                print(f"style {st:2d} voices {nv:2d}: lines agree ({len(lv)})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voices", type=int, nargs="+", default=[2, 3, 4, 5, 8, 16])
    ap.add_argument("--detune", type=float, default=0.0)
    ap.add_argument("--range", type=float, default=0.0, help="detune range in semitones (with --detune 10: the whole spread of the stack)")
    ap.add_argument("--styles", type=int, nargs="+", default=list(range(0, 11)))
    ap.add_argument("--power", type=float, default=1.0, help="detune power")
    ap.add_argument("--engine", action="store_true", help="compare with the engine")
    a = ap.parse_args()
    if a.engine:
        return compare(a)
    host = VR.VitalHost()
    for st in a.styles:
        for nv in a.voices:
            d = json.loads(RM.preset(0.0, 0, 24.0, 0.0)); s = d["settings"]
            s["osc_2_on"], s["osc_1_midi_track"] = 0.0, 1.0
            s["osc_1_unison_voices"], s["osc_1_unison_detune"], s["osc_1_stack_style"] = float(nv), a.detune, float(st)
            if a.range:
                s["osc_1_detune_range"], s["osc_1_detune_power"], s["osc_1_stereo_spread"], s["osc_1_unison_blend"] = a.range, a.power, 0.0, 1.0
            s["env_1_attack"], s["env_1_sustain"], s["env_1_release"] = 0.0, 1.0, 0.05
            x = np.asarray(host.render(json.dumps(d).encode(), ((60, 100, 0.0, 1.5),), 2.0))[0]
            print(f"style {st:2d} voices {nv:2d}: {lines(x, 60)}")


if __name__ == "__main__":
    main()
