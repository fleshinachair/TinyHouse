#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Measure Vital's sample oscillator through the real plugin: root pitch, key tracking, level law, loop and bounce.

    python calibration/vital_sample_probe.py

The test sample is a 100 Hz sine (441-sample cycles) at amplitude 0.5, 44.1 kHz, so pitch is read from the FFT peak and level from the rms.
"""
from __future__ import annotations

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


def sample_preset(samples: np.ndarray, **kw) -> bytes:
    d = RP.base_preset()
    s = d["settings"]
    s["osc_1_on"] = 0.0
    s["filter_1_on"] = 0.0
    s["sample_on"] = 1.0
    s["sample_level"] = kw.pop("level", 0.7071)
    s["sample_loop"] = kw.pop("loop", 1.0)
    s["sample_keytrack"] = kw.pop("keytrack", 1.0)
    s["sample_bounce"] = kw.pop("bounce", 0.0)
    s["sample_random_phase"] = kw.pop("random_phase", 0.0)
    s["sample_transpose"] = kw.pop("transpose", 0.0)
    s["sample_tune"] = kw.pop("tune", 0.0)
    s["sample_pan"] = kw.pop("pan", 0.0)
    s["sample_destination"] = kw.pop("destination", 3.0)
    s.update(kw)
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    s["sample"] = {"length": len(samples), "name": "probe", "sample_rate": SR,
                   "samples": base64.b64encode(pcm).decode(), "samples_stereo": base64.b64encode(pcm).decode()}
    return json.dumps(d).encode()


def peak_hz(x: np.ndarray, lo=20.0, hi=8000.0) -> float:
    w = np.hanning(len(x))
    sp = np.abs(np.fft.rfft(x * w, n=len(x) * 8))
    f = np.fft.rfftfreq(len(x) * 8, 1 / SR)
    m = (f >= lo) & (f <= hi)
    i = np.argmax(sp * m)
    return float(f[i])


def main() -> None:
    host = VR.VitalHost()
    n = 44100
    sine = 0.5 * np.sin(2 * np.pi * np.arange(n) / 441.0)
    out = {}

    def play(pre: bytes, note: int, dur: float = 1.0, vel: int = 100):
        a = np.asarray(host.render(pre, ((note, vel, 0.0, dur),), dur + 0.3))
        return a[0, int(0.15 * SR):int((0.15 + 0.6) * SR)] if a.shape[0] else a

    print("key tracking on: pitch vs note (Hz of the 100 Hz sample)")
    for note in (36, 48, 60, 72, 84):
        x = play(sample_preset(sine), note)
        print(" note", note, "->", round(peak_hz(x), 2), "Hz", "ratio", round(peak_hz(x) / 100.0, 4))
    print("key tracking off")
    for note in (36, 60, 84):
        x = play(sample_preset(sine, keytrack=0.0), note)
        print(" note", note, "->", round(peak_hz(x), 2), "Hz")
    print("transpose / tune (note 60)")
    for tr, tu in ((12, 0), (-12, 0), (0, 50), (0, -100)):
        x = play(sample_preset(sine, transpose=float(tr), tune=float(tu)), 60)
        print(" transpose", tr, "tune", tu, "->", round(peak_hz(x), 2), "Hz")
    print("level law (rms of the 0.5-amplitude sine = 0.3536 at unity)")
    for lv in (0.05, 0.1, 0.25, 0.5, 0.7071, 0.85, 1.0):
        x = play(sample_preset(sine, level=lv), 60)
        r = float(np.sqrt((x ** 2).mean()))
        print(" level", lv, "rms", round(r, 5), "dB re unity", round(20 * np.log10(r / 0.35355 + 1e-12), 2))
    print("loop / one-shot (rms in the last 0.2 s of a 0.9 s note after 0.7 s)")
    for lp, bn in ((1, 0), (0, 0), (1, 1)):
        a = np.asarray(host.render(sample_preset(np.concatenate([sine[:22050]]), loop=float(lp), bounce=float(bn)), ((60, 100, 0.0, 1.2),), 1.6))
        x = a[0]
        print(" loop", lp, "bounce", bn, "rms 0.1-0.3 s", round(float(np.sqrt((x[4410:13230] ** 2).mean())), 4), "rms 0.8-1.0 s", round(float(np.sqrt((x[35280:44100] ** 2).mean())), 4))


if __name__ == "__main__":
    main()
