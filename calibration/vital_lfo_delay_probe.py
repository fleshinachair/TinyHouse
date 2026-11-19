#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Vital's LFO Delay, Fade In, Phase and Smooth, plugin against engine: LFO 1 moves oscillator 1's level on a held tone, and the loudness over time is compared.

    python calibration/vital_lfo_delay_probe.py

Each case prints the largest difference in dB between the two loudness curves (20 ms frames, 3 s) and, with --plot-data, the curves themselves.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "calibration"))
import vital_lfo_probe as LP                                        # noqa: E402
import vital_porta_probe as PP                                      # noqa: E402
import vital_render as VR                                           # noqa: E402

SR = 44100


def curve(x: np.ndarray) -> np.ndarray:
    n = int(0.02 * SR)
    k = len(x) // n
    return 20 * np.log10(np.sqrt((x[:k * n].reshape(k, n) ** 2).mean(axis=1)) + 1e-9)


def case(host, **kw) -> tuple[np.ndarray, np.ndarray]:
    d = LP.base_preset()
    d["synth_version"] = "1.5.5"                                    # the base preset is a 0.6.1 one: Vital's loader turns the LFO smoothing off for presets that old
    s = d["settings"]
    s["osc_1_midi_track"] = 1.0
    s["osc_1_level"] = 0.5                                          # the LFO swings the level 0.1 .. 0.9 around it, so the tone never goes silent
    s["modulation_1_bipolar"] = 0.0
    s["modulation_1_amount"] = 0.4
    s["lfo_1_sync"] = 0.0
    s["lfo_1_frequency"] = kw.pop("freq", 0.0)
    s["lfo_1_sync_type"] = float(kw.pop("sync_type", 0))
    s.update({f"lfo_1_{k}": float(v) for k, v in kw.items()})
    s["env_1_attack"], s["env_1_release"] = 0.0, 0.05
    v = np.asarray(host.render(json.dumps(d).encode(), ((60, 100, 0.0, 3.0),), 3.4))[0]
    e = PP.engine_render(d, [(0.0, 3.0, 60)], 3.4)
    return curve(v), curve(e)


def main() -> None:
    host = VR.VitalHost()
    cases = {
        "delay 0.5 s": dict(delay_time=0.5),
        "delay 1.2 s": dict(delay_time=1.2),
        "fade 1.0 s (smooth off)": dict(fade_time=1.0, smooth_mode=0.0),
        "fade 2.0 s (smooth off)": dict(fade_time=2.0, smooth_mode=0.0),
        "fade 1.0 s (smooth on: ignored)": dict(fade_time=1.0, smooth_mode=1.0, smooth_time=-7.5),
        "delay 0.4 + fade 1.0": dict(delay_time=0.4, fade_time=1.0, smooth_mode=0.0),
        "phase 0.25": dict(phase=0.25),
        "phase 0.5 delay 0.5": dict(phase=0.5, delay_time=0.5),
        "smooth -2 (0.07 s)": dict(smooth_mode=1.0, smooth_time=-2.0),
        "smooth 0 (0.29 s)": dict(smooth_mode=1.0, smooth_time=0.0),
        "smooth -2, freq 2": dict(smooth_mode=1.0, smooth_time=-2.0, freq=2.0),
        "smooth 0 from a high start (phase .5)": dict(smooth_mode=1.0, smooth_time=0.0, phase=0.5),
        "smooth -2 + delay 0.5 (holds at 0)": dict(smooth_mode=1.0, smooth_time=-2.0, delay_time=0.5, phase=0.5),
        "envelope sync, delay 0.3": dict(sync_type=1, delay_time=0.3, freq=-1.0),
    }
    for name, kw in cases.items():
        v, e = case(host, **kw)
        k = int(2.9 / 0.02)                                         # the held part of the note; the engine's overall level is not matched, so the median offset is taken out
        dd = (v[5:k] - e[5:k])
        dd = dd - np.median(dd)
        print(f"{name:34s} max diff {np.abs(dd).max():5.1f} dB   mean {np.abs(dd).mean():4.1f} dB", flush=True)
        if "--curves" in sys.argv:
            print("   vital ", np.round(v[:k:10], 0).astype(int).tolist()); print("   engine", np.round(e[:k:10], 0).astype(int).tolist())


if __name__ == "__main__":
    main()
