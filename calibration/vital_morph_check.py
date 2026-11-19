#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Check converter/vital_morph.py against the plugin: a random-phase frame (1/h amplitudes, harmonics 1..40) through a morph type at several amounts.

    python calibration/vital_morph_check.py --types 2 3 4 5 6 7 8 9 10

The plugin's output is read as complex harmonics (a DFT at k * F0 over the held part of the note); the unmorphed render gives the plugin's own gain and timing, which are
fitted (a constant and a delay) and applied to the converter's predicted harmonics, so magnitude and phase are both compared. Prints the relative L2 error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "converter"))
sys.path.insert(0, str(ROOT / "calibration"))
import vital_morph as VM                                            # noqa: E402
import vital_morph2_probe as M2                                     # noqa: E402
import vital_morph3_probe as M3                                     # noqa: E402
import vital_render as VR                                           # noqa: E402

SR, F0 = M2.SR, M2.F0
NK = 60


def complex_harmonics(x: np.ndarray) -> np.ndarray:
    t = np.arange(32768) / SR
    seg = x[int(0.2 * SR):int(0.2 * SR) + 32768] * np.hanning(32768)
    return np.array([(seg * np.exp(-2j * np.pi * k * F0 * t)).sum() for k in range(1, NK + 1)])


def render(host, frame, typ, amt):
    return complex_harmonics(np.asarray(host.render(M3.preset(frame, typ, amt), ((M2.NOTE, 100, 0.0, 1.2),), 1.6))[0])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--types", type=int, nargs="*", default=[2, 3, 4, 5, 6, 7, 8, 9, 10])
    ap.add_argument("--amounts", type=float, nargs="*", default=[0.15, 0.3, 0.7, 0.9])
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--sign", type=float, default=None, help="phase-disperse rotation sign to test")
    a = ap.parse_args()
    if a.sign is not None:
        VM.PHASE_SIGN = a.sign
    host = VR.VitalHost()
    fr, amps, ph = M3.flat_frame(amp=0.2, seed=a.seed, nh=40, tilt=1.0)
    fr64 = fr.astype(np.float64)
    spin = np.fft.rfft(fr64)[1:NK + 1]
    base = render(host, fr, 0, 0.0)
    k = np.arange(1, 41)
    z = base[:40] / spin[:40]
    G = np.abs(z).mean()
    slope, c0 = np.polyfit(k, np.unwrap(np.angle(z)), 1)
    model = G * np.exp(1j * (c0 + slope * np.arange(1, NK + 1)))
    print(f"plugin gain {G:.3f}, delay {slope / (2 * np.pi * F0) * 1e6:.1f} us (phase fit residual {np.abs(np.angle(z / (G * np.exp(1j * (c0 + slope * k))))).max():.3f} rad)")
    for typ in a.types:
        for amt in a.amounts:
            got = render(host, fr, typ, amt)
            out = VM.morph_table(fr64[None], typ, amt)
            pred_sp = np.fft.rfft(out[0])[1:NK + 1] if out is not None else spin
            pred = pred_sp * model
            rel = np.sqrt((np.abs(got - pred) ** 2).sum() / max((np.abs(got) ** 2).sum(), 1e-12))
            mag = np.sqrt(((np.abs(got) - np.abs(pred)) ** 2).sum() / max((np.abs(got) ** 2).sum(), 1e-12))
            print(f"type {typ:2d} a {amt:4.2f}: complex rel L2 {rel:6.3f}   magnitude-only {mag:6.3f}")


if __name__ == "__main__":
    main()
