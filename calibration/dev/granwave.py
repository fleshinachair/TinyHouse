#!/usr/bin/env python3
"""Helpers for probing Serum's Granular oscillator with synthetic samples (tone1k.wav: a steady 1 kHz sine, 8 s; ruler.wav: 16 half-second tones, segment k at 300 * 2^(k/4) Hz, so the
frequency says which part of the file a grain came from). The two files live in Serum's Samples/User/_tinyhouse_probe/ (made by granwave.make_samples(); delete that folder when done).
render(sample, gran, osc, ...) puts one Granular oscillator straight to the output and returns the stereo audio.  gran = GranularOsc plainParams, osc = the oscillator's plainParams."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration")); os.chdir(ROOT)
import numpy as np
import test_render_calibration as H
SR = H.SR
REL = "User/_tinyhouse_probe/"
_, _bt = H.load_preset("Keys - Loop")
_base = H.init_tree(_bt, table="Analog/Basic Shapes.wav", frames=7)
_serum = [None]


def make_samples():
    import soundfile as sf, serum_paths
    d = serum_paths.SERUM_ROOT / "Samples" / "User" / "_tinyhouse_probe"; d.mkdir(parents=True, exist_ok=True)
    sr = 44100; t = np.arange(8 * sr) / sr
    sf.write(d / "tone1k.wav", (0.5 * np.sin(2 * np.pi * 1000 * t)).astype("float32"), sr, subtype="PCM_16")
    x = np.zeros_like(t)
    for k in range(16):
        i0, i1 = int(k * 0.5 * sr), int((k + 1) * 0.5 * sr)
        x[i0:i1] = 0.5 * np.sin(2 * np.pi * 300 * 2 ** (k / 4) * (t[i0:i1] - t[i0]))
    sf.write(d / "ruler.wav", x.astype("float32"), sr, subtype="PCM_16")
    n = np.random.default_rng(1).standard_normal(8 * sr)
    sf.write(d / "noise.wav", (n / np.abs(n).max() * 0.9).astype("float32"), sr, subtype="PCM_16")
    c = np.zeros(8 * sr, dtype="float32"); c[int(0.05 * sr)] = 0.9; c[int(0.15 * sr)] = -0.9      # every grain read from the start gives +pulse at age 50 ms, -pulse at 150 ms
    sf.write(d / "click.wav", c, sr, subtype="PCM_16")


def tree(sample, gran, osc):
    t = copy.deepcopy(_base)
    t["Oscillator0"] = {
        "plainParams": {"kParamType": "kOsc_Granular", "kParamEnable": 1.0, "kParamVolume": 0.75, **osc},
        "GranularOsc0": {"numChannels": 1, "numFrames": 8 * 44100, "sampleRate": 44100, "samplePathRelative": REL + sample, "plainParams": dict(gran)},
        "WTOsc0": {"plainParams": "default"}, "SampleOsc0": {"plainParams": "default"}, "MultiSampleOsc0": {"plainParams": "default"}, "SpectralOsc0": {"plainParams": "default"}}
    return t


def render(sample, gran=None, osc=None, note=60, dur=3.0, hold=2.8, vel=100):
    if _serum[0] is None or _serum[0].audio_seconds > 500:
        _serum[0] = H.Serum()
    return _serum[0].render(tree(sample, gran or {}, osc or {}), [note], dur, hold, vel)


def spec(x, t0, t1, n=1 << 15):
    m = x[0, int(t0 * SR):int(t1 * SR)]
    s = np.abs(np.fft.rfft(m * np.hanning(len(m)), n)) ** 2
    return np.fft.rfftfreq(n, 1 / SR), s


def peak_hz(x, t0, t1):
    f, s = spec(x, t0, t1)
    return f[int(np.argmax(s))]


def make_click1():
    """click1.wav: a single +0.9 click at 50 ms (no negative click), so pulses of different grains never cancel or overlap."""
    import soundfile as sf, serum_paths
    d = serum_paths.SERUM_ROOT / "Samples" / "User" / "_tinyhouse_probe"; sr = 44100
    c = np.zeros(8 * sr, dtype="float32"); c[int(0.05 * sr)] = 0.9
    sf.write(d / "click1.wav", c, sr, subtype="PCM_16")
