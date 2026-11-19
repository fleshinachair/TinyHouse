#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Measure Vital's LFO rate laws through the real plugin: how lfo_N_sync / lfo_N_tempo / lfo_N_frequency become a period.

    python calibration/vital_lfo_probe.py [-o calibration/vital_lfo_laws.json]

An LFO is routed to oscillator 1's level and a held tone is played; the period of the tone's loudness is found by autocorrelation.
Played at DawDreamer's 120 BPM. Writes {sync: {tempo index: period in seconds}} and {frequency (log2 Hz): period}.
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

SR = 44100
SRC = Path.home() / "OneDrive/Documents/Vital/Factory/Presets/Touch Tone.vital"
ALL_FX = ("reverb", "delay", "chorus", "flanger", "phaser", "distortion", "compressor", "eq", "filter_fx")


def base_preset() -> dict:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    s = d["settings"]
    for fx in ALL_FX:
        s[f"{fx}_on"] = 0.0
    s["filter_1_on"] = s["filter_2_on"] = 0.0
    s["sample_on"] = 0.0
    s["osc_2_on"] = s["osc_3_on"] = 0.0
    s["osc_1_on"] = 1.0
    s["osc_1_level"] = 0.7
    s["osc_1_unison_voices"] = 1.0
    s["env_1_attack"], s["env_1_decay"], s["env_1_sustain"], s["env_1_release"] = 0.001 ** 0.25, 1.0, 1.0, 0.1
    s["modulations"] = [{"destination": "", "source": ""} for _ in s["modulations"]]
    for i in range(1, 65):
        s[f"modulation_{i}_amount"] = 0.0
        s[f"modulation_{i}_bipolar"] = 0.0
        s[f"modulation_{i}_bypass"] = 0.0
        s[f"modulation_{i}_power"] = 0.0
        s[f"modulation_{i}_stereo"] = 0.0
    s["modulations"][0] = {"destination": "osc_1_level", "source": "lfo_1"}
    s["modulation_1_amount"] = 1.0
    return d


def period_s(x: np.ndarray, t0: float = 0.3) -> float | None:
    m = np.abs(x).max(axis=0)[int(t0 * SR):]
    k = int(0.01 * SR)
    e = np.convolve(m, np.ones(k) / k, mode="same")[::40]                # 1 kHz envelope
    e = e - e.mean()
    if np.abs(e).max() < 1e-4:
        return None
    ac = np.correlate(e, e, "full")[len(e) - 1:]
    ac /= ac[0]
    z = int(np.argmax(ac < 0))                                           # past the first zero crossing, so the slow decay of the autocorrelation is not read as the period
    if z == 0 or z >= len(ac) // 2:
        return None
    i = z + int(np.argmax(ac[z:len(ac) * 2 // 3]))
    if 0 < i < len(ac) - 1:                                              # parabolic refinement
        a, b, c = ac[i - 1], ac[i], ac[i + 1]
        i = i + 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else i
    return float(i) * 40.0 / SR                                          # the envelope is every 40th sample


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "calibration/vital_lfo_laws.json")
    a = ap.parse_args()
    host = VR.VitalHost()
    out = {"synced_period_s": {}, "free_period_s": {}}
    for sync in (1, 2, 3):
        row = {}
        for tempo in range(4, 11):
            d = base_preset()
            d["settings"].update(lfo_1_sync=float(sync), lfo_1_tempo=float(tempo), lfo_1_sync_type=0.0)
            row[tempo] = period_s(np.asarray(host.render(json.dumps(d).encode(), ((60, 100, 0.0, 24.0),), 24.0)))
            print(f"lfo sync {sync} tempo {tempo}: period {row[tempo]}", flush=True)
        out["synced_period_s"][sync] = row
    for f in (-1.0, 0.0, 1.0, 2.0):
        d = base_preset()
        d["settings"].update(lfo_1_sync=0.0, lfo_1_frequency=f, lfo_1_sync_type=0.0)
        out["free_period_s"][f] = period_s(np.asarray(host.render(json.dumps(d).encode(), ((60, 100, 0.0, 24.0),), 24.0)))
        print(f"lfo freq {f}: period {out['free_period_s'][f]} (1/2^f = {1 / 2 ** f:.3f})", flush=True)
    a.out.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("written", a.out)


if __name__ == "__main__":
    main()
