#!/usr/bin/env python3
"""Where does Serum's voice-filter cutoff sit at each note when key tracking is on, and does the converted engine preset follow it?

A held saw through Voice Filter 1 (MgL24, no resonance, key tracking 1.0 and 0, a fixed cutoff), the transfer read off every harmonic as the dB change against the unfiltered saw,
and the frequency where it first falls 12 dB below its passband. Serum and the engine (through the converter's own mapping) are compared note by note.
  probe_keytrack.py [cutoff ...]        writes calibration/probe_keytrack.json
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import test_render_calibration as H

SR = H.SR
NOTES = [24, 30, 36, 42, 48, 60, 72, 84]
DUR, HOLD = 1.3, 1.25


def corner(x, note, drop=-12.0, base=None):
    """Frequency (Hz) where the harmonic transfer first falls `drop` dB under its passband level, or None."""
    f0 = 440 * 2 ** ((note - 69) / 12)
    m = x[0, int(0.35 * SR):int(1.2 * SR)]
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)), 1 << 18)); fr = np.fft.rfftfreq(1 << 18, 1 / SR)
    ks = [k for k in range(1, 400) if k * f0 < 17000]
    h = np.array([20 * np.log10(sp[np.searchsorted(fr, k * f0) - 3:np.searchsorted(fr, k * f0) + 4].max() + 1e-12) for k in ks])
    return ks, h, f0


def cutoff_hz(ks, h, hb, f0):
    t = h - hb
    t = np.convolve(t, np.ones(3) / 3, mode="same")
    ref = np.median(t[: max(len(t) // 20, 3)]) if len(t) > 6 else t[0]
    for i in range(2, len(t)):
        if t[i] < ref - 12.0 and t[i - 1] >= ref - 12.0:
            return float(ks[i] * f0)
    return None


def main():
    cuts = [float(a) for a in sys.argv[1:]] or [0.45, 0.6]
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t)
    S = H.Serum()
    res = {}
    for c in cuts:
        for kt in (1.0, 0.0):
            t = H.with_filter(tree0, c, 0.0, "MgL24")
            t["VoiceFilter0"]["plainParams"]["kParamKeyTrack"] = kt
            row = {}
            for n in NOTES:
                if S.audio_seconds > 36:
                    S = H.Serum()
                bs = np.asarray(S.render(tree0, [n], DUR, HOLD)); fs = np.asarray(S.render(t, [n], DUR, HOLD))
                ks, hb, f0 = corner(bs, n); _, hf, _ = corner(fs, n)
                xe0, _ = H.tablor_render(tree0, [n], DUR, HOLD); xe, _ = H.tablor_render(t, [n], DUR, HOLD)
                _, heb, _ = corner(np.asarray(xe0), n); _, hef, _ = corner(np.asarray(xe), n)
                row[n] = (cutoff_hz(ks, hf, hb, f0), cutoff_hz(ks, hef, heb, f0))
            res[f"{c}|{kt}"] = row
            print(f"cutoff {c} key tracking {kt}: corner Hz (-12 dB) per note, Serum / engine", flush=True)
            print("   ", "  ".join(f"{n}: {a if a is None else round(a)} / {b if b is None else round(b)}" for n, (a, b) in row.items()), flush=True)
    (ROOT / "calibration/probe_keytrack.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    os._exit(0)


if __name__ == "__main__":
    main()
