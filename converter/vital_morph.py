# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Vital's spectral morph on wavetable frames, baked into the table at the preset's saved amount.

A frame is a set of harmonics (0 = DC, 1..1024, complex). Each type maps the input harmonics to the output harmonics for an amount a in 0..1:

  2 Formant scale  output i reads the input at i * g, g = 2^(2 - 4a), by complex interpolation between the two same-parity harmonics around it, scaled by g
  3 Stretch        harmonic h moves to 1 + (h - 1) * s, s = 2^(8a - 4), split linearly between the two neighbouring integer harmonics
  4 Inharmonic     harmonic k moves to max(1, 1 + (k - 1) * m^(log2(k) / 10)), m = 2^(24a - 12), split linearly the same way
  5 Smear          a running amplitude that leaks forward along the even and the odd harmonics with a gain of (j + 0.25) / j per pair
  6 Random         each harmonic's gain follows a fixed pseudo-random table (Mersenne Twister, seed 4) that grows with the amount
  7 Low pass       gain(h) = clamp(c - h + 1, 0, 1), c = 2^(10a)
  8 High pass      gain(h) = clamp(h - c + 1, 0, 1)
  9 Phase disperse harmonic h is rotated by p * ((h - 24)^2 - 23^2), p = a * 0.05
 11 Skew           each harmonic reads the wavetable at a position warped by its own log frequency (needs the whole table)

