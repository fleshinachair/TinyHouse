#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""A/B a Vital preset against its TinyHouse conversion: the same note through the real Vital VST3 and through the engine.

    python calibration/vital_ab.py "Preset.vital" [more.vital ...] [-o out/vital_ab] [--note 60 --vel 100]

Writes <out>/<name>_vital.wav and <out>/<name>_tinyhouse.wav (the engine's volume is set so both play at the same loudness over the held
part of the note, so what you hear is tone and effects, not level) and prints, per preset, which of Vital's effects were carried and how
close the effect tail and the brightness came. Needs Vital installed, dawdreamer, soundfile, numpy and calibration/tablor_cli.exe.
Reference renders of third-party presets are for listening only: keep them out of the repository (out/ is ignored).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "converter"))
sys.path.insert(0, str(ROOT / "calibration"))
import vital_import as VI                                           # noqa: E402
import vital_render as VR                                           # noqa: E402

CLI = ROOT / "calibration/tablor_cli.exe"
SR = 44100
HOLD, DUR = 1.5, 4.0


def tablor_render(move_root: Path, blob: str, note: int, vel: int, tmp: Path) -> np.ndarray:
    bf, rf = tmp / "b.txt", tmp / "o.raw"
    bf.write_text(blob, encoding="utf-8", newline="\n")
    r = subprocess.run([str(CLI), str(move_root), str(bf), str(rf), str(DUR), str(HOLD), str(vel), str(note), os.environ.get("TB_SEED", "1")], capture_output=True, timeout=300)
    if r.returncode:
        raise RuntimeError(r.stderr.decode(errors="replace")[:200])
    return np.frombuffer(rf.read_bytes(), dtype="<f4").reshape(-1, 2).T.copy()


def rms(x: np.ndarray, t0: float, t1: float) -> float:
    seg = x[:, int(t0 * SR):int(t1 * SR)]
    return float(np.sqrt(np.mean(seg ** 2))) if seg.size else 0.0


def db(x: float) -> float:
    return 20 * np.log10(max(x, 1e-9))


def centroid(x: np.ndarray, t0: float, t1: float) -> float:
    seg = x[:, int(t0 * SR):int(t1 * SR)].mean(axis=0)
    mag = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    f = np.fft.rfftfreq(len(seg), 1 / SR)
    return float((f * mag).sum() / max(mag.sum(), 1e-9))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("presets", nargs="+", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "out/vital_ab")
    ap.add_argument("--note", type=int, default=60)
    ap.add_argument("--vel", type=int, default=100)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    host = VR.VitalHost()
    tmp = a.out / "_tmp"
    tmp.mkdir(exist_ok=True)
    rows = []
    for p in a.presets:
        m = VI.convert(p, a.out)
        name = m["name"]
        v = np.asarray(host.render(p, ((a.note, a.vel, 0.0, HOLD),), DUR), dtype=np.float32)
        # the level is matched over the held part of the note, or over its first 0.3 s when the note is a click or a short pluck (a kick is silent by 0.3 s: the held part would match silence)
        w0, w1 = (0.3, HOLD) if db(rms(v, 0.3, HOLD)) > db(rms(v, 0.0, 0.3)) - 25.0 else (0.0, 0.3)
        target = rms(v, w0, w1)
        # the engine's master is squared (pot / 127)^2: move the volume pot until the held part matches Vital's
        vol = float(m["params"]["volume"])
        raw = None
        for _ in range(5):
            m["params"]["volume"] = int(round(min(127, max(1, vol))))
            t = tablor_render(a.out / "move", VI.blob(m), a.note, a.vel, tmp)
            got = rms(t, w0, w1)
            if raw is None:
                raw = db(got) - db(target)                                   # how far the converter's own master volume is from Vital's level, before any tuning
            if got < 1e-7 or abs(db(got) - db(target)) < 0.3 or m["params"]["volume"] in (1, 127):
                break
            vol *= (target / got) ** 0.5
        pre = a.out / "move/data" / VI.d2m.MOVE_PRESET_DIR / f"{name}.json"
        import json
        pre.write_text(json.dumps({"name": name, "module": "tinyhouse", "version": 1, "state": VI.blob(m)}) + "\n", encoding="utf-8")
        sf.write(a.out / f"{name}_vital.wav", v.T, SR)
        sf.write(a.out / f"{name}_tinyhouse.wav", t.T, SR)
        tail_v = db(rms(v, 2.2, 3.2)) - db(rms(v, 0.5, 1.4))
        tail_t = db(rms(t, 2.2, 3.2)) - db(rms(t, 0.5, 1.4))
        rows.append((name, m, db(target), db(rms(t, w0, w1)), tail_v, tail_t, centroid(v, 0.3, 1.2), centroid(t, 0.3, 1.2), float(np.abs(v).max()), float(np.abs(t).max())))
        print(f"{name}: raw level {raw:+.1f} dB before tuning, vol pot {m['params']['volume']}, level {db(target):.1f} vs {db(rms(t, w0, w1)):.1f} dB, "
              f"tail {tail_v:.1f} vs {tail_t:.1f} dB, centroid {rows[-1][6]:.0f} vs {rows[-1][7]:.0f} Hz, "
              f"FX {m['carried_fx']} of {m['fx_in_preset']}", flush=True)
    lines = ["# Vital vs TinyHouse renders", "", f"Note {a.note}, velocity {a.vel}, held {HOLD} s, level-matched over the held part.", "",
             "| preset | FX in Vital | FX carried | tail after release (Vital / TinyHouse, re held level) | brightness Hz (Vital / TinyHouse) | not carried |", "|---|---|---|---|---|---|"]
    for name, m, lv, lt, tv, tt, cv, ct, pv, pt in rows:
        lines.append(f"| {name} | {', '.join(m['fx_in_preset']) or '-'} | {', '.join(m['carried_fx']) or '-'} | {tv:.1f} / {tt:.1f} dB | {cv:.0f} / {ct:.0f} | {'; '.join(m['notes'])} |")
    (a.out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwritten to {a.out}")


if __name__ == "__main__":
    main()
