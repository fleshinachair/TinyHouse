#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Spectral morph gain curves: a frame with every harmonic 1..60 at the same amplitude and random phases goes through the morph; the output harmonics' level gives the filter curve.

    python calibration/vital_morph3_probe.py [--types 7 8] [-o work/morph3.json]
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
import vital_morph2_probe as M2                                     # noqa: E402
import vital_render as VR                                           # noqa: E402
import vital_route_probe as RP                                      # noqa: E402

SR, NOTE, F0 = M2.SR, M2.NOTE, M2.F0
NH = 60


def flat_frame(amp: float = 0.012, seed: int = 1, nh: int = NH, tilt: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    ph = rng.uniform(0, 2 * np.pi, nh)
    a = amp / np.arange(1, nh + 1) ** tilt
    x = np.arange(2048) / 2048.0
    w = sum(a[h] * np.sin(2 * np.pi * (h + 1) * x + ph[h]) for h in range(nh))
    return w.astype(np.float32), a, ph


def preset(frame: np.ndarray, typ: int, amt: float) -> bytes:
    b64 = base64.b64encode(frame.astype("<f4").tobytes()).decode()
    d = RP.base_preset()
    s = d["settings"]
    kf = s["wavetables"][0]["groups"][0]["components"][0]["keyframes"]
    kf[:] = [{"position": 0, "wave_data": b64}, {"position": 255, "wave_data": b64}]
    s["osc_1_transpose"], s["osc_1_tune"] = 0.0, 0.0
    s["osc_1_wave_frame"], s["osc_1_level"], s["osc_1_random_phase"] = 0.0, 1.0, 0.0
    s["osc_1_distortion_type"], s["osc_1_spectral_morph_type"] = 0.0, float(typ)
    s["osc_1_spectral_morph_amount"], s["osc_1_spectral_morph_spread"] = float(amt), 0.0
    s["filter_1_on"] = 0.0
    return json.dumps(d).encode()


def run(host, frame, typ, amt):
    x = np.asarray(host.render(preset(frame, typ, amt), ((NOTE, 100, 0.0, 1.2),), 1.6))[0]
    return M2.harmonics(x)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--types", type=int, nargs="*", default=[7, 8])
    ap.add_argument("--amounts", type=float, nargs="*", default=[round(0.05 * i, 2) for i in range(0, 21)])
    ap.add_argument("-o", "--out", default=str(ROOT / "work/morph3.json"))
    a = ap.parse_args()
    host = VR.VitalHost()
    fr, amps, ph = flat_frame()
    base = run(host, fr, 0, 0.0)
    res = {"base": base.tolist()}
    for t in a.types:
        res[t] = {}
        for amt in a.amounts:
            g = run(host, fr, t, amt)
            res[t][amt] = (g / np.maximum(base, 1e-12)).tolist()
        print("type", t, "done", flush=True)
    json.dump(res, open(a.out, "w"))


if __name__ == "__main__":
    main()
