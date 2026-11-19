#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Measure how much a Vital modulation amount moves a control, through the real plugin.

    python calibration/vital_route_probe.py [-o calibration/vital_route_laws.json]

A macro held at 1.0 is routed (amount a) to a control. The sound is compared with the same preset with the control set directly; the direct setting
whose spectrum matches the routed one is the effective shift. Reports the shift in the control's own units per unit of amount, so the converter's
route scales are measured, not assumed.
"""
from __future__ import annotations

import argparse
import base64
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


def _wave(kind: str) -> str:
    x = np.arange(2048) / 2048.0
    w = {"saw": 1 - 2 * x, "sine": np.sin(2 * np.pi * x)}[kind]
    return base64.b64encode(w.astype("<f4").tobytes()).decode()


def base_preset() -> dict:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    s = d["settings"]
    kf = s["wavetables"][0]["groups"][0]["components"][0]["keyframes"]
    kf[:] = [{"position": 0, "wave_data": _wave("saw")}, {"position": 255, "wave_data": _wave("sine")}]     # a saw morphing to a sine, so cutoff and frame both change the spectrum
    s["osc_1_wave_frame"] = 0.0
    for fx in ALL_FX:
        s[f"{fx}_on"] = 0.0
    s["filter_1_on"], s["filter_2_on"], s["sample_on"] = 1.0, 0.0, 0.0
    s["osc_2_on"] = s["osc_3_on"] = 0.0
    s["osc_1_on"], s["osc_1_level"], s["osc_1_unison_voices"] = 1.0, 0.7, 1.0
    s["filter_1_model"], s["filter_1_style"], s["filter_1_blend"] = 0.0, 0.0, 0.0
    s["filter_1_filter_input"] = 0.0
    s["filter_1_cutoff"], s["filter_1_resonance"], s["filter_1_drive"], s["filter_1_mix"] = 70.0, 0.2, 0.0, 1.0
    s["filter_1_keytrack"] = 0.0
    s["env_1_attack"], s["env_1_decay"], s["env_1_sustain"], s["env_1_release"] = 0.001 ** 0.25, 1.0, 1.0, 0.1
    s["modulations"] = [{"destination": "", "source": ""} for _ in s["modulations"]]
    for i in range(1, 65):
        for k in ("amount", "bipolar", "bypass", "power", "stereo"):
            s[f"modulation_{i}_{k}"] = 0.0
    for i in range(1, 5):
        s[f"macro_control_{i}"] = 0.0
    return d


def route(d: dict, src: str, dst: str, amount: float, bipolar: bool = False) -> dict:
    d = json.loads(json.dumps(d))
    s = d["settings"]
    s["modulations"][0] = {"destination": dst, "source": src}
    s["modulation_1_amount"] = amount
    s["modulation_1_bipolar"] = 1.0 if bipolar else 0.0
    return d


def render(host, d: dict) -> np.ndarray:
    return np.asarray(host.render(json.dumps(d).encode(), ((60, 100, 0.0, 1.8),), 2.0), dtype=np.float32)


def feature(x: np.ndarray) -> float:
    """Spectral centroid over the held part (log-frequency weighted, so it changes smoothly over a wide range)."""
    seg = x[:, int(0.3 * SR):int(1.5 * SR)].mean(axis=0)
    mag = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    f = np.fft.rfftfreq(len(seg), 1 / SR)
    return float((f * mag).sum() / max(mag.sum(), 1e-12))


def level(x: np.ndarray) -> float:
    seg = x[:, int(0.3 * SR):int(1.5 * SR)]
    return float(np.sqrt(np.mean(seg ** 2)))


def match(curve_x: np.ndarray, curve_y: np.ndarray, y: float) -> float:
    """The control value whose feature equals y (curve_y must be monotonic; flipped if falling)."""
    if curve_y[0] > curve_y[-1]:
        curve_x, curve_y = curve_x[::-1], curve_y[::-1]
    return float(np.interp(y, curve_y, curve_x))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "calibration/vital_route_laws.json")
    a = ap.parse_args()
    host = VR.VitalHost()
    out: dict = {}
    base = base_preset()

    # cutoff: direct sweep, then macro routes at several amounts from a base cutoff
    c0 = 60.0
    sweep = np.arange(30.0, 136.0, 4.0)
    ys = []
    for c in sweep:
        d = json.loads(json.dumps(base)); d["settings"]["filter_1_cutoff"] = float(c)
        ys.append(feature(render(host, d)))
    ys = np.array(ys)
    print("cutoff sweep (note, centroid):", [(float(c), round(float(y))) for c, y in zip(sweep, ys)], flush=True)
    out["cutoff_sweep"] = {float(c): float(y) for c, y in zip(sweep, ys)}
    out["cutoff_route"] = {}
    for amt in (0.1, 0.2, 0.3, 0.5):
        d = json.loads(json.dumps(base)); d["settings"]["filter_1_cutoff"] = c0; d["settings"]["macro_control_1"] = 1.0
        y = feature(render(host, route(d, "macro_control_1", "filter_1_cutoff", amt)))
        eff = match(sweep, ys, y) - c0
        out["cutoff_route"][amt] = {"shift_st": eff, "per_unit": eff / amt}
        print(f"cutoff route amount {amt}: centroid {y:.0f} -> shift {eff:.1f} st ({eff / amt:.1f} st per unit; Vital range is 128)", flush=True)
    for amt in (-0.2,):
        d = json.loads(json.dumps(base)); d["settings"]["filter_1_cutoff"] = 90.0; d["settings"]["macro_control_1"] = 1.0
        y = feature(render(host, route(d, "macro_control_1", "filter_1_cutoff", amt)))
        eff = match(sweep, ys, y) - 90.0
        out["cutoff_route"][amt] = {"shift_st": eff, "per_unit": eff / amt}
        print(f"cutoff route amount {amt}: shift {eff:.1f} st ({eff / amt:.1f} per unit)", flush=True)
    # bipolar: macro at 1.0 bipolar acts as +1 (v = 2*1 - 1), at 0.5 as 0
    d = json.loads(json.dumps(base)); d["settings"]["filter_1_cutoff"] = c0; d["settings"]["macro_control_1"] = 0.5
    y = feature(render(host, route(d, "macro_control_1", "filter_1_cutoff", 0.3, True)))
    print(f"bipolar route, macro at 0.5: shift {match(sweep, ys, y) - c0:.1f} st (0 expected)", flush=True)
    d["settings"]["macro_control_1"] = 1.0
    y = feature(render(host, route(d, "macro_control_1", "filter_1_cutoff", 0.3, True)))
    print(f"bipolar route, macro at 1.0, amount 0.3: shift {match(sweep, ys, y) - c0:.1f} st (unipolar 0.3 gave {out['cutoff_route'][0.3]['shift_st']:.1f})", flush=True)

    # level
    lv_sweep = np.arange(0.05, 1.0, 0.1)
    ly = []
    for v in lv_sweep:
        d = json.loads(json.dumps(base)); d["settings"]["osc_1_level"] = float(v)
        ly.append(level(render(host, d)))
    ly = np.array(ly)
    out["level_sweep"] = {float(v): float(y) for v, y in zip(lv_sweep, ly)}
    print("level sweep:", [(round(float(v), 2), round(float(y), 4)) for v, y in zip(lv_sweep, ly)], flush=True)
    out["level_route"] = {}
    for amt in (0.2, 0.4):
        d = json.loads(json.dumps(base)); d["settings"]["osc_1_level"] = 0.3; d["settings"]["macro_control_1"] = 1.0
        y = level(render(host, route(d, "macro_control_1", "osc_1_level", amt)))
        eff = match(lv_sweep, ly, y) - 0.3
        out["level_route"][amt] = {"shift": eff, "per_unit": eff / amt}
        print(f"level route amount {amt}: shift {eff:.3f} ({eff / amt:.2f} per unit)", flush=True)

    # wave frame
    fr_sweep = np.arange(0.0, 256.0, 16.0)
    fy = []
    for f in fr_sweep:
        d = json.loads(json.dumps(base)); d["settings"]["osc_1_wave_frame"] = float(f)
        fy.append(feature(render(host, d)))
    fy = np.array(fy)
    out["frame_sweep"] = {float(f): float(y) for f, y in zip(fr_sweep, fy)}
    print("frame sweep:", [(float(f), round(float(y))) for f, y in zip(fr_sweep, fy)], flush=True)
    out["frame_route"] = {}
    for amt in (0.2, 0.4):
        d = json.loads(json.dumps(base)); d["settings"]["osc_1_wave_frame"] = 20.0; d["settings"]["macro_control_1"] = 1.0
        y = feature(render(host, route(d, "macro_control_1", "osc_1_wave_frame", amt)))
        eff = match(fr_sweep, fy, y) - 20.0
        out["frame_route"][amt] = {"shift": eff, "per_unit": eff / amt}
        print(f"frame route amount {amt}: shift {eff:.1f} frames ({eff / amt:.0f} per unit; assumed 256)", flush=True)

    a.out.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("written", a.out)


if __name__ == "__main__":
    main()
