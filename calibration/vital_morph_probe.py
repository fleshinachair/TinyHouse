#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Vital's spectral morph (osc_N_spectral_morph_type / _amount) measured through the real plugin, as a change of each harmonic's level and phase.

    python calibration/vital_morph_probe.py [type ...]

The wavetable is one frame holding a band-limited pulse (harmonics 1..256 at level 1, fixed random phases), played at note 36 with the filter and effects off, so each harmonic of the
output is that harmonic's response to the morph. Prints the level (dB against the unmorphed render) of harmonics 1..N for each amount, and writes the raw
numbers to calibration/vital_morph_laws.json.
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
F0 = 440.0 * 2 ** ((NOTE - 69) / 12)
NH = 64
NAMES = {1: "vocode", 2: "formant", 3: "stretch", 4: "inharmonic", 5: "smear", 6: "random", 7: "lowpass", 8: "highpass", 9: "phase disperse", 10: "shepard", 11: "skew"}


def frame_preset(frame: np.ndarray, typ: int, amt: float) -> dict:
    d = RP.base_preset()
    s = d["settings"]
    kf = s["wavetables"][0]["groups"][0]["components"][0]["keyframes"]
    kf[:] = [{"position": 0, "wave_data": base64.b64encode(frame.astype("<f4").tobytes()).decode()}]
    s["osc_1_wave_frame"] = 0.0
    s["filter_1_on"] = 0.0
    s["osc_1_spectral_morph_type"], s["osc_1_spectral_morph_amount"] = float(typ), float(amt)
    s["osc_1_distortion_type"], s["osc_1_distortion_amount"] = 0.0, 0.5
    s["osc_1_random_phase"] = 0.0
    return d


def harmonics(x: np.ndarray, n=NH):
    seg = x[:, int(0.3 * SR):int(1.3 * SR)].mean(axis=0)
    w = np.hanning(len(seg))
    sp = np.fft.rfft(seg * w)
    out = []
    for k in range(1, n + 1):
        b = int(round(k * F0 * len(seg) / SR))
        lo, hi = max(b - 2, 0), b + 3
        i = lo + int(np.argmax(np.abs(sp[lo:hi])))
        out.append(sp[i])
    return np.array(out)


def main():
    types = [int(a) for a in sys.argv[1:]] or [1, 2, 3, 4, 5, 7, 8, 9, 10, 11]
    host = VR.VitalHost()
    sp = np.zeros(1025, complex); sp[1:257] = np.exp(2j * np.pi * np.random.RandomState(1).rand(256))                  # harmonics 1..256 at level 1, random phase: band-limited with a low crest factor, so the oscillator's interpolation does not notch it
    frame = np.fft.irfft(sp, 2048); frame /= np.abs(frame).max()
    ref = harmonics(RP.render(host, frame_preset(frame, 0, 0.0)))
    res = {}
    for t in types:
        for a in (0.0, 0.25, 0.5, 0.75, 1.0):
            h = harmonics(RP.render(host, frame_preset(frame, t, a)))
            res[f"{t}_{a}"] = {"db": (20 * np.log10(np.abs(h) / np.abs(ref).clip(1e-12) + 1e-12)).tolist(), "ph": np.angle(h / ref).tolist()}
            db = res[f"{t}_{a}"]["db"]
            print(f"{NAMES[t]:15s} amt {a:4.2f}  " + " ".join(f"{v:5.0f}" for v in db[:32]))
    (ROOT / "calibration" / "vital_morph_laws.json").write_text(json.dumps(res))


if __name__ == "__main__":
    main()
