#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Vital's oscillator phase distortion (osc_N_distortion_type / _amount) read off as a phase map, through the real plugin.

    python calibration/vital_pd_probe.py [type ...]

The wavetable is one frame holding a rising ramp, so the output at oscillator phase p is 2 * map(p) - 1: the plugin's output, folded by phase, IS the map.
Prints map(p) at 20 phases for each amount (a flat step in the map is a hold, a jump is a reset).
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
NAMES = {1: "sync", 2: "formant", 3: "quantize", 4: "bend", 5: "squeeze", 6: "pulse"}


def preset(typ: int, amt: float) -> dict:
    d = RP.base_preset()
    s = d["settings"]
    x = np.arange(2048) / 2048.0
    kf = s["wavetables"][0]["groups"][0]["components"][0]["keyframes"]
    kf[:] = [{"position": 0, "wave_data": base64.b64encode((2 * x - 1).astype("<f4").tobytes()).decode()}]
    s["osc_1_wave_frame"] = 0.0
    s["filter_1_on"] = 0.0
    s["osc_1_spectral_morph_type"], s["osc_1_spectral_morph_amount"] = 0.0, 0.5
    s["osc_1_distortion_type"], s["osc_1_distortion_amount"] = float(typ), float(amt)
    s["osc_1_random_phase"] = 0.0
    s["osc_1_level"] = 1.0
    s["osc_1_transpose"], s["osc_1_tune"] = 0.0, 0.0
    s["osc_1_unison_voices"] = 1.0
    return d


def phase_map(host, typ, amt, nb=20):
    x = np.asarray(host.render(json.dumps(preset(typ, amt)).encode(), ((NOTE, 100, 0.0, 1.8),), 2.0), dtype=np.float32).mean(axis=0)
    i = np.arange(int(0.2 * SR), int(0.5 * SR))
    seg = x[i] - x[i].mean()
    sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 1 << 18))
    fr = np.fft.rfftfreq(1 << 18, 1 / SR)
    f0 = fr[(fr > 20) & (fr < 1500)][np.argmax(sp[(fr > 20) & (fr < 1500)])] if typ == 0 else phase_map.f0
    phase_map.f0 = f0
    ph = (f0 * i / SR) % 1.0
    ref = x[i]
    out = []
    for k in range(nb):
        m = (ph >= k / nb) & (ph < k / nb + 0.01)
        out.append(float(ref[m].mean()))
    return out


def main():
    types = [int(a) for a in sys.argv[1:]] or [0, 1, 2, 3, 4, 5, 6]
    host = VR.VitalHost()
    res = {}
    base = phase_map(host, 0, 0.5)
    sc = max(abs(v) for v in base)
    for t in types:
        for a in (0.0, 0.25, 0.5, 0.75, 1.0):
            m = phase_map(host, t, a)
            res[f"{t}_{a}"] = m
            print(f"{NAMES.get(t, 'none'):9s} amt {a:4.2f} " + " ".join(f"{v / sc:5.2f}" for v in m))
    (ROOT / "calibration" / "vital_pd_laws.json").write_text(json.dumps(res))


if __name__ == "__main__":
    main()
