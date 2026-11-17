#!/usr/bin/env python3
"""Set each preset's master volume so every preset plays at the same loudness, whatever its level in Serum.

  converter/play_level.py SRC_DIR [--out DIR] [--target -20] [--peak -1] [--only NAME ...]

Serum's presets are not level-matched to each other (a pad can be 30 dB under a bass), and the converter matches each preset to ITS Serum render, so a bank
keeps that spread. This is the opt-in alternative for playing on the Move: a chord at velocity 100 goes through the real engine (calibration/tablor_cli.exe),
the volume is moved until the loudest 300 ms of it sits at --target dB (full scale = 0 dB, power over both channels), and then held down if the chord's peak
passes --peak dBFS, so a hard-hitting preset ends under the target rather than clipped. Only the `volume` key changes; the compressor and distortion sit
before the master gain, so nothing else needs to follow it.

Reads and writes Schwung preset files ({"name", "module", "version", "state"}). import_pack.py --play-level calls fit_state() on each preset it keeps.
Needs numpy and calibration/tablor_cli.exe (scripts/zig-build.sh native); the wavetables and samples a preset names are read from out/move.
"""
import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "calibration/tablor_cli.exe"
MOVE_ROOT = ROOT / "out/move"
SR = 44100
TARGET_DB, PEAK_DB, TOL_DB = -20.0, -1.0, 0.4
WINDOW_S, DUR_S, HOLD_S, VELOCITY = 0.3, 4.0, 2.5, 100


def getk(state: str, key: str, default: float) -> float:
    m = re.search(rf"(?:^|;){key}=([^;]*)", state)
    try:
        return float(m.group(1)) if m else default
    except ValueError:
        return default


def setk(state: str, key: str, val) -> str:
    if re.search(rf"(^|;){key}=", state):
        return re.sub(rf"((?:^|;){key}=)[^;]*", rf"\g<1>{val}", state, count=1)
    return state + f"{key}={val};"


def chord(state: str, name: str = "") -> list:
    """The notes the preset is judged on: one note for a mono voice, a four-note chord otherwise, two octaves down for a bass (the same chords the converter's clipping check uses)."""
    low = bool(re.match(r"\s*(BA\b|Bass\b|BS\b|SUB\b)", name, re.I))
    if getk(state, "voice_mode", 0) == 1:
        return [36 if low else 48]
    return [36, 43, 48, 51] if low else [48, 55, 60, 63]


def render(state: str, notes: list, seed: int) -> np.ndarray:
    with tempfile.TemporaryDirectory() as td:
        bf, rf = Path(td, "b.txt"), Path(td, "o.raw")
        bf.write_text(state, encoding="utf-8", newline="\n")
        r = subprocess.run([str(CLI), str(MOVE_ROOT), str(bf), str(rf), str(DUR_S), str(HOLD_S), str(VELOCITY), ",".join(map(str, notes)), str(seed)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode:
            raise RuntimeError(f"tablor_cli {r.returncode}: {r.stderr[:120]}")
        x = np.fromfile(rf, dtype=np.float32).astype(np.float64)
    return np.stack([x[0::2], x[1::2]])


def measure(state: str, notes: list, renders: int = 2) -> tuple:
    """(loudest 300 ms in dB, peak in dBFS) of the chord, averaged over a couple of renders (the oscillator phases are random)."""
    w, power, peak = int(WINDOW_S * SR), [], 0.0
    for seed in range(1, renders + 1):
        a = render(state, notes, seed)
        m = (a ** 2).mean(axis=0)
        c = np.cumsum(np.concatenate([[0.0], m]))
        win = (c[w:] - c[:-w]) / w
        power.append(win[: int(HOLD_S * SR)].max())
        peak = max(peak, float(np.abs(a).max()))
    return 10 * math.log10(float(np.mean(power)) + 1e-20), 20 * math.log10(peak + 1e-9)


def fit_state(state: str, name: str = "", target_db: float = TARGET_DB, peak_db: float = PEAK_DB) -> tuple:
    """(state at the fitted volume, info dict). The master gain is 40 log10 of the volume knob, so each step moves it by that law; the volume is a whole number from 1 to 127."""
    notes = chord(state, name)
    v0 = int(getk(state, "volume", 64))
    v, best = v0, None
    for _ in range(8):
        st = setk(state, "volume", v)
        loud, peak = measure(st, notes)
        err = target_db - loud
        if best is None or abs(err) < abs(best[0]):
            best = (err, v, loud, peak)
        if abs(err) <= TOL_DB:
            break
        nv = int(round(min(max(v * 10 ** (err / 40.0), 1), 127)))
        if nv == v:
            if (nv == 127 and err > 0) or (nv == 1 and err < 0):
                break
            nv = v + (1 if err > 0 else -1)
        v = nv
    _, v, loud, peak = best
    held = False
    for _ in range(8):                                  # the peak ceiling: a hard-hitting preset stays under it, quieter than the target
        if peak <= peak_db + 0.2 or v <= 1:
            break
        held = True
        nv = int(round(min(max(v * 10 ** ((peak_db - peak) / 40.0), 1), v - 1)))
        v = max(nv, 1)
        loud, peak = measure(setk(state, "volume", v), notes)
    return setk(state, "volume", v), {"from": v0, "to": v, "loud": loud, "peak": peak, "held_by_peak": held, "notes": notes}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src", type=Path, help="folder of Schwung preset .json files")
    ap.add_argument("--out", type=Path, default=None, help="where to write the levelled presets (default: SRC_DIR/../<name>_levelled)")
    ap.add_argument("--target", type=float, default=TARGET_DB, help="loudest 300 ms of the chord, dB re full scale")
    ap.add_argument("--peak", type=float, default=PEAK_DB, help="highest peak the chord may reach, dBFS")
    ap.add_argument("--only", nargs="*", default=None, help="preset names to do (default: all)")
    a = ap.parse_args()
    if not CLI.exists():
        sys.exit("calibration/tablor_cli.exe is not built: run schwung-tablor/scripts/zig-build.sh native")
    out = a.out or a.src.parent / (a.src.name + "_levelled")
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for f in sorted(a.src.glob("*.json")):
        if a.only is not None and f.stem not in a.only:
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        st, info = fit_state(d["state"], f.stem, a.target, a.peak)
        d["state"] = st
        (out / f.name).write_text(json.dumps(d) + "\n", encoding="utf-8")
        rows.append((f.stem, info))
        print(f"{f.stem:32s} volume {info['from']:3d} -> {info['to']:3d}   loudest 300 ms {info['loud']:6.1f} dB   peak {info['peak']:6.1f} dBFS" + ("   held by the peak ceiling" if info["held_by_peak"] else ""), flush=True)
    if rows:
        ls = [i["loud"] for _, i in rows]
        print(f"{len(rows)} presets -> {out}: loudness {min(ls):.1f} .. {max(ls):.1f} dB (spread {max(ls) - min(ls):.1f} dB), {sum(i['held_by_peak'] for _, i in rows)} held by the peak ceiling")


if __name__ == "__main__":
    main()
