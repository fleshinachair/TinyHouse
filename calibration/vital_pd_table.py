#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Measure Vital's oscillator phase distortion (types 1..6) as tables the converter can bake into a wavetable: out(p) = gain(p) * frame(map(p)).

    python calibration/vital_pd_table.py            # writes converter/vital_pd_table.npz

Two one-frame wavetables go through the real plugin: a rising ramp x (the output is then gain * x with x = 2 map - 1) and its cube (gain * x^3; a constant frame would be
removed with the output's DC), so x = sign * sqrt(cube / ramp) and gain = ramp / x. Folded by oscillator phase over many cycles, that gives gain(p) and map(p) for each distortion
type at 65 amounts. Types 7.. (FM / RM from another oscillator) are not phase maps and are not measured here.
"""
from __future__ import annotations
import base64, json, sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "calibration"))
import vital_render as VR                                           # noqa: E402
import vital_route_probe as RP                                      # noqa: E402

SR = 44100
NOTE = 36
F0 = 2 * 440.0 * 2 ** ((NOTE - 69) / 12)           # the host sounds this note an octave up (measured)
NB = 512
NA = 65
TYPES = (1, 2, 3, 4, 5, 6)
CONST = 0.5


def preset(frame, typ, amt):
    d = RP.base_preset()
    s = d["settings"]
    kf = s["wavetables"][0]["groups"][0]["components"][0]["keyframes"]
    kf[:] = [{"position": 0, "wave_data": base64.b64encode(np.asarray(frame, dtype="<f4").tobytes()).decode()}]
    s["osc_1_wave_frame"] = 0.0
    s["filter_1_on"] = 0.0
    s["osc_1_spectral_morph_type"], s["osc_1_spectral_morph_amount"] = 0.0, 0.5
    s["osc_1_distortion_type"], s["osc_1_distortion_amount"] = float(typ), float(amt)
    s["osc_1_random_phase"] = 0.0
    s["osc_1_level"] = 1.0
    s["osc_1_transpose"], s["osc_1_tune"] = 0.0, 0.0
    s["osc_1_unison_voices"] = 1.0
    return d


def fold(host, frame, typ, amt, shift):
    x = np.asarray(host.render(json.dumps(preset(frame, typ, amt)).encode(), ((NOTE, 100, 0.0, 2.4),), 2.6), dtype=np.float32).mean(axis=0)
    i = np.arange(int(0.4 * SR), int(2.2 * SR))
    ph = ((F0 * i / SR) - shift) % 1.0
    b = np.minimum((ph * NB).astype(int), NB - 1)
    return np.bincount(b, weights=x[i], minlength=NB) / np.maximum(np.bincount(b, minlength=NB), 1)


def neutralise(maps, gains):
    """The ramp / cube estimate is slightly biased by the plugin's own band-limiting (a neutral setting reads gain 0.88, and the wrap bin 0.16): measure that bias on a
    setting that is known to do nothing (Quantize at amount 0) and divide it out."""
    m0 = maps[2, 0].copy()
    ident = (np.arange(maps.shape[2]) + 0.5) / maps.shape[2]
    good = np.abs(gains[2, 0] - np.median(gains[2, 0])) < 0.2
    maps = np.where(good, maps + (ident - m0), maps)
    bias = float(np.median(gains[2, 0]))                                   # a neutral setting reads this instead of 1
    out = np.ones_like(gains)
    out[1] = np.clip(gains[1] / bias, 0.0, 1.2)                            # only Formant has a real gain (a window); the others do not change the level
    return maps, out
    return maps, gains


def main():
    host = VR.VitalHost()
    ramp = 2 * (np.arange(2048) / 2048.0) - 1
    cube = ramp ** 3
    # the phase origin: where the unmodified ramp wraps
    base = fold(host, ramp, 0, 0.5, 0.0)
    wrap = int(np.argmax(np.abs(np.diff(np.concatenate([base, base[:1]])))))
    shift = (wrap + 1) / NB
    base = fold(host, ramp, 0, 0.5, shift)
    print("origin shift", shift, "ramp check", np.round(base[[0, 1, 2, 255, 256, 509, 510, 511]], 3), flush=True)
    sc = 1.0 / np.abs(base).max() * 0.98                                   # the unmodified level, so the maps come out in the frame's own units
    base3 = fold(host, cube, 0, 0.5, shift) * sc
    amps = np.linspace(0, 1, NA)
    maps = np.zeros((len(TYPES), NA, NB), np.float32)
    gains = np.ones_like(maps)
    for ti, t in enumerate(TYPES):
        for ai, a in enumerate(amps):
            A = fold(host, ramp, t, a, shift) * sc                         # g * x            (x = 2 map - 1)
            B = fold(host, cube, t, a, shift) * sc                         # g * x ** 3
            big = np.abs(A) > 0.15
            x = np.where(big, np.sign(A) * np.sqrt(np.clip(B / np.where(big, A, 1.0), 0.0, 4.0)), A)
            g = np.where(big, A / np.where(np.abs(x) > 1e-3, x, 1.0), 1.0)
            g = np.clip(g, 0.0, 4.0)
            x = np.where(big, x, A / np.maximum(g, 1e-3))
            maps[ti, ai] = np.clip((x + 1) / 2, -0.5, 1.5)
            gains[ti, ai] = g
        print("type", t, "done", flush=True)
    maps, gains = neutralise(maps, gains)
    np.savez_compressed(ROOT / "converter" / "vital_pd_table.npz", maps=maps.astype(np.float16), gains=gains.astype(np.float16), amps=amps.astype(np.float32), types=np.array(TYPES))


if __name__ == "__main__":
    main()
