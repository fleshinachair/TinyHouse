#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Vital's amplitude envelope against the engine's, through the converter: a sine, attack 0, then decay, sustain and release at the settings below.

    python calibration/vital_env_probe.py

Vital applies its amplitude envelope squared (measured: sustain 0.5 plays at 0.25), and its decay and release run a power curve that ends exactly at the set time.  For each case the amplitude
over time is printed in dB below the peak, for both, and the largest difference while Vital is above -40 dB.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "calibration"))
sys.path.insert(0, str(ROOT / "converter"))
import vital_render as VR                                           # noqa: E402
import vital_rm_probe as RM                                         # noqa: E402
import vital_import as VI                                           # noqa: E402

SR = 44100
CASES = [dict(decay=0.6, sustain=0.5, release=0.7, power=-2.0), dict(decay=0.9, sustain=0.0, release=0.8, power=-2.0),
         dict(decay=0.5, sustain=0.8, release=1.0, power=-2.0), dict(decay=0.7, sustain=0.3, release=0.9, power=-4.0),
         dict(decay=0.7, sustain=0.3, release=0.9, power=0.0)]
HOLD, DUR = 2.5, 5.0


def curve(x: np.ndarray, ts) -> list[float]:
    w = int(0.05 * SR)
    return [20 * np.log10(np.sqrt((x[int(t * SR):int(t * SR) + w] ** 2).mean()) + 1e-9) for t in ts]


def main() -> None:
    import subprocess
    host = VR.VitalHost()
    root = Path(tempfile.mkdtemp()); tmp = Path(tempfile.mkdtemp())
    ts = [0.05, 0.2, 0.4, 0.7, 1.0, 1.5, 2.2, 2.6, 2.8, 3.0, 3.3, 3.8]
    print("times", ts)
    for c in CASES:
        d = json.loads(RM.preset(0.0, 0, 24.0, 0.0)); s = d["settings"]
        s["osc_2_on"] = 0.0
        s["env_1_attack"], s["env_1_decay"], s["env_1_sustain"], s["env_1_release"] = 0.0, c["decay"], c["sustain"], c["release"]
        s["env_1_decay_power"] = s["env_1_release_power"] = c["power"]
        v = np.asarray(host.render(json.dumps(d).encode(), ((60, 100, 0.0, HOLD),), DUR))[0]
        m = VI.map_vital(d, "e", root / "move/data/UserData/UserLibrary/Wavetables", "Probe")
        m["params"]["volume"] = 20
        (tmp / "b.txt").write_text(VI.blob(m), encoding="utf-8", newline="\n")
        subprocess.run([str(ROOT / "calibration/tablor_cli.exe"), str(root / "move"), str(tmp / "b.txt"), str(tmp / "o.raw"), str(DUR), str(HOLD), "100", "60", "1"], check=True, capture_output=True)
        e = np.frombuffer((tmp / "o.raw").read_bytes(), "<f4").reshape(-1, 2).T[0]
        cv, ce = np.array(curve(v, ts)), np.array(curve(e, ts))
        cv, ce = cv - cv.max(), ce - ce.max()
        ok = cv > -40
        print(f"decay {c['decay']} sustain {c['sustain']} release {c['release']} power {c['power']}:  worst |diff| {np.abs(cv - ce)[ok].max():.1f} dB")
        print("   Vital ", np.round(cv, 1).tolist())
        print("   engine", np.round(ce, 1).tolist())


if __name__ == "__main__":
    main()
