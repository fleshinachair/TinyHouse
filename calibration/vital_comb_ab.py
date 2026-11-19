#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Vital's comb filter, plugin against the engine's Vital comb law (comb.h processVital), per harmonic of a held saw.

    python calibration/vital_comb_ab.py [--cli path]

For each setting the filter-on render is divided by the filter-off render of the same preset in each renderer, so the level of the oscillator and the voice chain cancels, and the two
responses are compared harmonic by harmonic (dB).  The last column is the rms difference over the harmonics where both are above -50 dB of the unfiltered saw.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "calibration"))
sys.path.insert(0, str(ROOT / "converter"))
import vital_render as VR                                           # noqa: E402
import vital_route_probe as RP                                      # noqa: E402
import vital_import as VI                                           # noqa: E402

SR, NOTE = 44100, 36
F0 = 440.0 * 2 ** ((NOTE - 69) / 12)


def harm(x: np.ndarray, f0: float) -> np.ndarray:
    seg = x[int(0.4 * SR):int(1.3 * SR)]
    mag = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    fr = np.fft.rfftfreq(len(seg), 1 / SR)
    ks = np.arange(1, int(9000 / f0))
    return np.array([mag[max(int(round(k * f0 / fr[1])) - 2, 0):int(round(k * f0 / fr[1])) + 3].max() for k in ks])


def preset(**kw) -> dict:
    d = RP.base_preset()
    s = d["settings"]
    s["osc_1_wave_frame"], s["osc_1_random_phase"] = 0.0, 0.0
    s["osc_1_midi_track"], s["osc_1_transpose"] = 1.0, 0.0
    s["osc_1_level"] = 0.1
    s["filter_1_keytrack"] = 0.0
    s.update(kw)
    return d


def engine_render(d: dict, root: Path, cli: Path) -> np.ndarray:
    m = VI.map_vital(json.loads(json.dumps(d)), "comb", root / "move/data/UserData/UserLibrary/Wavetables", "Probe")
    m["params"]["volume"] = 32
    tmp = Path(tempfile.mkdtemp())
    (tmp / "b.txt").write_text(VI.blob(m), encoding="utf-8", newline="\n")
    subprocess.run([str(cli), str(root / "move"), str(tmp / "b.txt"), str(tmp / "o.raw"), "1.6", "1.5", "100", str(NOTE), "1"], check=True)
    return np.frombuffer((tmp / "o.raw").read_bytes(), "<f4").reshape(-1, 2).T.copy().mean(axis=0)


CASES = [
    ("comb res .8 cut 60", dict(filter_1_style=0.0, filter_1_cutoff=60.0, filter_1_resonance=0.8, filter_1_blend=0.0)),
    ("comb res .2 cut 60", dict(filter_1_style=0.0, filter_1_cutoff=60.0, filter_1_resonance=0.2, filter_1_blend=0.0)),
    ("comb res .8 blend 1", dict(filter_1_style=0.0, filter_1_cutoff=60.0, filter_1_resonance=0.8, filter_1_blend=1.0, filter_1_blend_transpose=0.0)),
    ("pos flange res .6", dict(filter_1_style=1.0, filter_1_cutoff=60.0, filter_1_resonance=0.6, filter_1_blend=0.0)),
    ("neg flange res .6", dict(filter_1_style=2.0, filter_1_cutoff=60.0, filter_1_resonance=0.6, filter_1_blend=0.0)),
    ("band comb res .8 blend .5", dict(filter_1_style=3.0, filter_1_cutoff=60.0, filter_1_resonance=0.8, filter_1_blend=0.5, filter_1_blend_transpose=12.0)),
    ("band comb Feeder", dict(filter_1_style=3.0, filter_1_cutoff=60.0, filter_1_resonance=0.9, filter_1_blend=1.69, filter_1_blend_transpose=41.9)),
    ("band pos flange blend .5", dict(filter_1_style=4.0, filter_1_cutoff=60.0, filter_1_resonance=0.6, filter_1_blend=0.5, filter_1_blend_transpose=0.0)),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", default=str(ROOT / "calibration/tablor_cli.exe"))
    a = ap.parse_args()
    host = VR.VitalHost()
    root = Path(tempfile.mkdtemp())

    def vrender(d: dict) -> np.ndarray:
        return np.asarray(host.render(json.dumps(d).encode(), ((NOTE, 100, 0.0, 1.5),), 1.6), dtype=np.float32).mean(axis=0)

    v0 = harm(vrender(preset(filter_1_on=0.0)), F0)
    # the engine's pitch may differ from the plugin's (the probe oscillator): find its fundamental from the unfiltered render
    e0w = engine_render(preset(filter_1_on=0.0), root, Path(a.cli))
    seg = e0w[int(0.4 * SR):int(1.3 * SR)] * np.hanning(int(0.9 * SR))
    fe = np.fft.rfftfreq(len(seg), 1 / SR)
    ef0 = fe[np.argmax(np.abs(np.fft.rfft(seg))[int(20 / fe[1]):]) + int(20 / fe[1])]
    print(f"plugin f0 {F0:.2f} Hz, engine strongest line {ef0:.2f} Hz")
    e0 = harm(e0w, F0)
    for name, kw in CASES:
        d = preset(filter_1_on=1.0, filter_1_model=6.0, **kw)
        vr = harm(vrender(d), F0)
        er = harm(engine_render(d, root, Path(a.cli)), F0)
        vdb = 20 * np.log10(np.maximum(vr, 1e-12) / np.maximum(v0, 1e-12))
        edb = 20 * np.log10(np.maximum(er, 1e-12) / np.maximum(e0, 1e-12))
        ok = (np.minimum(vr / v0, 1e9) > 10 ** (-50 / 20)) & (er / e0 > 10 ** (-50 / 20))
        diff = vdb - edb
        print(f"{name:28s} plugin mean {vdb.mean():+6.1f} max {vdb.max():+5.1f} | engine mean {edb.mean():+6.1f} max {edb.max():+5.1f} | offset {np.median(diff):+5.1f} dB, rms {np.sqrt(np.mean((diff[ok] - np.median(diff[ok])) ** 2)):4.1f} dB over {int(ok.sum())} harmonics", flush=True)


if __name__ == "__main__":
    main()
