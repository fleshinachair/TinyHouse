#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Measure Vital's distortion filter through the real plugin: its magnitude response against cutoff, resonance and blend.

    python calibration/vital_distfilt_probe.py [-o calibration/vital_distfilt_laws.json]

A quiet saw at note 36 (65 Hz, so its harmonics sample the spectrum densely) goes through the distortion at its lowest drive with the filter off and on;
the ratio of the harmonic levels is the filter's response. Settings are Vital's own: distortion_filter_order 1 = pre, 2 = post (0 = off), cutoff in MIDI note
numbers, resonance and blend (0 = low-pass, 1 = band-pass, 2 = high-pass) in their stored ranges.
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
import vital_route_probe as RP                                      # noqa: E402

SR = 44100
F0 = 440.0 * 2 ** ((36 - 69) / 12)


def harmonics(x: np.ndarray) -> np.ndarray:
    seg = x[:, int(0.4 * SR):int(1.3 * SR)].mean(axis=0)
    mag = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    fr = np.fft.rfftfreq(len(seg), 1 / SR)
    ks = np.arange(1, int(18000 / F0))
    out = np.zeros(len(ks))
    for i, k in enumerate(ks):
        j = int(round(k * F0 / (fr[1])))
        out[i] = mag[j - 2:j + 3].max()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "calibration/vital_distfilt_laws.json")
    ap.add_argument("--drive", type=float, default=-30.0)
    a = ap.parse_args()
    host = VR.VitalHost()
    base = RP.base_preset()
    s = base["settings"]
    s["filter_1_on"] = 0.0
    s["osc_1_wave_frame"] = 0.0
    s["osc_1_random_phase"] = 0.0
    s["osc_1_midi_track"], s["osc_1_transpose"] = 1.0, 0.0
    s["osc_1_level"] = 0.1
    s["distortion_on"], s["distortion_type"], s["distortion_drive"], s["distortion_mix"] = 1.0, 0.0, a.drive, 1.0

    def render(**kw) -> np.ndarray:
        d = json.loads(json.dumps(base))
        d["settings"].update(kw)
        return harmonics(np.asarray(host.render(json.dumps(d).encode(), ((36, 100, 0.0, 1.5),), 1.6), dtype=np.float32))

    ref = render(distortion_filter_order=0.0)
    freqs = F0 * np.arange(1, len(ref) + 1)
    out = {"f0": F0, "freqs": freqs.tolist(), "rows": []}
    for order in (1.0, 2.0):
        for blend in (0.0, 1.0, 2.0):
            for res in (0.0, 0.5, 1.0):
                for cut in (50.0, 70.0, 90.0, 110.0):
                    h = render(distortion_filter_order=order, distortion_filter_blend=blend, distortion_filter_resonance=res, distortion_filter_cutoff=cut)
                    resp = 20 * np.log10(np.maximum(h, 1e-12) / np.maximum(ref, 1e-12))
                    out["rows"].append({"order": order, "blend": blend, "res": res, "cut": cut, "db": [round(float(v), 2) for v in resp]})
                    fc = 440.0 * 2 ** ((cut - 69) / 12)
                    pts = [int(round(m * fc / F0)) - 1 for m in (0.25, 0.5, 1.0, 2.0, 4.0)]
                    print(f"order {int(order)} blend {blend:.0f} res {res:.1f} cut {cut:.0f} ({fc:7.0f} Hz): " + "  ".join(f"{m}x {resp[min(max(p,0),len(resp)-1)]:+6.1f}" for m, p in zip((0.25, 0.5, 1, 2, 4), pts)), flush=True)
    a.out.write_text(json.dumps(out), encoding="utf-8")
    print("exit")


if __name__ == "__main__":
    main()
