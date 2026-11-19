#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Measure Vital's multiband compressor through the real plugin: the static transfer curve (output level against input level) of a sine in each band.

    python calibration/vital_comp_probe.py [-o calibration/vital_comp_laws.json] [--set compressor_low_gain=0 ...]

A held sine (the oscillator on a pure sine frame, every other effect off) is rendered with the compressor off and on at several input levels; the
gain is the difference in dB. Bands: low (note 36, 65 Hz), mid (note 72, 523 Hz), high (note 108, 8.4 kHz). Settings given with --set replace the preset's values.
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


def rms_db(x: np.ndarray) -> float:
    seg = x[:, int(0.5 * SR):int(1.4 * SR)]
    return 20 * np.log10(max(float(np.sqrt(np.mean(seg ** 2))), 1e-9))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "calibration/vital_comp_laws.json")
    ap.add_argument("--set", nargs="*", default=[])
    ap.add_argument("--levels", type=float, nargs="*", default=[0.02, 0.05, 0.1, 0.2, 0.35, 0.5, 0.7, 1.0])
    a = ap.parse_args()
    host = VR.VitalHost()
    base = RP.base_preset()
    s = base["settings"]
    s["filter_1_on"] = 0.0
    s["osc_1_wave_frame"] = 255.0                                    # the sine end of the probe's saw-to-sine table
    s["osc_1_random_phase"] = 0.0
    s["osc_1_midi_track"], s["osc_1_transpose"] = 1.0, 0.0           # the probe preset's oscillator does not follow the keyboard: without this every note is middle C
    for kv in a.set:
        k, v = kv.split("=")
        s[k] = float(v)
    out: dict = {}
    for band, note in (("low", 36), ("mid", 72), ("high", 108)):
        rows = []
        for lv in a.levels:
            d = json.loads(json.dumps(base)); d["settings"]["osc_1_level"] = lv
            d["settings"]["compressor_on"] = 0.0
            ref = rms_db(np.asarray(host.render(json.dumps(d).encode(), ((note, 100, 0.0, 1.5),), 2.0), dtype=np.float32))
            d["settings"]["compressor_on"] = 1.0
            got = rms_db(np.asarray(host.render(json.dumps(d).encode(), ((note, 100, 0.0, 1.5),), 2.0), dtype=np.float32))
            rows.append((round(ref, 2), round(got - ref, 2)))
            print(f"{band} note {note} level {lv}: in {ref:.1f} dB, gain {got - ref:+.1f} dB", flush=True)
        out[band] = rows
    out["settings"] = {k: v for k, v in s.items() if k.startswith("compressor_")}
    a.out.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("written", a.out)
    print("exit")


if __name__ == "__main__":
    main()
