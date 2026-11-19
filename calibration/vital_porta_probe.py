#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Vital's portamento, measured through the plugin: a sine, notes 60 then 72, the pitch followed in 10 ms steps.

    python calibration/vital_porta_probe.py

For each setting it prints how long the glide from 60 to 72 takes to cover 10 %, 50 % and 90 % of the interval (in seconds from the second note-on), for an
overlapping pair (legato) and for a note that starts after a gap (does Vital glide from the previous note at all?).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "calibration"))
import vital_render as VR                                           # noqa: E402
import vital_rm_probe as RM                                         # noqa: E402

SR = 44100


def pitch_track(x: np.ndarray, t0: float, t1: float, step: float = 0.01, win: float = 0.04) -> tuple[np.ndarray, np.ndarray]:
    ts, ps = [], []
    n = int(win * SR)
    w = np.hanning(n)
    for t in np.arange(t0, t1, step):
        seg = x[int(t * SR):int(t * SR) + n]
        if len(seg) < n:
            break
        sp = np.abs(np.fft.rfft(seg * w, 16 * n))
        k = int(np.argmax(sp[int(40 * 16 * n / SR):])) + int(40 * 16 * n / SR)
        f = k * SR / (16 * n)
        ts.append(t); ps.append(69 + 12 * np.log2(f / 440.0))
    return np.array(ts), np.array(ps)


def crossings(ts, ps, start, a, b, t_on):
    out = []
    for frac in (0.1, 0.5, 0.9):
        lvl = a + (b - a) * frac
        idx = np.where(((ps - lvl) * np.sign(b - a) >= 0) & (ts >= start))[0]
        out.append(round(float(ts[idx[0]] - t_on), 3) if len(idx) else None)
    return out


def engine_render(d: dict, events: list[tuple[float, float, int]], dur: float) -> np.ndarray:
    """The same preset through the converter and the engine; events are (on s, off s, note)."""
    import os
    import subprocess
    import tempfile
    sys.path.insert(0, str(ROOT / "converter"))
    import vital_import as VI
    root, tmp = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    m = VI.map_vital(d, "p", root / "move/data/UserData/UserLibrary/Wavetables", "Probe")
    m["params"]["volume"] = 20
    (tmp / "b.txt").write_text(VI.blob(m), encoding="utf-8", newline="\n")
    (tmp / "ev.txt").write_text("".join(f"{a} {b} {n} 100\n" for a, b, n in events), encoding="utf-8")
    env = dict(os.environ, TB_EVENTS=str(tmp / "ev.txt"))
    subprocess.run([str(ROOT / "calibration/tablor_cli.exe"), str(root / "move"), str(tmp / "b.txt"), str(tmp / "o.raw"), str(dur), "1", "100", "60", "1"], check=True, env=env)
    return np.frombuffer((tmp / "o.raw").read_bytes(), "<f4").reshape(-1, 2).T[0].copy()


def main() -> None:
    host = VR.VitalHost()
    if "--engine" in sys.argv:
        return compare(host)
    cases = [(t, sl, fo, sc, leg) for t in (-6.0, -4.0, -2.0, -1.0) for sl, fo, sc, leg in ((0, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1), (4, 0, 0, 0), (-4, 0, 0, 0))]
    print("time slope force scale legato | overlapped 60->72 10/50/90 % | after a gap 72 -> 60 (note 2 starts 0.5 s after note 1 ends)")
    for t, sl, fo, sc, leg in cases:
        d = json.loads(RM.preset(0.0, 0, 24.0, 0.0)); s = d["settings"]
        s["osc_2_on"] = 0.0
        s["osc_1_midi_track"] = 1.0                                  # the RM probe's preset plays one fixed pitch
        s["polyphony"] = 1.0
        s["portamento_time"], s["portamento_slope"], s["portamento_force"], s["portamento_scale"], s["legato"] = float(t), float(sl), float(fo), float(sc), float(leg)
        s["env_1_attack"], s["env_1_sustain"], s["env_1_release"] = 0.0, 1.0, 0.05
        raw = json.dumps(d).encode()
        x = np.asarray(host.render(raw, ((60, 100, 0.0, 2.0), (72, 100, 1.0, 1.5)), 4.0))[0]       # 72 starts while 60 is held
        ts, ps = pitch_track(x, 1.0, 3.0)
        ov = crossings(ts, ps, 1.0, 60, 72, 1.0)
        y = np.asarray(host.render(raw, ((72, 100, 0.0, 0.6), (60, 100, 1.1, 1.0)), 3.0))[0]      # 60 starts 0.5 s after 72 ended
        ts2, ps2 = pitch_track(y, 1.1, 2.0)
        gap = crossings(ts2, ps2, 1.1, 72, 60, 1.1)
        print(f"{t:5.1f} {sl:3d}   {fo}     {sc}    {leg}   | {ov} | {gap}  start {ps[0]:.1f} / {ps2[0]:.1f}")


def compare(host) -> None:
    """Vital and the engine side by side: the 10 / 50 / 90 % times of a 60 -> 72 glide while 60 is held, and of a 60 played 0.5 s after a 72 (Force decides)."""
    print("poly time scale force | overlapped Vital | engine | after a gap Vital | engine")
    for poly in (1, 8):
        for t, sc, fo in ((-4.0, 0, 0), (-2.0, 0, 0), (-1.0, 0, 0), (-1.0, 1, 0), (-2.0, 0, 1), (-0.6, 1, 1)):
            d = json.loads(RM.preset(0.0, 0, 24.0, 0.0)); s = d["settings"]
            s["osc_2_on"], s["osc_1_midi_track"], s["polyphony"] = 0.0, 1.0, float(poly)
            s["portamento_time"], s["portamento_scale"], s["portamento_force"], s["portamento_slope"] = t, float(sc), float(fo), 0.0
            s["env_1_attack"], s["env_1_sustain"], s["env_1_release"] = 0.0, 1.0, 0.05
            raw = json.dumps(d).encode()
            row = []
            for ev, a, b, t0, dur in (([(0.0, 2.0, 60), (1.0, 2.5, 72)], 60, 72, 1.0, 4.0), ([(0.0, 0.6, 72), (1.1, 2.1, 60)], 72, 60, 1.1, 3.0)):
                v = np.asarray(host.render(raw, tuple((n, 100, o, f - o) for o, f, n in ev), dur))[0]
                e = engine_render(d, ev, dur)
                for x in (v, e):
                    ts, ps = pitch_track(x, t0, t0 + 1.5)
                    row.append(crossings(ts, ps, t0, a, b, t0))
            print(f"{poly:4d} {t:5.1f} {sc:4d} {fo:4d} | {row[0]} | {row[1]} | {row[2]} | {row[3]}")


if __name__ == "__main__":
    main()
