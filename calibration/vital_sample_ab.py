#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Level of the converted Vital sample oscillator against the plugin's, on a 100 Hz sine sample (see vital_sample_probe.py).

    python calibration/vital_sample_ab.py

Prints, per Vital level, the dB the engine is off by with VITAL_SMP_SCALE as it stands in converter/vital_import.py.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "converter"))
sys.path.insert(0, str(ROOT / "calibration"))
import vital_ab as AB                                               # noqa: E402
import vital_import as VI                                           # noqa: E402
import vital_render as VR                                           # noqa: E402
import vital_sample_probe as SP                                     # noqa: E402


def main() -> None:
    host = VR.VitalHost()
    sine = 0.5 * np.sin(2 * np.pi * np.arange(44100) / 441.0)
    out = Path(tempfile.mkdtemp(prefix="vital_smp_ab_"))
    for fixed_vol in (None,):
        for lv in (1.0, 0.7071, 0.5, 0.25):
            pre = SP.sample_preset(sine, level=lv)
            d = json.loads(pre)
            d["settings"]["volume"] = 5800.0 if False else d["settings"].get("volume", 0)
            m = VI.map_vital(d, "smp_ab", out / "move/data/UserData/UserLibrary/Wavetables")
            v = np.asarray(host.render(pre, ((60, 100, 0.0, 1.5),), 2.0), dtype=np.float32)
            AB.DUR, AB.HOLD = 2.0, 1.5
            t = AB.tablor_render(out / "move", VI.blob(m), 60, 100, out)
            rv, rt = AB.rms(v, 0.3, 1.4), AB.rms(t, 0.3, 1.4)
            print(f"level {lv:6.4f}: vital {AB.db(rv):7.2f} dB  engine {AB.db(rt):7.2f} dB  engine - vital {AB.db(rt) - AB.db(rv):+.2f} dB  (smp_level pot {m['params']['smp_level']}, volume pot {m['params']['volume']})")




def real_samples() -> None:
    """The sample oscillators of the local presets, each played alone (everything else off) through Vital and through the engine."""
    import base64
    import glob
    import os
    host = VR.VitalHost()
    out = Path(tempfile.mkdtemp(prefix="vital_smp_real_"))
    root = os.path.expanduser("~/OneDrive/Documents/Vital")
    for f in sorted(glob.glob(root + "/**/*.vital", recursive=True)):
        s0 = json.load(open(f, encoding="utf-8"))["settings"]
        if not s0.get("sample_on") or s0["sample_level"] < 0.05:
            continue
        sm = s0["sample"]
        pcm = np.frombuffer(base64.b64decode(sm["samples"]), "<i2").astype(np.float32) / 32768.0
        for note in (60, 48):
            pre = SP.sample_preset(pcm, level=0.7071, loop=s0["sample_loop"], keytrack=s0["sample_keytrack"], bounce=s0["sample_bounce"],
                                   transpose=s0["sample_transpose"], tune=s0["sample_tune"])
            d = json.loads(pre)
            m = VI.map_vital(d, "smp_real", out / "move/data/UserData/UserLibrary/Wavetables")
            v = np.asarray(host.render(pre, ((note, 100, 0.0, 1.5),), 2.0), dtype=np.float32)
            AB.DUR, AB.HOLD = 2.0, 1.5
            t = AB.tablor_render(out / "move", VI.blob(m), note, 100, out)
            n = int(1.0 * 44100)
            a, b = v[0, :n], t[0, :n]
            best = max(np.corrcoef(a[:n - 200], np.roll(b, k)[:n - 200])[0, 1] for k in range(0, 200, 4))
            print(f"{os.path.basename(f)[:28]:28s} n{note}: loop {int(s0['sample_loop'])} kt {int(s0['sample_keytrack'])} tr {s0['sample_transpose']:+.0f}: "
                  f"level {AB.db(AB.rms(t, 0.05, 1.0)) - AB.db(AB.rms(v, 0.05, 1.0)):+.2f} dB  centroid {AB.centroid(v, 0.05, 1.0):.0f} vs {AB.centroid(t, 0.05, 1.0):.0f}  corr {best:.3f}")


if __name__ == "__main__":
    (real_samples if "--real" in sys.argv else main)()
