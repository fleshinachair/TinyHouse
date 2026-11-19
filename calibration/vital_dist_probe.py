#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Measure Vital's distortion transfer curves through the real plugin: output against input, per type and drive.

    python calibration/vital_dist_probe.py [-o calibration/vital_dist_laws.json]

A sine is played with the distortion off and on (same phase: the oscillator's random phase is off), and the wet sample is plotted against the dry one, so
the static curve of each type (0 soft clip, 1 hard clip, 2 linear fold, 3 sine fold, 4 bit crush, 5 down sample) can be read off. Drive is in dB.
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "calibration/vital_dist_laws.json")
    ap.add_argument("--types", type=int, nargs="*", default=[0, 1, 2, 3])
    ap.add_argument("--drives", type=float, nargs="*", default=[-12.0, 0.0, 12.0, 24.0])
    ap.add_argument("--levels", type=float, nargs="*", default=[0.7, 0.2])
    a = ap.parse_args()
    host = VR.VitalHost()
    base = RP.base_preset()
    s = base["settings"]
    s["filter_1_on"] = 0.0
    s["osc_1_wave_frame"] = 255.0
    s["osc_1_random_phase"] = 0.0
    s["osc_1_midi_track"], s["osc_1_transpose"] = 1.0, 0.0
    s["distortion_filter_order"] = 0.0

    def render(**kw) -> np.ndarray:
        d = json.loads(json.dumps(base))
        d["settings"].update(kw)
        x = np.asarray(host.render(json.dumps(d).encode(), ((48, 100, 0.0, 1.5),), 1.6), dtype=np.float32)
        return x[0, int(0.5 * SR):int(1.2 * SR)]

    out: dict = {}
    for lv in a.levels:
        dry = render(osc_1_level=lv, distortion_on=0.0)
        print(f"level {lv}: dry peak {np.abs(dry).max():.4f}")
        for ty in a.types:
            for dr in a.drives:
                wet = render(osc_1_level=lv, distortion_on=1.0, distortion_type=float(ty), distortion_drive=dr, distortion_mix=1.0)
                edges = np.linspace(-np.abs(dry).max(), np.abs(dry).max(), 13)
                idx = np.digitize(dry, edges)
                pts = [(round(float(dry[idx == i].mean()), 4), round(float(wet[idx == i].mean()), 4)) for i in range(1, 13) if (idx == i).sum() > 20]
                out[f"{lv}|{ty}|{dr}"] = pts
                print(f"  type {ty} drive {dr:+5.1f}: wet peak {np.abs(wet).max():.4f}  " + " ".join(f"{x:+.2f}>{y:+.3f}" for x, y in pts[len(pts) // 2:]), flush=True)
    a.out.write_text(json.dumps(out), encoding="utf-8")
    print("exit")


if __name__ == "__main__":
    main()
