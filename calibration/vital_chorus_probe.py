#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Does the engine's Vital chorus (chor_vital = 1) do what Vital's chorus does? The same short pluck goes through each chorus setting in Vital and in the engine; what is compared is the
chorus's transfer (the output spectrum over the same note's dry spectrum, 100 Hz - 10 kHz, smoothed over 1/3 octave) and the level of the tail, in dB.

    python calibration/vital_chorus_probe.py [--only NAME]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "converter"))
sys.path.insert(0, str(ROOT / "calibration"))
import vital_fx_probe as FP                                         # noqa: E402
import vital_render as VR                                           # noqa: E402

SR = 44100


def engine_pots(delay1=-5.64386, delay2=-5.64386, fb=0.0, voices=1, depth=0.0, rate_hz=0.0156, mix=1.0, cutoff=8.0, spread=1.0) -> dict:
    lg = lambda v: int(round(127 * (v + 10.0) / 4.35614))
    return {"chor_on": 1, "chor_vital": 1, "chor_mix": int(round(127 * mix)), "chor_d1": lg(delay1), "chor_d2": lg(delay2), "chor_fb": int(round(127 * (fb / 0.95 + 1) / 2)),
            "chor_voices": voices, "chor_depth": int(round(127 * depth)), "chor_rate": int(round(127 * (rate_hz / 20.0) ** (1 / 3))),
            "chor_vcut": int(round(127 * (cutoff - 8.0) / 128.0)), "chor_vspread": int(round(127 * spread))}


def vital_settings(delay1=-5.64386, delay2=-5.64386, fb=0.0, voices=1, depth=0.0, rate_hz=0.0156, mix=1.0, cutoff=8.0, spread=1.0) -> dict:
    return {"chorus_on": 1.0, "chorus_dry_wet": mix, "chorus_delay_1": delay1, "chorus_delay_2": delay2, "chorus_feedback": fb, "chorus_voices": float(voices), "chorus_mod_depth": depth,
            "chorus_frequency": math.log2(rate_hz), "chorus_sync": 0.0, "chorus_cutoff": cutoff, "chorus_spread": spread}


def spec(x: np.ndarray, t1: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    seg = x.mean(axis=0)[:int(t1 * SR)]
    m = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    return np.fft.rfftfreq(len(seg), 1 / SR), m


def third_octave(f: np.ndarray, p: np.ndarray, lo=100.0, hi=10000.0) -> tuple[np.ndarray, np.ndarray]:
    cs = 2.0 ** (np.arange(math.log2(lo), math.log2(hi), 1 / 3))
    return cs, np.array([10 * np.log10(p[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))].sum() + 1e-18) for c in cs])


CASES = {
    "plain 1v d=-8": dict(delay1=-8.0, delay2=-8.0),
    "2 delays -9/-7": dict(delay1=-9.0, delay2=-7.0),
    "4 voices": dict(delay1=-9.0, delay2=-7.0, voices=4),
    "fb +0.8": dict(delay1=-8.0, delay2=-8.0, fb=0.8),
    "fb -0.95": dict(delay1=-5.64386, delay2=-5.64386, fb=-0.95),
    "fb -0.95 d=-8.8": dict(delay1=-8.8, delay2=-8.8, fb=-0.95),
    "depth 0.5": dict(delay1=-9.0, delay2=-7.0, depth=0.5),
    "depth 1": dict(delay1=-9.0, delay2=-7.0, depth=1.0, rate_hz=1.0),
    "filter 60/0.3": dict(delay1=-8.0, delay2=-8.0, cutoff=60.0, spread=0.3),
    "filter 90/0.1 fb .5": dict(delay1=-8.0, delay2=-8.0, cutoff=90.0, spread=0.1, fb=0.5),
    "mix 0.3": dict(delay1=-9.0, delay2=-7.0, mix=0.3),
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only")
    a = ap.parse_args()
    host = VR.VitalHost()
    base = FP.pluck_preset()
    base["settings"]["osc_1_random_phase"] = base["settings"]["osc_2_random_phase"] = 0.0
    vdry = FP.vital_render(host, base, 1.2)
    edry = FP.tablor_render({}, 1.2)
    _, vd = third_octave(*spec(vdry)); cs, ed = third_octave(*spec(edry))
    worst = 0.0
    for name, kw in CASES.items():
        if a.only and a.only not in name:
            continue
        d = json.loads(json.dumps(base)); d["settings"].update(vital_settings(**kw))
        vx = FP.vital_render(host, d, 1.2)
        ex = FP.tablor_render(engine_pots(**kw), 1.2)
        _, vw = third_octave(*spec(vx)); _, ew = third_octave(*spec(ex))
        vt, et = vw - vd, ew - ed
        diff = et - vt
        tail = lambda x: 10 * math.log10(float(np.mean(x[:, int(0.3 * SR):int(0.8 * SR)] ** 2)) + 1e-18)
        print(f"{name:22s} transfer diff mean {np.mean(np.abs(diff)):5.2f} dB (bias {np.mean(diff):+5.2f}, worst {np.max(np.abs(diff)):5.1f})   tail vital {tail(vx):6.1f} engine {tail(ex):6.1f} dry {tail(vdry):6.1f}/{tail(edry):6.1f}", flush=True)
        worst = max(worst, float(np.mean(np.abs(diff))))
    print("worst mean diff", round(worst, 2))


if __name__ == "__main__":
    main()
