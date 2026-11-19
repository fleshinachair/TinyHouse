#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Measure Vital's delay and reverb through the real plugin, and the engine's reverb, so the converter's FX laws are checked, not guessed.

    python calibration/vital_fx_probe.py [-o calibration/vital_fx_laws.json]

A short pluck (every other effect off) is played through
  - Vital's delay: first echo time against delay_tempo for the three tempo types, and against delay_frequency with sync off;
  - Vital's reverb: decay time (the -5 to -25 dB part of the tail, extrapolated to -60 dB) against reverb_decay_time;
  - the engine's reverb: the same measurement against rev_decay, so a Vital decay can be matched by the pot that measures the same.
Everything is played at Vital's own default 120 BPM through DawDreamer.
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
sys.path.insert(0, str(ROOT / "converter"))
sys.path.insert(0, str(ROOT / "calibration"))
import vital_import as VI                                           # noqa: E402
import vital_render as VR                                           # noqa: E402

SR = 44100
SRC = Path.home() / "OneDrive/Documents/Vital/Factory/Presets/Touch Tone.vital"
ALL_FX = ("reverb", "delay", "chorus", "flanger", "phaser", "distortion", "compressor", "eq", "filter_fx")


def pluck_preset() -> dict:
    """A short, bright, dry pluck: Touch Tone's oscillator with a 20 ms decay and every effect off."""
    d = json.loads(SRC.read_text(encoding="utf-8"))
    s = d["settings"]
    for fx in ALL_FX:
        s[f"{fx}_on"] = 0.0
    s["filter_1_on"] = s["filter_2_on"] = 0.0
    s["sample_on"] = 0.0
    s["env_1_attack"], s["env_1_decay"], s["env_1_sustain"], s["env_1_release"] = 0.03 ** 0.25, 0.05 ** 0.25, 0.0, 0.02 ** 0.25
    s["modulations"] = [{"destination": "", "source": ""} for _ in s["modulations"]]      # an empty slot is these two empty strings; a bare {} makes Vital open a "preset is corrupted" dialog and stall the render
    for i in range(1, 65):
        s[f"modulation_{i}_amount"] = 0.0
    return d


def envelope(x: np.ndarray, win: float = 0.005) -> np.ndarray:
    m = np.abs(x).max(axis=0) if x.ndim == 2 else np.abs(x)
    k = int(win * SR)
    return np.convolve(m, np.ones(k) / k, mode="same")


def first_echo_s(x: np.ndarray) -> float | None:
    """Time of the first echo's peak after the note has ended (the note is under 0.1 s long)."""
    e = envelope(x)
    t0 = int(0.12 * SR)
    seg = e[t0:]
    if seg.max() < 1e-4:
        return None
    thr = 0.25 * seg.max()
    i = int(np.argmax(seg > thr))
    j = i + int(np.argmax(seg[i:i + int(0.4 * SR)]))
    return (t0 + j) / SR


def t60(x: np.ndarray, start: float) -> float | None:
    """Reverb decay: slope of the tail's level between -5 and -25 dB from its start, extrapolated to -60 dB."""
    e = envelope(x, 0.02)[int(start * SR):]
    if e.max() < 1e-5:
        return None
    db = 20 * np.log10(np.maximum(e, 1e-9) / e.max())
    i0 = int(np.argmax(db < -5))
    i1 = int(np.argmax(db < -25))
    if i1 <= i0 + 10:
        return None
    return float(-60.0 / ((db[i1] - db[i0]) / ((i1 - i0) / SR)))


def vital_render(host, d: dict, dur: float) -> np.ndarray:
    return np.asarray(host.render(json.dumps(d).encode("utf-8"), ((60, 110, 0.0, 0.05),), dur), dtype=np.float32)


def tablor_render(P: dict, dur: float) -> np.ndarray:
    cli = ROOT / "calibration/tablor_cli.exe"
    m = {"params": dict(VI.d2m.TABLOR_DEFAULTS) | P, "paths": {"wt1_table": "Init", "wt2_table": "Init", "wt3_table": "Init"}}
    m["params"].update({"wt1_level": 100, "vca_a": 0, "vca_d": 60, "vca_s": 0, "vca_r": 60, "flt_freq": 127, "voice_mode": 0, "dly_on": 0, "chor_on": 0, "eq_on": 0, "dist_mix": 0, "comp_on": 0, "fx_space": 127})
    m["params"].update(P)
    with tempfile.TemporaryDirectory() as td:
        bf, rf = Path(td, "b.txt"), Path(td, "o.raw")
        bf.write_text(VI.blob(m), encoding="utf-8", newline="\n")
        r = subprocess.run([str(cli), str(ROOT / "out/move"), str(bf), str(rf), str(dur), "0.05", "110", "60", "1"], capture_output=True, timeout=300)
        if r.returncode:
            raise RuntimeError(r.stderr.decode(errors="replace")[:200])
        return np.frombuffer(rf.read_bytes(), dtype="<f4").reshape(-1, 2).T.copy()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "calibration/vital_fx_laws.json")
    a = ap.parse_args()
    host = VR.VitalHost()
    laws: dict = {"delay_synced_s": {}, "delay_hz_s": {}, "vital_reverb_t60": {}, "engine_reverb_t60": {}}

    base = pluck_preset()
    for sync in (1, 2, 3):                                           # Tempo, Tempo Dotted, Tempo Triplets
        row = {}
        for tempo in range(0, 13):
            d = json.loads(json.dumps(base))
            s = d["settings"]
            s.update(delay_on=1.0, delay_dry_wet=1.0, delay_feedback=0.0, delay_style=0.0, delay_sync=float(sync), delay_tempo=float(tempo), delay_filter_cutoff=60.0, delay_filter_spread=1.0)
            row[tempo] = first_echo_s(vital_render(host, d, 6.0))
            print(f"delay sync {sync} tempo {tempo}: {row[tempo]}", flush=True)
        laws["delay_synced_s"][sync] = row
    for f in (-2.0, -1.0, 0.0, 1.0, 2.0, 3.0):
        d = json.loads(json.dumps(base))
        d["settings"].update(delay_on=1.0, delay_dry_wet=1.0, delay_feedback=0.0, delay_style=0.0, delay_sync=0.0, delay_frequency=f)
        laws["delay_hz_s"][f] = first_echo_s(vital_render(host, d, 6.0))
        print(f"delay freq {f}: {laws['delay_hz_s'][f]} (1/2^f = {1 / 2 ** f:.3f})", flush=True)
    for dt in (-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0):
        d = json.loads(json.dumps(base))
        d["settings"].update(reverb_on=1.0, reverb_dry_wet=1.0, reverb_decay_time=dt, reverb_size=0.5, reverb_delay=0.0)
        laws["vital_reverb_t60"][dt] = t60(vital_render(host, d, 20.0), 0.1)
        print(f"vital reverb decay_time {dt}: T60 {laws['vital_reverb_t60'][dt]} (2^dt = {2 ** dt:.2f})", flush=True)
    for pot in range(0, 128, 8):
        laws["engine_reverb_t60"][pot] = t60(tablor_render({"rev_on": 1, "rev_mix": 127, "rev_decay": pot, "rev_damp": 127}, 25.0), 0.1)
        print(f"engine rev_decay {pot}: T60 {laws['engine_reverb_t60'][pot]}", flush=True)
    a.out.write_text(json.dumps(laws, indent=1), encoding="utf-8")
    print("written", a.out)


if __name__ == "__main__":
    main()
