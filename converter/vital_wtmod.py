# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Vital's wavetable modifiers (Wave Folder, Frequency Filter, Wave Warp, Phase Shift) applied to a rendered group's frames.

Ported from src/common/wavetable/{wave_fold,frequency_filter,wave_warp,phase}_modifier.cpp. A modifier has keyframes of its own; for each frame the parameters
are tweened between the two keyframes around that frame's position (WavetableComponent::interpolate) and the modifier then reshapes the frame.
"""
from __future__ import annotations

import math

import numpy as np

FRAME = 2048
PHASE_SIGN = 1.0                                   # which way Vital's FFT turns a positive phase (checked against the plugin: calibration/vital_modifier_check.py)

MODIFIERS = ("Wave Folder", "Frequency Filter", "Wave Warp", "Phase Shift")


def _keyframe_params(keys: list[dict], names: tuple[str, ...], pos: float, style: int) -> dict:
    """The component's keyframe parameters at table position `pos`: held (style 0), or a linear tween between the keyframes either side."""
    n = len(keys)
    index = sum(1 for k in keys if int(pos) >= k.get("position", 0)) - 1            # getIndexFromPosition(position) - 1
    frm = keys[min(max(index, 0), n - 1)]
    if index < 0 or index >= n - 1 or style == 0:
        return {k: float(frm.get(k, 0.0)) for k in names}
    to = keys[index + 1]
    p0, p1 = float(keys[index].get("position", 0)), float(to.get("position", 0))
    t = (pos - p0) / (p1 - p0) if p1 != p0 else 0.0
    return {k: float(frm.get(k, 0.0)) + (float(to.get(k, 0.0)) - float(frm.get(k, 0.0))) * t for k in names}


def _power_scale(v: np.ndarray, power: float) -> np.ndarray:
    if abs(power) < 0.01:
        return v
    num = np.exp(power * np.abs(v)) - 1.0
    den = math.exp(power) - 1.0
    return np.where(v >= 0.0, num / den, -num / den)


def _fold(x: np.ndarray, boost: float) -> np.ndarray:
    peak = max(1.0, float(np.abs(x).max()))
    v = np.clip(x / peak, -1.0, 1.0)
    return np.sin(peak * boost * np.arcsin(v))


def _warp(x: np.ndarray, hp: float, vp: float, h_asym: bool, v_asym: bool) -> np.ndarray:
    h = np.arange(FRAME) / (FRAME - 1.0)
    wh = _power_scale(h, hp) if h_asym else 0.5 * _power_scale(2.0 * h - 1.0, hp) + 0.5
    fi = (FRAME - 1) * wh
    i = np.clip(fi.astype(int), 0, FRAME - 2)
    v = np.clip(x[i] + (x[i + 1] - x[i]) * (fi - i), -1.0, 1.0)
    return 2.0 * _power_scale(0.5 * v + 0.5, vp) - 1.0 if v_asym else _power_scale(v, vp)


def _filter_gain(style: int, cutoff: float, shape: float, bins: np.ndarray) -> np.ndarray:
    ci = 2.0 ** cutoff
    delta = bins - ci
    slope = 1.0 / (1.0 + (128.0 - 1.0) * shape * shape)
    power = -9.0 + 18.0 * shape
    if style == 0:
        return np.clip(1.0 - slope * delta, 0.0, 1.0)
    if style == 1:
        return np.clip(1.0 - np.abs(slope * delta), 0.0, 1.0)
    if style == 2:
        return np.clip(1.0 + slope * delta, 0.0, 1.0)
    if style == 3:
        t = bins / (ci * 2.0)
        r = t - np.floor(t)
        return 2.0 * _power_scale(1.0 - np.abs(2.0 * r - 1.0), power)
    return np.zeros_like(bins)


def _freq_filter(x: np.ndarray, style: int, cutoff: float, shape: float, normalize: bool) -> np.ndarray:
    sp = np.fft.rfft(x)
    sp *= _filter_gain(style, cutoff, shape, np.arange(len(sp), dtype=np.float64))
    y = np.fft.irfft(sp, n=FRAME)
    if normalize:
        y = y / max(1e-7, float(np.abs(y).max()))
    return y


def _phase(x: np.ndarray, style: int, phase: float, mix: float) -> np.ndarray:
    sp = np.fft.rfft(x)
    i = np.arange(len(sp), dtype=np.float64)
    ph = -PHASE_SIGN * phase
    if style == 2:                                                       # Harmonic: every harmonic turned by the same phase
        mult = np.full(len(sp), np.exp(1j * ph))
    elif style == 3:                                                     # Harmonic Even/Odd
        mult = np.where(i % 2 == 0, np.exp(1j * ph), np.exp(-1j * ph))
    elif style == 0:                                                     # Normal: a time shift (phase grows with the harmonic number)
        mult = np.exp(1j * ph * i)
    elif style == 1:                                                     # Even/Odd
        mult = np.where(i % 2 == 0, np.exp(1j * ph * i), np.exp(-1j * ph * i))
    else:                                                                # Clear: all harmonics in phase with the first
        return np.fft.irfft(np.abs(sp), n=FRAME)
    return np.fft.irfft(mix * sp * mult + (1.0 - mix) * sp, n=FRAME)


def apply_modifier(comp: dict, frames: np.ndarray, positions: np.ndarray) -> np.ndarray:
    """One modifier component over a group's frames; `positions` is each frame's table position (0..255)."""
    keys = sorted(comp.get("keyframes", []), key=lambda k: k.get("position", 0))
    if not keys:
        return frames
    typ = comp.get("type")
    style = int(comp.get("interpolation_style", 1))
    out = np.empty_like(frames, dtype=np.float64)
    for i, (fr, pos) in enumerate(zip(frames, positions)):
        x = fr.astype(np.float64)
        if typ == "Wave Folder":
            out[i] = _fold(x, _keyframe_params(keys, ("fold_boost",), pos, style)["fold_boost"])
        elif typ == "Wave Warp":
            p = _keyframe_params(keys, ("horizontal_power", "vertical_power"), pos, style)
            out[i] = _warp(x, p["horizontal_power"], p["vertical_power"], bool(comp.get("horizontal_asymmetric")), bool(comp.get("vertical_asymmetric")))
        elif typ == "Frequency Filter":
            p = _keyframe_params(keys, ("cutoff", "shape"), pos, style)
            out[i] = _freq_filter(x, int(comp.get("style", 0)), p["cutoff"], p["shape"], bool(comp.get("normalize", True)))
        elif typ == "Phase Shift":
            p = _keyframe_params(keys, ("phase", "mix"), pos, style)
            out[i] = _phase(x, int(comp.get("style", 0)), p["phase"], p["mix"])
        else:
            out[i] = x
    return out
