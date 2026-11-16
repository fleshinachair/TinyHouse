#!/usr/bin/env python3
"""Rough multi-pitch transcription of a demo MP3: which notes play when.

  transcribe.py file.mp3 [lo_midi hi_midi max_notes]

Harmonic-salience per frame (each candidate note scores the spectral energy at its first six harmonics, nearer
harmonics weighted more), the strongest note picked, its harmonics subtracted, repeat. Frames are 4096 samples
with a 1024 hop. Notes that persist for several frames become events. This is for deciding what to play through a
preset to compare with the demo, not a score: octave errors and missed quiet notes are expected.
Output: a piano-roll list of (start s, end s, midi note) and calibration/transcripts/<name>.json
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100
N, HOP = 4096, 1024
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def name(m):
    return f"{NAMES[m % 12]}{m // 12 - 1}"


def main():
    path = sys.argv[1]
    lo, hi, kmax = (int(a) for a in sys.argv[2:5]) if len(sys.argv) >= 5 else (36, 96, 4)
    x, sr = sf.read(path, always_2d=True)
    x = x.mean(axis=1)
    assert sr == SR
    win = np.hanning(N)
    freqs = np.fft.rfftfreq(N, 1 / SR)
    cand = np.arange(lo, hi + 1)
    f0 = 440 * 2 ** ((cand - 69) / 12.0)
    harm = [(h, 0.85 ** (h - 1)) for h in range(1, 7)]
    frames = []
    for i in range(0, len(x) - N, HOP):
        mag = np.log1p(30 * np.abs(np.fft.rfft(x[i:i + N] * win)) / N)
        picked = []
        for _ in range(kmax):
            sal = np.zeros(len(cand))
            for ci, f in enumerate(f0):
                s = 0.0
                for h, w in harm:
                    k = int(round(f * h / (SR / N)))
                    if k + 2 < len(mag):
                        s += w * mag[k - 1:k + 2].max()
                sal[ci] = s
            for p in picked:                       # nothing within a semitone of a note already taken
                sal[max(p - lo - 1, 0):p - lo + 2] = 0
            b = int(np.argmax(sal))
            if sal[b] < 0.35 * (frames[-1]["top"] if frames else sal[b]) and picked or sal[b] < 1.2:
                break
            if not picked:
                top = sal[b]
            elif sal[b] < 0.5 * top:
                break
            picked.append(int(cand[b]))
            for h, w in harm:                      # take this note's harmonics out of the spectrum
                k = int(round(f0[b] * h / (SR / N)))
                if k + 2 < len(mag):
                    mag[k - 1:k + 2] *= 0.25
        frames.append({"t": i / SR, "notes": sorted(picked), "top": float(top) if picked else 0.0})
    # events: a note counts while it appears in at least 2 of any 3 consecutive frames
    events, active = [], {}
    for fi, fr in enumerate(frames):
        for n in set(fr["notes"]):
            active.setdefault(n, [fr["t"], fr["t"], 0])
            a = active[n]
            if fr["t"] - a[1] <= 3 * HOP / SR:
                a[1] = fr["t"]; a[2] += 1
            else:
                events.append((a[0], a[1], n, a[2])); active[n] = [fr["t"], fr["t"], 1]
    events += [(a[0], a[1], n, a[2]) for n, a in active.items()]
    events = sorted([(round(s, 3), round(e + HOP / SR, 3), n) for s, e, n, c in events if c >= 3 and e - s >= 0.05])
    out = Path("calibration/transcripts"); out.mkdir(exist_ok=True)
    (out / (Path(path).stem + ".json")).write_text(json.dumps({"frames": frames, "events": events}), encoding="utf-8")
    print(f"{len(events)} note events, {len(x) / SR:.1f} s")
    for s, e, n in events[:60]:
        print(f"  {s:6.2f} - {e:6.2f}  {name(n):4s} ({n})")


if __name__ == "__main__":
    main()
