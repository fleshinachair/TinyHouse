#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Measure Vital's comb filter (filter model 6) through the real plugin: magnitude response of each style against cutoff and resonance.

    python calibration/vital_comb_probe.py

A quiet saw at note 24 (32.7 Hz, harmonics every 33 Hz) goes through filter 1 set to the comb model; the ratio of the harmonic levels to the unfiltered saw is the response.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "calibration"))
import vital_render as VR                                           # noqa: E402
import vital_route_probe as RP                                      # noqa: E402

SR = 44100
NOTE = 24
F0 = 440.0 * 2 ** ((NOTE - 69) / 12)


def harm(x: np.ndarray) -> np.ndarray:
    seg = x[:, int(0.4 * SR):int(1.3 * SR)].mean(axis=0)
    mag = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    fr = np.fft.rfftfreq(len(seg), 1 / SR)
    ks = np.arange(1, int(8000 / F0))
    return np.array([mag[int(round(k * F0 / fr[1])) - 2:int(round(k * F0 / fr[1])) + 3].max() for k in ks])


def main() -> None:
    host = VR.VitalHost()
    base = RP.base_preset()
    s = base["settings"]
    s["osc_1_wave_frame"] = 0.0
    s["osc_1_random_phase"] = 0.0
    s["osc_1_midi_track"], s["osc_1_transpose"] = 1.0, 0.0
    s["osc_1_level"] = 0.1
    s["filter_1_keytrack"] = 0.0

    def render(**kw) -> np.ndarray:
        d = json.loads(json.dumps(base))
        d["settings"].update(kw)
        return harm(np.asarray(host.render(json.dumps(d).encode(), ((NOTE, 100, 0.0, 1.5),), 1.6), dtype=np.float32))

    ref = render(filter_1_on=0.0)
    freqs = F0 * np.arange(1, len(ref) + 1)
    out = {}
    for cut in (48.0, 60.0):
        fc = 440.0 * 2 ** ((cut - 69) / 12)
        for res in (0.0, 0.5, 0.9):
            for style in range(6):
                h = render(filter_1_on=1.0, filter_1_model=6.0, filter_1_style=float(style), filter_1_cutoff=cut, filter_1_resonance=res, filter_1_blend=0.0)
                db = 20 * np.log10(np.maximum(h, 1e-12) / np.maximum(ref, 1e-12))
                out[f"{cut}|{res}|{style}"] = db.tolist()
                i1 = int(np.argmin(abs(freqs - fc)))
                pk = [f"{freqs[i]:.0f}:{db[i]:+.0f}" for i in range(1, len(db) - 1) if db[i] > db[i - 1] and db[i] > db[i + 1] and db[i] > db.max() - 6][:6]
                print(f"cut {cut:.0f} ({fc:.0f} Hz) res {res:.1f} style {style}: mean {db.mean():+.1f} max {db.max():+.1f} min {db.min():+.1f}  peaks {pk}", flush=True)
    (ROOT / "calibration/vital_comb_laws.json").write_text(json.dumps({"f0": F0, "freqs": freqs.tolist(), "rows": out}), encoding="utf-8")
    print("exit")


if __name__ == "__main__":
    main()
