#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Vital's compressor, plugin against the engine's Vital compressor law (fx.h processCompVital): the gain a held sine gets in each band at several input levels.

    python calibration/vital_comp_ab.py [--cli path]

For each compressor setting, band (note 36 / 72 / 108) and level, the render with the compressor on is compared with the one with it off, in each renderer, and the two gains (dB) are printed
side by side. Settings: the plugin's defaults, a limiter, an upward compressor, and Digestive Trauma's own.
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

SR = 44100


def rms_db(x: np.ndarray, t0: float = 0.6, t1: float = 1.4) -> float:
    seg = x[int(t0 * SR):int(t1 * SR)]
    return 20 * np.log10(max(float(np.sqrt(np.mean(seg ** 2))), 1e-9))


def engine(d: dict, note: int, root: Path, cli: Path) -> np.ndarray:
    m = VI.map_vital(json.loads(json.dumps(d)), "comp", root / "move/data/UserData/UserLibrary/Wavetables", "Probe")
    m["params"]["volume"] = 40
    tmp = Path(tempfile.mkdtemp())
    (tmp / "b.txt").write_text(VI.blob(m), encoding="utf-8", newline="\n")
    subprocess.run([str(cli), str(root / "move"), str(tmp / "b.txt"), str(tmp / "o.raw"), "2.0", "1.5", "100", str(note), "1"], check=True)
    return np.frombuffer((tmp / "o.raw").read_bytes(), "<f4").reshape(-1, 2).T.copy().mean(axis=0)


def settings_sets(dt: dict) -> dict:
    lim = {f"compressor_{b}_upper_threshold": -30.0 for b in ("low", "band", "high")}
    lim.update({f"compressor_{b}_upper_ratio": 0.8 for b in ("low", "band", "high")})
    lim.update({f"compressor_{b}_lower_ratio": 0.0 for b in ("low", "band", "high")})
    up = {f"compressor_{b}_lower_threshold": -30.0 for b in ("low", "band", "high")}
    up.update({f"compressor_{b}_lower_ratio": 0.6 for b in ("low", "band", "high")})
    up.update({f"compressor_{b}_upper_ratio": 0.0 for b in ("low", "band", "high")})
    return {"plugin defaults": {}, "limiter -30 dB, 0.8": lim, "upward under -30 dB, 0.6": up, "Digestive Trauma": dt}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", default=str(ROOT / "calibration/tablor_cli.exe"))
    ap.add_argument("--levels", type=float, nargs="*", default=[0.05, 0.2, 0.5, 1.0])
    a = ap.parse_args()
    host = VR.VitalHost()
    root = Path(tempfile.mkdtemp())
    dt = json.load(open(Path(r"C:\Users\Mat\OneDrive\Documents\Vital\Glorkglunk\Presets\Digestive Trauma.vital"), encoding="utf-8"))["settings"]
    dts = {k: v for k, v in dt.items() if k.startswith("compressor_") and k != "compressor_on"}
    for name, kw in settings_sets(dts).items():
        print(f"== {name}")
        for band, note in (("low", 36), ("mid", 72), ("high", 108)):
            row = []
            for lv in a.levels:
                base = RP.base_preset()
                s = base["settings"]
                s.update({"filter_1_on": 0.0, "osc_1_wave_frame": 255.0, "osc_1_random_phase": 0.0, "osc_1_midi_track": 1.0, "osc_1_transpose": 0.0, "osc_1_level": lv})
                for fx in ("reverb_on", "delay_on", "chorus_on", "flanger_on", "phaser_on", "distortion_on", "eq_on", "filter_fx_on"):
                    s[fx] = 0.0
                s.update(kw)
                g = []
                for r in ("vital", "engine"):
                    gains = []
                    for on in (0.0, 1.0):
                        s["compressor_on"] = on
                        x = (np.asarray(host.render(json.dumps(base).encode(), ((note, 100, 0.0, 1.5),), 2.0), dtype=np.float32).mean(axis=0) if r == "vital"
                             else engine(base, note, root, Path(a.cli)))
                        gains.append(rms_db(x))
                    g.append(gains[1] - gains[0])
                row.append(f"{lv:4.2f}: V {g[0]:+6.1f} E {g[1]:+6.1f}")
            print(f"  {band:5s} " + " | ".join(row), flush=True)


if __name__ == "__main__":
    main()
