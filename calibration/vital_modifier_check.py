#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Check converter/vital_wtmod.py against the plugin: a random-phase frame through each wavetable modifier, played at table position 0 (the modifier's first keyframe)
and halfway to a second keyframe (the tween).

    python calibration/vital_modifier_check.py [--sign -1]

The plugin's output is read as complex harmonics; the unmodified render gives the plugin's gain and delay, which are fitted and applied to the converter's prediction,
so magnitude and phase are both compared. Prints the relative complex L2 error and the magnitude-only error.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "converter"))
sys.path.insert(0, str(ROOT / "calibration"))
import vital_morph2_probe as M2                                     # noqa: E402
import vital_morph3_probe as M3                                     # noqa: E402
import vital_render as VR                                           # noqa: E402
import vital_wtmod as WM                                            # noqa: E402

SR, F0, NK = M2.SR, M2.F0, 60


def complex_harmonics(x: np.ndarray) -> np.ndarray:
    t = np.arange(32768) / SR
    seg = x[int(0.2 * SR):int(0.2 * SR) + 32768] * np.hanning(32768)
    return np.array([(seg * np.exp(-2j * np.pi * k * F0 * t)).sum() for k in range(1, NK + 1)])


def preset(frame: np.ndarray, comp: dict | None, pos: float) -> bytes:
    b64 = base64.b64encode(frame.astype("<f4").tobytes()).decode()
    d = M3.RP.base_preset()
    s = d["settings"]
    comps = s["wavetables"][0]["groups"][0]["components"]
    comps[0]["keyframes"][:] = [{"position": 0, "wave_data": b64}, {"position": 255, "wave_data": b64}]
    del comps[1:]
    if comp is not None:
        comps.append(comp)
    s["osc_1_transpose"], s["osc_1_tune"] = 0.0, 0.0
    s["osc_1_wave_frame"], s["osc_1_level"], s["osc_1_random_phase"] = float(pos), 1.0, 0.0
    s["osc_1_distortion_type"], s["osc_1_spectral_morph_type"] = 0.0, 0.0
    s["filter_1_on"] = 0.0
    return json.dumps(d).encode()


def cases() -> list[tuple[str, dict]]:
    mod = lambda typ, kfs, **kw: {"type": typ, "interpolation_style": 1, "keyframes": kfs, **kw}
    out = []
    for b0, b1 in ((1.0, 2.0), (2.5, 0.5)):
        out.append((f"fold {b0}->{b1}", mod("Wave Folder", [{"position": 0, "fold_boost": b0}, {"position": 255, "fold_boost": b1}])))
    for hp, vp, ha, va in ((0.0, -10.0, False, False), (6.0, 0.0, False, False), (0.0, 8.0, False, True), (-5.0, 3.0, True, False)):
        out.append((f"warp h{hp} v{vp} asym {ha}/{va}", mod("Wave Warp", [{"position": 0, "horizontal_power": hp, "vertical_power": vp}, {"position": 255, "horizontal_power": hp * 0.4, "vertical_power": vp * 0.4}],
                                                           horizontal_asymmetric=ha, vertical_asymmetric=va)))
    for st in range(4):
        out.append((f"filter style {st}", mod("Frequency Filter", [{"position": 0, "cutoff": 3.0, "shape": 0.5}, {"position": 255, "cutoff": 5.0, "shape": 0.9}], style=st, normalize=True)))
    out.append(("filter lp no-normalize", mod("Frequency Filter", [{"position": 0, "cutoff": 3.0, "shape": 0.5}], style=0, normalize=False)))
    for st in range(5):
        out.append((f"phase style {st}", mod("Phase Shift", [{"position": 0, "phase": -0.88, "mix": 1.0}, {"position": 255, "phase": 0.6, "mix": 0.5}], style=st)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sign", type=float, default=None)
    ap.add_argument("--only", nargs="*", default=None)
    a = ap.parse_args()
    if a.sign is not None:
        WM.PHASE_SIGN = a.sign
    host = VR.VitalHost()
    fr, _amps, _ph = M3.flat_frame(amp=0.35, seed=3, nh=40, tilt=1.0)
    fr64 = fr.astype(np.float64)
    spin = np.fft.rfft(fr64)[1:NK + 1]
    print(f"frame peak {np.abs(fr64).max():.2f}")
    base = complex_harmonics(np.asarray(host.render(preset(fr, None, 0), ((M2.NOTE, 100, 0.0, 1.2),), 1.6))[0])
    k = np.arange(1, 41)
    z = base[:40] / spin[:40]
    G = np.abs(z).mean()
    slope, c0 = np.polyfit(k, np.unwrap(np.angle(z)), 1)
    model = G * np.exp(1j * (c0 + slope * np.arange(1, NK + 1)))
    print(f"plugin gain {G:.3f}")
    for name, comp in cases():
        if a.only and not any(o in name for o in a.only):
            continue
        for pos in (0, 127.5):
            got = complex_harmonics(np.asarray(host.render(preset(fr, comp, pos), ((M2.NOTE, 100, 0.0, 1.2),), 1.6))[0])
            pred = np.fft.rfft(WM.apply_modifier(comp, fr64[None], np.array([float(pos)]))[0])[1:NK + 1] * model
            rel = np.sqrt((np.abs(got - pred) ** 2).sum() / max((np.abs(got) ** 2).sum(), 1e-12))
            mag = np.sqrt(((np.abs(got) - np.abs(pred)) ** 2).sum() / max((np.abs(got) ** 2).sum(), 1e-12))
            print(f"{name:34s} pos {pos:5.1f}: complex rel L2 {rel:6.3f}   magnitude-only {mag:6.3f}", flush=True)


if __name__ == "__main__":
    main()
