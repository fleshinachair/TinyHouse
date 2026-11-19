#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Measure how loud Vital's reverb and delay are against their dry/wet knob, and the engine's against its mix pot.

    python calibration/vital_mix_probe.py [-o calibration/vital_mix_laws.json]

A short pluck goes through one effect. Measured: the dry part (the first 0.12 s, peak RMS) and the tail's RMS between 0.3 and 1.5 s after the note.
The tail-to-dry ratio in dB against Vital's dry_wet (0..1) and the engine's mix pot (0..127) shows which pot gives the same loudness as a Vital setting.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "converter"))
sys.path.insert(0, str(ROOT / "calibration"))
import vital_fx_probe as FP                                         # noqa: E402
import vital_render as VR                                           # noqa: E402

SR = 44100


def rms(x: np.ndarray, t0: float, t1: float) -> float:
    seg = x[:, int(t0 * SR):int(t1 * SR)]
    return float(np.sqrt(np.mean(seg ** 2))) if seg.size else 0.0


def db(x: float) -> float:
    return 20 * np.log10(max(x, 1e-9))


REF = {"dry": 0.0}


def measure(x: np.ndarray) -> dict:
    """Tail levels in dB relative to the dry note (REF['dry'], rendered with the effect off), plus the effect's own dry level (does the dry/wet knob also lower the dry?)."""
    return {"dry_db": db(rms(x, 0.0, 0.12)) - REF["dry"], "tail_db": db(rms(x, 0.3, 1.5)) - REF["dry"], "late_db": db(rms(x, 1.5, 3.0)) - REF["dry"]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "calibration/vital_mix_laws.json")
    a = ap.parse_args()
    host = VR.VitalHost()
    base = FP.pluck_preset()
    base["settings"]["osc_1_random_phase"] = base["settings"]["osc_2_random_phase"] = 0.0             # a fixed start phase so the dry level repeats
    REF["dry"] = db(rms(FP.vital_render(host, base, 2.0), 0.0, 0.12))
    ref_e = db(rms(FP.tablor_render({}, 2.0), 0.0, 0.12))
    print("reference dry levels (dB):", REF["dry"], ref_e, flush=True)
    out: dict = {"vital_reverb": {}, "engine_reverb": {}, "vital_delay": {}, "engine_delay": {}}

    for w in (0.1, 0.2, 0.3, 0.5, 0.7, 1.0):
        d = json.loads(json.dumps(base))
        d["settings"].update(reverb_on=1.0, reverb_dry_wet=w, reverb_decay_time=1.0, reverb_size=0.5, reverb_delay=0.0)
        out["vital_reverb"][w] = measure(FP.vital_render(host, d, 4.0))
        print("vital reverb", w, out["vital_reverb"][w], flush=True)
    REF["dry"] = ref_e                                                    # the engine's own dry level for its renders
    for p in (8, 16, 32, 48, 64, 80, 96, 127):
        out["engine_reverb"][p] = measure(FP.tablor_render({"rev_on": 1, "rev_mix": p, "rev_decay": 64, "rev_damp": 127}, 4.0))     # decay pot 64 ~ 1.9 s as Vital's decay_time 1
        print("engine reverb", p, out["engine_reverb"][p], flush=True)
    REF["dry"] = db(rms(FP.vital_render(host, base, 2.0), 0.0, 0.12))
    for w in (0.1, 0.3, 0.5, 0.7, 1.0):
        d = json.loads(json.dumps(base))
        d["settings"].update(delay_on=1.0, delay_dry_wet=w, delay_feedback=0.5, delay_style=0.0, delay_sync=0.0, delay_frequency=1.0, delay_filter_cutoff=60.0, delay_filter_spread=1.0)
        out["vital_delay"][w] = measure(FP.vital_render(host, d, 4.0))
        print("vital delay", w, out["vital_delay"][w], flush=True)
    REF["dry"] = ref_e
    for p in (16, 32, 48, 64, 80, 96, 127):
        out["engine_delay"][p] = measure(FP.tablor_render({"dly_on": 1, "dly_mix": p, "dly_fb": 64, "dly_sync": 0, "dly_time": 108, "dly_timer": 108}, 4.0))
        print("engine delay", p, out["engine_delay"][p], flush=True)
    a.out.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("written", a.out)


if __name__ == "__main__":
    main()