Type 1 (vocode) is Formant scale read at the note's absolute frequency (its g is also scaled by note / the table's own fundamental), baked at the note the importer gives it; type 10 (Shepard) depends on the played pitch and is not carried. Each type is checked against the plugin by calibration/vital_morph_check.py.
"""
from __future__ import annotations

import numpy as np

KNOWN = (1, 2, 3, 4, 5, 6, 7, 8, 9, 11)
VOCODE_EXP = 1.0                   # kMaxFormantShift (Vocode's range, like Formant scale's 2)
VOCODE_RATIO = None                # the played note's frequency over the wavetable's own (set by the importer for each oscillator): Vocode reads the frame at the note's absolute frequency
NH = 1024
PHASE_SIGN = 1.0                   # the sign of the phase-disperse rotation in numpy's rfft convention (calibration/vital_morph_check.py)
_RAND = None


def cutoff_harmonic(a: float) -> float:
    return 2.0 ** (10.0 * a)


def stretch_scale(a: float) -> float:
    return 2.0 ** (8.0 * a - 4.0)


def _random_table() -> np.ndarray:
    global _RAND
    if _RAND is None:
        rs = np.random.RandomState(4)
        n = 17 * (NH + 1 + 1) // 4 * 4                            # (stages + 1) * (harmonics + 1) values
        raw = rs.randint(0, 2 ** 32, size=n, dtype=np.uint64).astype(np.float64)
        u = np.minimum((raw / 4294967296.0).astype(np.float32), np.float32(1.0 - 2 ** -24))
        _RAND = (-1.0 + 2.0 * u).astype(np.float64)
    return _RAND


MIN_PHASE_AMP = 0.1                # a harmonic quieter than this takes its direction from the nearest louder frame (or +i when there is none)


def _unit_phases(sps: np.ndarray) -> np.ndarray:
    """The unit-length direction of every harmonic of every frame; harmonics too quiet to have a meaningful phase borrow it from the louder frames around them."""
    mag = np.abs(sps)
    unit = np.where(mag > 1e-9, sps / np.where(mag > 1e-9, mag, 1.0), 1j)
    nf = len(sps)
    out = np.empty_like(unit)
    for h in range(sps.shape[1]):
        loud = np.flatnonzero(mag[:, h] > MIN_PHASE_AMP)
        if len(loud) == 0:
            out[:, h] = 1j
            continue
        out[:, h] = np.interp(np.arange(nf), loud, unit[loud, h].real) + 1j * np.interp(np.arange(nf), loud, unit[loud, h].imag)
    return out


def _split(spec: np.ndarray, pos: np.ndarray) -> np.ndarray:
    """spec[h] for h = 1..n moved to the (float) positions pos[h - 1], split linearly between the two integer neighbours."""
    n = len(spec)
    out = np.zeros(n + 2, dtype=np.complex128)
    lo = np.floor(pos).astype(np.int64)
    frac = pos - lo
    ok = (lo >= 1) & (lo <= n)
    np.add.at(out, lo[ok] - 1, spec[ok] * (1.0 - frac[ok]))
    ok2 = (frac > 0) & (lo + 1 >= 1) & (lo + 1 <= n)
    np.add.at(out, lo[ok2], spec[ok2] * frac[ok2])
    return out[:n]


def morph_spectrum(sp: np.ndarray, typ: int, a: float, unit: np.ndarray | None = None, g_over: float | None = None) -> np.ndarray:
    """sp[0..NH] (DC and harmonics 1..NH, complex); returns the morphed spectrum, same shape. Type 11 is handled in morph_table."""
    h = np.arange(1, NH + 1, dtype=np.float64)
    body = sp[1:NH + 1]
    out = np.zeros_like(sp)
    out[0] = sp[0]
    if typ == 7:
        out[1:] = body * np.clip(cutoff_harmonic(a) - h + 1.0, 0.0, 1.0)
    elif typ == 8:
        out[1:] = body * np.clip(h - cutoff_harmonic(a) + 1.0, 0.0, 1.0)
    elif typ == 3:
        out[1:] = _split(body, 1.0 + (h - 1.0) * stretch_scale(a))
    elif typ == 4:
        mult = 2.0 ** (24.0 * a - 12.0)
        shift = mult ** (np.log2(h) / 10.0)
        out[1:] = _split(body, np.maximum(1.0, shift * (h - 1.0) + 1.0))
    elif typ in (1, 2):
        g = 2.0 ** (2.0 - 4.0 * a) if g_over is None else g_over
        last = int(min(NH, 1024.0 / g))
        for i in range(1, last + 1):
            sh = max(1.0, i * g)
            k0 = int(sh)
            k0 -= (i + k0) % 2
            t = (sh - k0) * 0.5
            frm = sp[k0] if 0 <= k0 <= NH else 0.0
            to = sp[k0 + 2] if 0 <= k0 + 2 <= NH else 0.0
            out[i] = g * (frm * (1.0 - t) + to * t)
    elif typ == 5:
        s = 1.0 - (1.0 - a) ** 3
        mag = np.abs(sp)
        ph = unit if unit is not None else np.exp(1j * np.angle(sp))
        npair = (NH + 1) // 2 + 1                                # element j holds harmonics 2j and 2j + 1
        amp = np.array([mag[0], mag[1]]) * (1.0 - s)
        out[0], out[1] = amp[0] * ph[0], amp[1] * ph[1]
        for j in range(1, npair):
            e, o = 2 * j, 2 * j + 1
            orig = np.array([mag[e] if e <= NH else 0.0, mag[o] if o <= NH else 0.0])
            amp = orig + (amp - orig) * s
            if e <= NH:
                out[e] = amp[0] * ph[e]
            if o <= NH:
                out[o] = amp[1] * ph[o]
            amp = amp * ((j + 0.25) / j)
    elif typ == 6:
        shift = a * 15.0
        idx = min(int(shift), 14)
        t = shift - idx
        scale, center, mult = shift, 1.0 - shift, 1.0 + shift
        rnd = _random_table()
        b1, b2 = idx * 1025 // 4, (idx + 1) * 1025 // 4
        j = np.arange(0, NH + 1) // 2
        lane = (np.arange(0, NH + 1) % 2) * 2
        r1 = rnd[4 * (b1 + j) + lane]
        r2 = rnd[4 * (b2 + j) + lane]
        g1 = mult * np.maximum(center - scale * r1, 0.0)
        g2 = mult * np.maximum(center - scale * r2, 0.0)
        gain = g1 + (g2 - g1) * t
        mag = np.abs(sp)
        new = np.minimum(gain * mag, 1024.0)
        out = np.where(mag > 1e-9, sp / np.where(mag > 1e-9, mag, 1.0) * new, 0.0)
        out[0] = sp[0]
    elif typ == 9:
        p = a * 0.05
        ang = p * ((h - 24.0) ** 2 - 23.0 ** 2)
        out[1:] = body * np.exp(1j * PHASE_SIGN * ang)
    return out


def _skew(sps: np.ndarray, a: float) -> np.ndarray:
    nf = len(sps)
    shift = a * a * 16.0
    mag = np.abs(sps)
    unit = np.where(mag > 1e-9, sps / np.where(mag > 1e-9, mag, 1.0), 1.0)
    out = np.zeros_like(sps)
    out[:, 0] = sps[:, 0]
    maxf = nf - 1.0
    i = np.arange(1, NH + 1)
    sc = np.log2(i) / 11.0
    for f in range(nf):
        v = (f / maxf + shift * sc) * 0.5
        bv = 1.0 - (v - np.floor(v)) * 2.0
        si = (1.0 - np.abs(bv)) * maxf
        f0 = np.minimum(si.astype(np.int64), nf - 2)
        t = np.minimum(1.0, si - f0)
        amp = mag[f0, i] + (mag[f0 + 1, i] - mag[f0, i]) * t
        u = unit[f0, i] + (unit[f0 + 1, i] - unit[f0, i]) * t
        out[f, 1:NH + 1] = amp * u
    return out


def morph_table(table: np.ndarray, typ: int, a: float) -> np.ndarray | None:
    """A (frames, 2048) table through the morph; None when the type is not carried or the morph does nothing."""
    if typ not in KNOWN:
        return None
    if typ == 1:
        if VOCODE_RATIO is None:
            return None
        g = 2.0 ** ((a - 0.5) * 2.0 * -VOCODE_EXP) * VOCODE_RATIO
        sp = np.fft.rfft(table, axis=1)[:, :NH + 1]
        new = np.array([morph_spectrum(f, 1, a, None, g) for f in sp])
        full = np.zeros((len(table), table.shape[1] // 2 + 1), dtype=np.complex128)
        full[:, :NH + 1] = new
        full[:, 0] = 0.0
        return np.fft.irfft(full, n=table.shape[1], axis=1)
    if (typ in (2, 3, 4) and abs(a - 0.5) < 1e-3) or (typ == 7 and cutoff_harmonic(a) > 1023) or (typ == 8 and cutoff_harmonic(a) <= 1.0):
        return None
    sp = np.fft.rfft(table, axis=1)[:, :NH + 1]
    if typ == 11:
        if len(table) < 2:
            return None
        new = _skew(sp, a)
    elif typ == 5:
        units = _unit_phases(sp)
        new = np.array([morph_spectrum(f, typ, a, u) for f, u in zip(sp, units)])
    else:
        new = np.array([morph_spectrum(f, typ, a) for f in sp])
    full = np.zeros((len(table), table.shape[1] // 2 + 1), dtype=np.complex128)
    full[:, :NH + 1] = new
    full[:, 0] = 0.0
    return np.fft.irfft(full, n=table.shape[1], axis=1)
