#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Vital's voice filter Mix, measured through the plugin: a saw (60 st) through a low-pass far below its harmonics, at several Mix values. The harmonics the filter removes
come back only through the dry part, so their level against Mix 0 gives the dry gain, and the level of the fundamental region gives the wet gain.

    python calibration/vital_fmix_probe.py
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
import vital_rm_probe as RM                                         # noqa: E402

SR = 44100


def render(host, mix: float, cutoff: float, model=0, style=0) -> np.ndarray:
    n = np.arange(2048); w = np.zeros(2048)
    for k in range(1, 60):
        w += np.sin(2 * np.pi * k * n / 2048) / k
    d = json.loads(RM.preset(0.0, 0, 24.0, 0.0)); s = d["settings"]
    b64 = base64.b64encode((w / np.abs(w).max()).astype("<f4").tobytes()).decode()
    s["wavetables"][0]["groups"][0]["components"][0]["keyframes"][:] = [{"position": 0, "wave_data": b64}, {"position": 255, "wave_data": b64}]
    s["osc_2_on"], s["osc_1_midi_track"], s["osc_1_random_phase"] = 0.0, 1.0, 0.0
    s["filter_1_on"], s["filter_1_model"], s["filter_1_style"], s["filter_1_blend"] = 1.0, float(model), float(style), 0.0
    s["filter_1_cutoff"], s["filter_1_resonance"], s["filter_1_drive"], s["filter_1_mix"], s["filter_1_keytrack"] = cutoff, 0.0, 0.0, mix, 0.0
    s["env_1_attack"], s["env_1_sustain"], s["env_1_release"] = 0.0, 1.0, 0.05
    return np.asarray(host.render(json.dumps(d).encode(), ((60, 100, 0.0, 1.5),), 2.0))[0]


def band(x: np.ndarray, lo: float, hi: float) -> float:
    a = x[int(0.4 * SR):int(1.2 * SR)]
    sp = np.abs(np.fft.rfft(a * np.hanning(len(a)))) ** 2
    fr = np.fft.rfftfreq(len(a), 1 / SR)
    return 10 * np.log10(sp[(fr > lo) & (fr < hi)].sum() + 1e-20)


def engine(mix: float, cutoff: float) -> np.ndarray:
    import vital_porta_probe as PP
    import numpy as _np
    d = json.loads(RM.preset(0.0, 0, 24.0, 0.0))
    return None if d is None else PP.engine_render(_set(d, mix, cutoff), [(0.0, 1.5, 60)], 2.0)


def _set(d: dict, mix: float, cutoff: float) -> dict:
    n = np.arange(2048); w = np.zeros(2048)
    for k in range(1, 60):
        w += np.sin(2 * np.pi * k * n / 2048) / k
    s = d["settings"]
    b64 = base64.b64encode((w / np.abs(w).max()).astype("<f4").tobytes()).decode()
    s["wavetables"][0]["groups"][0]["components"][0]["keyframes"][:] = [{"position": 0, "wave_data": b64}, {"position": 255, "wave_data": b64}]
    s["osc_2_on"], s["osc_1_midi_track"], s["osc_1_random_phase"] = 0.0, 1.0, 0.0
    s["filter_1_on"], s["filter_1_model"], s["filter_1_style"], s["filter_1_blend"] = 1.0, 0.0, 0.0, 0.0
    s["filter_1_cutoff"], s["filter_1_resonance"], s["filter_1_drive"], s["filter_1_mix"], s["filter_1_keytrack"] = cutoff, 0.0, 0.0, mix, 0.0
    s["env_1_attack"], s["env_1_sustain"], s["env_1_release"] = 0.0, 1.0, 0.05
    return d


def main() -> None:
    host = VR.VitalHost()
    if "--engine" in sys.argv:
        for cut in (40.0, 60.0):
            dry = band(engine(0.0, cut), 3000, 9000)
            print(f"engine, cutoff note {cut}: " + "  ".join(f"mix {m}: {band(engine(m, cut), 3000, 9000) - dry:+.2f} dB" for m in (0.25, 0.5, 0.75)))
        return
    for cut in (40.0, 60.0):
        ref = {}
        for mix in (1.0, 0.0, 0.25, 0.5, 0.75):
            x = render(host, mix, cut)
            lo, hi = band(x, 200, 400), band(x, 3000, 9000)
            ref[mix] = (lo, hi)
        dry_hi, wet_lo = ref[0.0][1], ref[1.0][0]
        print(f"cutoff note {cut}: wet-only low {ref[1.0][0]:.1f} dB, high {ref[1.0][1]:.1f}; dry low {ref[0.0][0]:.1f}, high {ref[0.0][1]:.1f}")
        for mix in (0.25, 0.5, 0.75):
            lo, hi = ref[mix]
            print(f"   mix {mix}: high-band gain vs dry {hi - dry_hi:+.2f} dB  (linear {20*np.log10(max(1-mix,1e-6)):+.2f}, equal-power {20*np.log10(np.cos(mix*np.pi/2)):+.2f})")


if __name__ == "__main__":
    main()
