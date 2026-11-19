#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Spectral morph, measured with single-harmonic frames: where does a sine at harmonic h go, and how loud, for each type and amount?

    python calibration/vital_morph2_probe.py [--types 1 2 ...] [--amounts 0.25 0.5 0.75 1.0] [-o calibration/vital_morph2.json]

The frame is a sine at harmonic h (amplitude 0.5), the note C1-ish (F0 = 2 * 440 * 2^((36 - 69) / 12) = 130.8 Hz); the output's harmonics 1..64 are read in dB re the unmorphed
sine. Results are written as {type: {amount: {h: [dB of harmonics 1..64]}}}.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "calibration"))
import vital_render as VR                                           # noqa: E402
import vital_route_probe as RP                                      # noqa: E402

SR = 44100
NOTE = 36
F0 = 2 * 440.0 * 2 ** ((NOTE - 69) / 12.0)
NH = 64


def frame_b64(h: int) -> str:
    x = np.arange(2048) / 2048.0
    return base64.b64encode((0.5 * np.sin(2 * np.pi * h * x)).astype("<f4").tobytes()).decode()


def preset(h: int, typ: int, amt: float, spread: float = 0.0) -> bytes:
    d = RP.base_preset()
    s = d["settings"]
    kf = s["wavetables"][0]["groups"][0]["components"][0]["keyframes"]
    kf[:] = [{"position": 0, "wave_data": frame_b64(h)}, {"position": 255, "wave_data": frame_b64(h)}]
    s["osc_1_transpose"], s["osc_1_tune"] = 0.0, 0.0
    s["osc_1_wave_frame"], s["osc_1_level"], s["osc_1_random_phase"] = 0.0, 1.0, 0.0
    s["osc_1_distortion_type"], s["osc_1_spectral_morph_type"] = 0.0, float(typ)
    s["osc_1_spectral_morph_amount"], s["osc_1_spectral_morph_spread"] = float(amt), float(spread)
    s["filter_1_on"] = 0.0
    return json.dumps(d).encode()


def harmonics(x: np.ndarray) -> np.ndarray:
    seg = x[int(0.2 * SR):int(0.2 * SR) + 32768]
    sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) * 2 / (len(seg) * 0.5)
    f = np.fft.rfftfreq(len(seg), 1 / SR)
    out = np.zeros(NH)
    for k in range(1, NH + 1):
        m = np.abs(f - k * F0) < F0 * 0.12
        out[k - 1] = sp[m].max() if m.any() else 0.0
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--types", type=int, nargs="*", default=list(range(1, 12)))
    ap.add_argument("--amounts", type=float, nargs="*", default=[0.25, 0.5, 0.75, 1.0])
    ap.add_argument("--hs", type=int, nargs="*", default=[1, 2, 3, 5, 8, 13, 21])
    ap.add_argument("-o", "--out", default=str(ROOT / "calibration/vital_morph2.json"))
    a = ap.parse_args()
    host = VR.VitalHost()
    res: dict = {}
    for typ in a.types:
        res[typ] = {}
        for amt in a.amounts:
            res[typ][amt] = {}
            for h in a.hs:
                x = np.asarray(host.render(preset(h, typ, amt), ((NOTE, 100, 0.0, 1.2),), 1.6))[0]
                res[typ][amt][h] = harmonics(x).tolist()
        print("type", typ, "done", flush=True)
    base = {h: harmonics(np.asarray(host.render(preset(h, 0, 0.0), ((NOTE, 100, 0.0, 1.2),), 1.6))[0]).tolist() for h in a.hs}
    json.dump({"f0": F0, "base": base, "res": res}, open(a.out, "w"))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
