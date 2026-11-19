#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Vital preset (.vital) and bank (.vitalbank) -> Tablor preset for the Ableton Move (Schwung), a second front end on the same engine.

    python converter/vital_import.py "My Preset.vital" [more.vital ...] -o out/vital

Written from Vital's file format and by measuring the plugin (calibration/vital_render.py and the vital_*_probe.py scripts), with Vital's source read for the exact laws:
the parameters are plain numbers in real units in the preset's "settings", and the wavetables are inside the file.

Carried:    oscillators 1-3 (wavetable frames and their modifiers, spectral morph, phase distortion, unison laws and stack styles, FM / ring mod), the sample oscillator, both voice filters
            (models, serial routing, dry / wet mix), the amp, filter and two spare envelopes (with delay and hold), four LFOs (delay, fade, phase, smooth), macros, velocity, mod wheel and
            random modulators (as their average), the Filter FX, portamento, and the FX chain (reverb, delay, chorus, flanger, phaser, distortion, compressor, EQ).
Not yet:    random modulators as a live signal, the wavetable "stereo" LFO spread, the Filter FX drive, wavetable component types beyond the sources and the four modifiers.
Everything not carried is listed in the preset's report so nothing is dropped silently. Laws marked GUESS below are first estimates that calibration/vital_ab.py is there to check
against Vital's own renders.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import re
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deephouse2move as d2m                                     # TABLOR_DEFAULTS, pot_time / pot_attack, MOVE_WT_ROOT, MOVE_PRESET_DIR
import sample_export as SE
import vital_morph as VM
import vital_wtmod as WM

FRAME = 2048
FRAMES_OUT = 128                      # the engine's position pot has 128 steps, so more frames than that cannot be told apart
CLAMP = d2m._clamp
# Vital's reverb decay: reverb_decay_time (log2 seconds) -> the T60 measured through the plugin (calibration/vital_fx_probe.py); it follows 2^x from 1 to 8 s and flattens at both ends
VITAL_REVERB_T60 = [(-3.0, 0.27), (-2.0, 0.48), (-1.0, 0.59), (0.0, 1.45), (1.0, 1.93), (2.0, 3.98), (3.0, 8.13), (4.0, 11.2)]


# ---------------------------------------------------------------------------
# Wavetables: the file stores how a table is built; the engine plays finished frames
# ---------------------------------------------------------------------------
def _tween(pts, rng, t, cubic):
    """A value between keyframes: linear, or the cubic whose end slopes follow the neighbouring keyframes. pts = (prev, from, to, next), rng = (prev span, span, next span)."""
    prev, frm, to, nxt = pts
    if not cubic:
        return frm + (to - frm) * t
    rp, r, rn = rng
    slope_from = (to - prev) / (1.0 + rp / r) if rp > 0 else 0.0
    slope_to = (nxt - frm) / (1.0 + rn / r) if rn > 0 else 0.0
    delta = to - frm
    return frm + delta * t + t * (1.0 - t) * ((1.0 - t) * (slope_from - delta) + t * (delta - slope_to))


def _interp_frames(keys: list[tuple[float, np.ndarray]], pos: float, style: int, mode: int = 1) -> np.ndarray:
    """The frame at table position `pos` (0..255) from keyframes [(position, wave)]. style 0 holds the earlier keyframe, 1 is linear, 2 is cubic; mode 0
    blends the waveforms, mode 1 blends the harmonics (square-root amplitude blended, phase moved by the shorter turn)."""
    idx = sum(1 for k in keys if int(pos) >= k[0]) - 1
    n = len(keys)
    if idx < 0:
        return keys[0][1]
    if idx >= n - 1 or style == 0:
        return keys[min(idx, n - 1)][1]
    cubic = style == 2
    nxt_i = idx + 2 if idx + 2 < n else idx
    prv_i = idx - 1 if idx >= 1 else idx + 1
    p_from, p_to = keys[idx][0], keys[idx + 1][0]
    t = (pos - p_from) / (p_to - p_from)
    rng = (p_from - keys[prv_i][0], p_to - p_from, keys[nxt_i][0] - p_to)
    waves = (keys[prv_i][1], keys[idx][1], keys[idx + 1][1], keys[nxt_i][1])
    if mode == 0:
        return _tween(waves, rng, t, cubic)
    spec = [np.fft.rfft(w) for w in waves]
    amp = [np.sqrt(np.abs(x)) for x in spec]
    if cubic:
        d_from = np.angle(np.conj(spec[0]) * spec[1])
        d_to = np.angle(np.conj(spec[1]) * spec[2])
        d_next = np.angle(np.conj(spec[2]) * spec[3])
        ph0 = np.angle(spec[0])
        ph1 = ph0 + np.where(amp[1] > 0, d_from, 0.0)
        ph2 = ph1 + np.where(amp[2] > 0, d_to, 0.0)
        ph3 = ph2 + np.where(amp[3] > 0, d_next, 0.0)
        phase = _tween((ph0, ph1, ph2, ph3), rng, t, True)
    else:
        phase = np.angle(spec[1]) + t * np.angle(np.conj(spec[1]) * spec[2])
        phase = np.where(amp[1] == 0, np.angle(spec[2]), phase)
    out = _tween(tuple(amp), rng, t, cubic) ** 2 * np.exp(1j * phase)
    out[0] = _tween(tuple(x[0].real for x in spec), rng, t, cubic)
    out[-1] = _tween(tuple(x[-1].real for x in spec), rng, t, cubic)
    return np.fft.irfft(out, n=len(waves[0]))


def _line_wave(line: dict) -> np.ndarray:
    """A Line Source keyframe's drawn shape as 2048 samples (the editor's y = 0 is the top; segment powers are ignored: GUESS)."""
    pts = line.get("points") or [0, 1, 1, 1]
    xs, ys = pts[0::2], pts[1::2]
    x = np.arange(FRAME) / FRAME
    return 1.0 - 2.0 * np.interp(x, xs, ys)


def _resample(x: np.ndarray, n: int) -> np.ndarray:
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x) if len(x) > 1 else np.zeros(n)


def _audio_wave(comp: dict, kf: dict, audio: np.ndarray) -> np.ndarray:
    """An Audio File Source keyframe as one 2048-sample cycle. Fitted to what Vital plays (work/audio_probe.py, 32 harmonics of Staggered Phrases): the
    component's window_size (not the keyframe's), no fade window, the window starting half its length after start_position, the audio at its own level."""
    size = int(max(8, comp.get("window_size", kf.get("window_size", FRAME))))
    start = int(kf.get("start_position", 0.0) + size / 2)
    seg = audio[start:start + size]
    if len(seg) < size:
        seg = np.concatenate([seg, np.zeros(size - len(seg))])
    return _resample(seg, FRAME)


def render_group(group: dict, notes: list[str]) -> np.ndarray | None:
    """One group of a wavetable as (128, 2048) frames: its source, then its modifiers in order (Wave Folder, Frequency Filter, Wave Warp, Phase Shift: vital_wtmod.py).
    Any other modifier is named in `notes`."""
    comps = group.get("components", [])
    src = next((c for c in comps if c.get("type") in ("Wave Source", "Line Source", "Audio File Source")), None)
    frames = _render_source(src, notes) if src is not None else None
    if frames is None:
        return None
    pos = np.arange(FRAMES_OUT) * 255.0 / (FRAMES_OUT - 1)
    for c in comps:
        if c is src:
            continue
        if c.get("type") in WM.MODIFIERS:
            frames = WM.apply_modifier(c, frames, pos)
        else:
            notes.append(f"wavetable modifier '{c.get('type')}' not carried")
    return frames


def _render_source(src: dict, notes: list[str]) -> np.ndarray | None:
    kfs = sorted(src.get("keyframes", []), key=lambda k: k.get("position", 0))
    keys: list[tuple[float, np.ndarray]] = []
    audio = None
    if src["type"] == "Audio File Source":
        raw = base64.b64decode(src["audio_file"])
        audio = np.frombuffer(raw[:len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    if src["type"] == "Audio File Source" and kfs:
        # Vital slides the window through the audio between keyframes (FileSource::interpolate: start_position is a linear tween), so every frame is its own slice of the file
        # rather than a blend of the two keyframes' frames (FileSource::render, file_source.cpp)
        ps = [float(k.get("position", 0)) for k in kfs]
        st = [float(k.get("start_position", 0.0)) for k in kfs]
        return np.stack([_audio_wave(src, {"start_position": float(np.interp(i * 255.0 / (FRAMES_OUT - 1), ps, st))}, audio) for i in range(FRAMES_OUT)])
    for k in kfs:
        if src["type"] == "Wave Source":
            w = np.frombuffer(base64.b64decode(k["wave_data"]), dtype="<f4")[:FRAME].astype(np.float64)
        elif src["type"] == "Line Source":
            w = _line_wave(k.get("line", {}))
        else:
            w = _audio_wave(src, k, audio)
        keys.append((float(k.get("position", 0)), w))
    if not keys:
        return None
    style = int(src.get("interpolation_style", 1))
    mode = int(src.get("interpolation", 1)) if src["type"] == "Wave Source" else 0
    return np.stack([_interp_frames(keys, i * 255.0 / (FRAMES_OUT - 1), style, mode) for i in range(FRAMES_OUT)])


def render_table(wt: dict, notes: list[str]) -> np.ndarray | None:
    frames = [f for f in (render_group(g, notes) for g in wt.get("groups", [])) if f is not None]
    if not frames:
        return None
    t = np.sum(frames, axis=0)
    t -= t.mean(axis=1, keepdims=True)                          # remove DC
    peak = np.abs(t).max()
    if wt.get("full_normalize", True) and peak > 1e-9:
        t = t / peak
    return t.astype(np.float32)


_PD_TABLE = None


def bake_phase_distortion(table: np.ndarray, typ: int, amount: float) -> np.ndarray | None:
    """An oscillator's phase distortion (types 1..6: sync, formant, quantize, bend, squeeze, pulse width) at its saved, fixed amount, baked into every frame:
    out(p) = gain(p) * frame(map(p)). The map and gain are MEASURED through the plugin (calibration/vital_pd_table.py), at 65 amounts, so this reads the nearest one.
    Returns None when the type is not a phase map (FM / RM from another oscillator) or the amount is the type's neutral setting."""
    global _PD_TABLE
    if typ < 1 or typ > 6:
        return None
    if _PD_TABLE is None:
        z = np.load(Path(__file__).with_name("vital_pd_table.npz"))
        _PD_TABLE = (z["maps"].astype(np.float64), z["gains"].astype(np.float64))
    maps, gains = _PD_TABLE
    ai = int(round(CLAMP(amount, 0.0, 1.0) * (maps.shape[1] - 1)))
    m, g = maps[typ - 1, ai], gains[typ - 1, ai]
    nb = len(m)
    x = (np.arange(FRAME) + 0.5) * nb / FRAME - 0.5                         # the table's bin centres
    mm = np.interp(x, np.arange(nb), m, period=nb)
    if m.max() - m.min() < 0.05:                                            # a pulse width at its limit: the whole cycle reads one point of the frame, a constant, which Vital's output stage removes (silent, measured)
        mm = np.full(FRAME, float(m.mean()))
    gg = np.interp(x, np.arange(nb), g, period=nb)
    if np.abs(mm - (np.arange(FRAME) + 0.5) / FRAME).max() < 0.01 and np.abs(gg - 1.0).max() < 0.02:
        return None
    pos = (mm % 1.0) * FRAME
    i0 = np.floor(pos).astype(int) % FRAME
    f = pos - np.floor(pos)
    i1 = (i0 + 1) % FRAME
    out = (table[:, i0] * (1.0 - f) + table[:, i1] * f) * gg
    out -= out.mean(axis=1, keepdims=True)
    return out.astype(np.float32)


def amount_sweep_table(table: np.ndarray, pos127: float, kind: str, morph_type: int, morph_amt: float, pd_type: int, pd_amt: float) -> np.ndarray:
    """An oscillator whose distortion (kind 'pd') or morph (kind 'morph') AMOUNT is moved live by an LFO or envelope while its frame stays put: the engine cannot move those amounts, but it moves the table
    position live, so the table is rebuilt with the amount along its frame axis: frame j is the one static frame with that effect at amount j / 127, and the route drives the position instead. The other effect
    stays baked at its saved amount."""
    i0 = int(math.floor(pos127))
    f = pos127 - i0
    i1 = min(i0 + 1, table.shape[0] - 1)
    base = (table[i0] * (1.0 - f) + table[i1] * f)[None].astype(np.float64)
    rows = []
    for j in range(128):
        a = j / 127.0
        fr = base
        ma, pa = (a, pd_amt) if kind == "morph" else (morph_amt, a)
        if morph_type in VM.KNOWN:
            mo = VM.morph_table(fr, morph_type, CLAMP(ma, 0.0, 1.0))
            fr = mo if mo is not None else fr
        if pd_type:
            bk = bake_phase_distortion(fr, pd_type, CLAMP(pa, 0.0, 1.0))
            fr = bk if bk is not None else fr
        rows.append(fr[0])
    return np.stack(rows).astype(np.float32)


def write_table(frames: np.ndarray, wt_root: Path, subdir: str) -> str:
    """Write the table as a Serum-style float WAV (interpolated between frames) and return its path on the Move."""
    digest = hashlib.md5(frames.tobytes()).hexdigest()[:12]
    name = f"vital_{digest}.wav"
    dest = wt_root / subdir / name
    if True:                                                    # always rewritten: an older copy may lack the tbpk chunk
        dest.parent.mkdir(parents=True, exist_ok=True)
        clm = b"<!>2048 10000000 wavetable (www.xferrecords.com)"
        pcm = frames.astype("<f4").tobytes()
        fmt = struct.pack("<HHIIHH", 3, 1, 44100, 44100 * 4, 4, 32)
        body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"clm " + struct.pack("<I", len(clm)) + clm
                + (bytes(1) if len(clm) % 2 else b"") + b"data" + struct.pack("<I", len(pcm)) + pcm
                + b"tbpk" + struct.pack("<I", 4) + struct.pack("<f", 1.0))     # normalise as a table that peaks at 1: Vital's frames keep their own amplitudes (the loader would otherwise raise every table to a 0.9 peak)
        dest.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return f"{d2m.MOVE_WT_ROOT}/{subdir}/{name}"


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
def note_hz(n: float) -> float:
    return 440.0 * 2.0 ** ((n - 69.0) / 12.0)


def pot(x: float) -> int:
    return int(round(CLAMP(x, 0, 127)))


def pot_log(x: float, lo: float, hi: float) -> int:
    """The pot of a control that runs lo..hi exponentially over 0..127."""
    return pot(127.0 * math.log(max(x, lo) / lo) / math.log(hi / lo))


def eq_freq_pot(hz: float) -> int:
    return pot_log(hz, 20.0, 20000.0)


# Vital's filter models: 0 Analog, 1 Dirty, 2 Ladder, 3 Digital, 4 Diode, 5 Formant, 6 Comb, 7 Phaser (read from the plugin's menu strings).
# The blend knob runs LP (0) - BP (1) - HP (2). Style 0 / 1 are the 12 / 24 dB pair for the first five models: GUESS.
VCOMB_TRANS_STEP = 0.75                                                                       # the engine's Blend Transpose pot: (pot - 64) * 0.75 semitones


def comb_params(pre: str, style: int, blend: float, transpose: float) -> dict:
    """The engine's Vital comb law (comb.h processVital): styles 0..2 filter the loop with a low / high blend, styles 3..5 with a band whose width is the blend."""
    return {f"v{'comb' if pre == 'flt' else 'comb2'}": 2 if style >= 3 else 1,
            f"cblend{'' if pre == 'flt' else '2'}": int(round(127 * CLAMP(blend, 0.0, 2.0) / 2.0)),
            f"ctrans{'' if pre == 'flt' else '2'}": int(round(CLAMP(64 + transpose / VCOMB_TRANS_STEP, 0, 127)))}


def filter_type(model: int, style: int, blend: float) -> tuple[int, str | None]:
    if model == 6:
        return (8, 10, 11)[style % 3], None                  # Vital's feedback styles: comb, positive flange, negative flange (the engine's Vital comb law, see comb_params)
    if model == 7:
        return 13, "phaser filter read as 'Phase 24 +' (approximate)"
    steep = 1 if style == 1 else 0
    if model == 4 and 0.5 <= blend < 1.5:
        return 0, None                                           # measured (calibration/vital_vfilter_probe.py --model 4): the Diode filter's middle blends still pass the lows, a 12 dB low-pass, not a band-pass
    kind = 0 if blend < 0.5 else (4 if blend < 1.5 else 2)       # LP, BP, HP
    t = kind + steep
    return t, ("formant filter not carried: read as a low-pass" if model == 5 else None)


# ---------------------------------------------------------------------------
# Modulation: LFOs, macros, envelope routes, velocity
# ---------------------------------------------------------------------------
LR_DST = {"wt1_pos": 1, "wt2_pos": 2, "wt3_pos": 18, "wt1_level": 3, "wt2_level": 4, "wt3_level": 19, "wt1_pan": 11, "wt2_pan": 12,
          "wt1_pitch": 43, "wt2_pitch": 44, "wt3_pitch": 45, "wt1_fine": 61, "wt2_fine": 62, "wt3_fine": 63, "cutoff": 13, "res": 14}          # the engine's lr_dst / me_dst list (tb_opts_3)
FX_MIX_KEYS = {"reverb_dry_wet": ("reverb_on", "rev_mix"), "delay_dry_wet": ("delay_on", "dly_mix"), "chorus_dry_wet": ("chorus_on", "chor_mix"),
               "flanger_dry_wet": ("flanger_on", "flan_mix"), "phaser_dry_wet": ("phaser_on", "phas_mix"), "distortion_mix": ("distortion_on", "dist_mix"),
               "delay_feedback": ("delay_on", "dly_fb")}
# The stored range (min, max) of the effect controls that a macro or velocity route can move (src/common/synth_parameters.cpp). Vital adds a route in the control's own stored units (a fraction of this range), so a macro at its saved value is a fixed offset that can be added to the control before the effect is mapped.
FX_FOLD_RANGE = {
    "filter_1_blend": (0.0, 2.0), "filter_2_blend": (0.0, 2.0),                                    # the voice filters' Blend knob: a route adds amount x 2 x the source
    "reverb_pre_low_cutoff": (0.0, 128.0), "reverb_pre_high_cutoff": (0.0, 128.0), "reverb_low_shelf_cutoff": (0.0, 128.0), "reverb_low_shelf_gain": (-6.0, 0.0), "reverb_high_shelf_cutoff": (0.0, 128.0),
    "reverb_high_shelf_gain": (-6.0, 0.0), "reverb_delay": (0.0, 0.3), "reverb_decay_time": (-6.0, 6.0), "reverb_size": (0.0, 1.0), "reverb_chorus_amount": (0.0, 1.0), "reverb_chorus_frequency": (-8.0, 3.0),
    "delay_frequency": (-2.0, 9.0), "delay_aux_frequency": (-2.0, 9.0), "delay_filter_cutoff": (8.0, 136.0), "delay_filter_spread": (0.0, 1.0),
    "chorus_feedback": (-0.95, 0.95), "chorus_cutoff": (8.0, 136.0), "chorus_spread": (0.0, 1.0), "chorus_frequency": (-6.0, 3.0), "chorus_mod_depth": (0.0, 1.0), "chorus_delay_1": (-10.0, -5.64386), "chorus_delay_2": (-10.0, -5.64386),
    "flanger_feedback": (-1.0, 1.0), "flanger_frequency": (-5.0, 2.0), "flanger_center": (8.0, 136.0), "flanger_mod_depth": (0.0, 1.0),
    "phaser_feedback": (0.0, 1.0), "phaser_frequency": (-5.0, 2.0), "phaser_center": (8.0, 136.0), "phaser_blend": (0.0, 2.0), "phaser_mod_depth": (0.0, 48.0),
    "compressor_mix": (0.0, 1.0), "compressor_attack": (0.0, 1.0), "compressor_release": (0.0, 1.0),
    **{f"compressor_{b}_{k}": r for b in ("low", "band", "high") for k, r in (("upper_threshold", (-80.0, 0.0)), ("lower_threshold", (-80.0, 0.0)), ("upper_ratio", (0.0, 1.0)), ("lower_ratio", (-1.0, 1.0)), ("gain", (-30.0, 30.0)))},
    **{f"eq_{b}_{k}": r for b in ("low", "band", "high") for k, r in (("cutoff", (8.0, 136.0)), ("gain", (-15.0, 15.0)), ("resonance", (0.0, 1.0)))},
    # an LFO's own controls moved by a macro, velocity or a random modulator (taken at its average): the LFO runs at the moved value
    **{f"lfo_{n}_{k}": r for n in range(1, 9) for k, r in (("frequency", (-7.0, 9.0)), ("smooth_time", (-10.0, 4.0)), ("delay_time", (0.0, 4.0)), ("fade_time", (0.0, 8.0)), ("phase", (0.0, 1.0)),
                                                         ("tempo", (0.0, 12.0)))},
}

# Effect mix loudness, measured by calibration/vital_mix_probe.py (tail level 0.3-1.5 s after a short pluck, dB re the dry note).
# Vital's dry/wet is an equal-power crossfade: wet = sin(w * pi / 2); at w = 1 the reverb tail is -17.1 dB and the delay tail -9.0 dB.
# The engine's mix pots are not: this is the tail level each pot gives (reverb decay pot 64, delay feedback 64, 500 ms), saturating near pot 96.
VITAL_FULL_TAIL_DB = {"reverb": -17.1, "delay": -9.0}
ENGINE_TAIL_DB = {"reverb": [(8, -44.7), (16, -33.2), (32, -22.3), (48, -16.5), (64, -13.0), (80, -10.9), (96, -10.0), (127, -9.9)],
                  "delay": [(16, -30.9), (32, -19.9), (48, -14.2), (64, -10.7), (80, -8.7), (96, -8.0), (127, -7.9)]}


def wet_pot(effect: str, w: float) -> int:
    """The engine mix pot whose tail is as loud as Vital's dry/wet `w` (0 stays 0; a level past what the engine reaches is its maximum)."""
    if w <= 1e-3:
        return 0
    target = VITAL_FULL_TAIL_DB[effect] + 20.0 * math.log10(max(math.sin(CLAMP(w, 0.0, 1.0) * math.pi / 2.0), 1e-4))
    pots, dbs = zip(*ENGINE_TAIL_DB[effect])
    if target <= dbs[0]:
        return max(1, int(round(pots[0] * 10 ** ((target - dbs[0]) / 20.0))))     # below the table the pot is about linear in the wet gain
    return pot(float(np.interp(target, dbs, pots)))


VITAL_VOLUME_DB_AT_4879 = -9.5          # master gain at the 4879 knee value, from the compressor fit (the compressor's input level is the output minus this)
ENGINE_PRE_MASTER_OFFSET_DB = -6.7      # the engine's pre-master signal against Vital's pre-volume signal for the same oscillator: measured on a plain level-1 sine, converter volume vs Vital's output (vital_ab.py 'raw level')
VITAL_FX_LIST = ("chorus", "compressor", "delay", "distortion", "eq", "filter_fx", "flanger", "phaser", "reverb")     # the effect list in code-0 order


def effect_order(code: float) -> list[str]:
    """Vital's effect_chain_order as the effects top to bottom. The code is a mixed-radix permutation number (the list position p has a digit 0..p, weight 9!/(p+1)!, that counts
    the effects above it with a higher number in the code-0 list); checked against the plugin: the default order's tails and notes differ from a preset's own exactly where the order moves the compressor."""
    code, n = int(code), len(VITAL_FX_LIST)
    digit = {p: (code // (math.factorial(n) // math.factorial(p + 1))) % (p + 1) for p in range(1, n)}
    rem, out = list(range(n)), [0] * n
    for p in range(n - 1, 0, -1):
        out[p] = rem.pop(len(rem) - 1 - digit[p])
    out[0] = rem.pop()
    return [VITAL_FX_LIST[i] for i in out]


def stage_rank(seq: list[int]) -> int:
    """The lexicographic rank of a stage order among the permutations of 0..6 (the engine's fx_order; the identity is 0)."""
    pool, rank = list(range(len(seq))), 0
    for k, st in enumerate(seq):
        d = pool.index(st)
        pool.pop(d)
        rank += d * math.factorial(len(seq) - 1 - k)
    return rank


VELOCITY_NOMINAL = 100 / 127.0
DPOW_HOLD, DPOW_FAST = 3.0, -8.0                                                # amp decay power from which (a positive one holds and then plunges, a very negative one drops at once) the engine draws Vital's own decay curve (adsr.h setDecayPower); the one-pole fit does better between: -5 to 2 measured


def env_stage_pot(s: dict, k: int, stage: str) -> int:
    """An envelope's Delay or Hold as the engine's hidden pot: Vital stores 0..sqrt 2 and plays its fourth power in seconds (0..4 s); the engine's pot is 4 * (pot / 127)^4 s."""
    return pot(127.0 * max(float(s.get(f"env_{k}_{stage}", 0.0)), 0.0) / 1.41421356)


RANDOM_AS_MEAN = True
AMOUNT_SWEEP = False                                                                        # carry a live LFO / envelope route to an oscillator's distortion or morph amount by sweeping the amount along the table position (amount_sweep_table)
FILTER_BLEND = True                                                                         # the voice filters' Blend knob as a continuous crossfade (engine flt_vb / flt_blend) instead of the nearest LP / BP / HP type
FILTER_BLEND_MODELS = (0, 1, 2, 3)
VITAL_FORMANT = True                                                                         # Vital's Formant voice filter (model 5, styles 0 and 1) as its own engine filter (formant.h) instead of a low-pass
VITAL_PHASER = True                                                                          # Vital's Phaser voice filter (model 7) as its own engine filter (comb.h VitalPhaserFilter) instead of 'Phase 24 +'
RANDOM_LIVE = True                                                                     # a random modulator (Perlin, Sample and Hold, Sine, Lorenz) is a live route source in the engine (rn1..rn4, sources 5..8); False keeps it out
VITAL_BLEND_LIVE = True                                                                 # a route to a voice filter's Blend knob moves it live (engine destinations Filter Blend / Filter 2 Blend) instead of at its mean
LFO_BLEND_MEAN = True                                                                   # an LFO route to the Blend knob at the LFO's mean (2 presets: Destruction 8.6 to 8.1)
FILTER_BLEND_DROP_DB = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0}                                  # how far the low-pass / high-pass passband falls at full resonance (engine flt_vbg). Measured per model (Analog 10 dB, Dirty 7.4, Ladder 11, Digital 2: calibration/vital_vfilter_probe.py at res 0.5, taken as res^0.9) but it made the A/B scores worse overall (92 presets: 7.42 without, 7.46 with half, 7.55 with all of it), so it is off
ROUTE_CURVES = True


def static_source_value(s: dict, src: str):
    """The value of a route's source when it does not move during a note: a macro at its saved position, velocity at the nominal value, and the mod wheel at the position saved in the preset (the plugin loads it:
    Moving Harmonics at wheel 1 renders 12 dB louder than at 0). None for a source that moves (LFOs, envelopes, random...)."""
    if src.startswith("macro_control_"):
        return float(s.get(src, 0.0))
    if src == "velocity":
        return VELOCITY_NOMINAL
    if src == "mod_wheel":
        return float(s.get("mod_wheel", 0.0))
    if src.startswith("random_") and RANDOM_AS_MEAN:
        return 0.5                                           # a random modulator (0..1, a new value each time) cannot be replayed: its average, which is the middle of its range
    if src.startswith("const:"):
        return float(src[6:])                                # a static source already passed through its route's curve (route_static_value)
    return None


def line_value(line: dict, x: float) -> float:
    """Vital's LineGenerator at phase x (line_generator.cpp render): the value is 1 - y, each segment eased by Smooth and bent by its power."""
    pts = line.get("points") or [0.0, 1.0, 1.0, 0.0]
    xs, ys = pts[0::2], pts[1::2]
    pw = line.get("powers") or [0.0] * len(xs)
    x = CLAMP(x, 0.0, 1.0)
    if x <= xs[0]:
        return 1.0 - ys[0]
    for j in range(len(xs) - 1):
        if xs[j] <= x <= xs[j + 1]:
            w = xs[j + 1] - xs[j]
            if w <= 0.0:
                return 1.0 - ys[j + 1]
            t = (x - xs[j]) / w
            if line.get("smooth"):
                t = 0.5 * math.sin((t - 0.5) * math.pi) + 0.5
            p = pw[j] if j < len(pw) else 0.0
            if abs(p) > 1e-6:
                t = (math.exp(p * t) - 1.0) / (math.exp(p) - 1.0)
            return 1.0 - (ys[j] + CLAMP(t, 0.0, 1.0) * (ys[j + 1] - ys[j]))
    return 1.0 - ys[-1]


def route_shaped(s: dict, i: int) -> bool:
    """Whether route i (0-based) bends its source: a curve other than the identity line (ModulationConnectionProcessor uses the map unless LineGenerator::linear()), or a power."""
    lm = (s.get("modulations") or [{}] * (i + 1))[i].get("line_mapping")
    lin = (not lm or (not lm.get("smooth") and lm.get("num_points", 2) == 2 and not (lm.get("powers") or [0.0])[0]
                      and list(lm.get("points", [0.0, 1.0, 1.0, 0.0]))[:4] == [0.0, 1.0, 1.0, 0.0]))
    return not lin or abs(float(s.get(f"modulation_{i + 1}_power", 0.0))) > 1e-6


def route_static_value(s: dict, i: int, v: float) -> float:
    """What a static source value v becomes after route i's curve and power (modulation_connection_processor.cpp: value = map(v), then shift = value * (bip + 1) - bip,
    out = sign(shift) * powerScale(|shift|, -power) * (1 - bip / 2)), given back on the 0..1 scale the route code expects (a bipolar route subtracts 0.5 from it)."""
    lm = (s.get("modulations") or [{}] * (i + 1))[i].get("line_mapping")
    bip = 1.0 if s.get(f"modulation_{i + 1}_bipolar") else 0.0
    p = -float(s.get(f"modulation_{i + 1}_power", 0.0))
    def one(x):
        val = CLAMP(line_value(lm, x), -1.0, 1.0) if lm else x
        sh = val * (bip + 1.0) - bip
        a = abs(sh)
        ps = a if abs(p) < 1e-6 else (math.exp(p * a) - 1.0) / (math.exp(p) - 1.0)
        out = math.copysign(ps, sh) * (1.0 - 0.5 * bip)
        return out + 0.5 * bip
    src = (s.get("modulations") or [{}] * (i + 1))[i].get("source", "")
    if src.startswith("random_"):
        return sum(one(k / 32.0) for k in range(33)) / 33.0     # the mean of the curve over the random value's range
    return one(v)
# Seconds per LFO cycle at 120 BPM for lfo_N_sync = 1 Tempo / 2 Dotted / 3 Triplets, from the tempo index: the same ladder the delay measured (calibration/vital_lfo_probe.py checks it)
LFO_SYNC_MULT = {1: 1.0, 2: 1.5, 3: 2.0 / 3.0}


def lfo_period_s(sync: int, tempo: float) -> float:
    return 2.0 ** (7 - round(tempo)) * LFO_SYNC_MULT.get(sync, 1.0)


LFO_LINE_SMOOTH = True


def lfo_shape(lfo: dict, n: int = 16) -> list[int]:
    """Vital's LFO line as the engine's n points (0..127) of one cycle (16 for the shape table, 64 for its Hi-Res one): the value is 1 - y, each segment bent by its power."""
    pts = lfo.get("points") or [0.0, 1.0, 1.0, 1.0]
    xs, ys = pts[0::2], pts[1::2]
    pw = lfo.get("powers") or [0.0] * len(xs)
    out = []
    for i in range(n):
        x = i / float(n)
        y = ys[-1]
        for j in range(len(xs) - 1):
            if xs[j] <= x < xs[j + 1] or (x >= xs[-1] and j == len(xs) - 2):
                t = (x - xs[j]) / (xs[j + 1] - xs[j])
                p = pw[j] if j < len(pw) else 0.0
                if lfo.get("smooth") and LFO_LINE_SMOOTH:                                   # the line's Smooth switch eases every segment (LineGenerator::smoothTransition: a half sine) before its power bends it
                    t = 0.5 * math.sin((t - 0.5) * math.pi) + 0.5
                if abs(p) > 1e-3:
                    t = (math.exp(p * t) - 1.0) / (math.exp(p) - 1.0)
                y = ys[j] + (ys[j + 1] - ys[j]) * t
                break
        out.append(pot(127 * (1.0 - y)))
    return out


def dest_spec(name: str, s: dict, P: dict, main_filter: int) -> dict | None:
    """What a Vital destination is in the engine: `lr` the modulation destination, `x` its route amount per unit of Vital's amount (Vital's amount is a
    fraction of the control's whole range), `key` the pot a macro or a static route moves and `pot` how far per unit of amount, `lo`/`hi` its range."""
    import re
    m = re.fullmatch(r"osc_([123])_distortion_amount", name)
    if m and s.get(f"osc_{m.group(1)}_on") and int(s.get(f"osc_{m.group(1)}_distortion_type", 0)) in (7, 8, 10, 11):
        n = int(m.group(1))                                                       # FM / RM by another oscillator: the amount is the engine's warp amount (0..1 over the xa pot's 0..1000)
        return {"lr": 45 + n, "x": 1.0, "key": f"wt{n}_xa", "pot": 1000.0, "lo": 0, "hi": 1000}
    m = re.fullmatch(r"osc_([123])_(wave_frame|level|pan|transpose|tune)", name)
    if m:
        n, what = int(m.group(1)), m.group(2)
        if not s.get(f"osc_{n}_on"):
            return None
        if what == "wave_frame":
            return {"lr": LR_DST[f"wt{n}_pos"], "x": 1.0, "key": f"wt{n}_pos", "pot": 127.0, "lo": 0, "hi": 127}
        if what == "level":
            return {"lr": LR_DST[f"wt{n}_level"], "x": 1.0, "key": f"wt{n}_level", "pot": 127.0, "lo": 0, "hi": 127}
        if what == "pan":
            return {"lr": LR_DST.get(f"wt{n}_pan"), "x": 2.0, "key": f"wt{n}_pan", "pot": 126.0, "lo": 0, "hi": 127}
        if what == "transpose":
            return {"lr": LR_DST[f"wt{n}_pitch"], "x": 96.0 / 48.0, "key": f"wt{n}_tune", "pot": 96.0, "lo": -72, "hi": 72}
        return {"lr": LR_DST[f"wt{n}_fine"], "x": 1.0, "key": None, "pot": 0.0, "lo": 0, "hi": 0}            # Vital's osc Tune spans 2 st, the engine's Fine destination moves 2 st per unit: a vibrato of cents keeps its depth
    if name == "sample_level" and s.get("sample_on"):
        return {"lr": 39, "x": 1.0, "key": "smp_level", "pot": 127.0, "lo": 0, "hi": 127}       # the engine's "Smp1 Level" destination
    mb = re.fullmatch(r"filter_([12])_blend", name)
    if mb and VITAL_BLEND_LIVE and s.get(f"filter_{mb.group(1)}_on") and int(s.get(f"filter_{mb.group(1)}_model", 0)) in FILTER_BLEND_MODELS + (6, 7):
        fi, mdl = int(mb.group(1)), int(s[f"filter_{mb.group(1)}_model"])
        key = ("cblend" if fi == 1 else "cblend2") if mdl == 6 else ("flt_blend" if fi == 1 else "flt2_blend")
        return {"lr": 63 + fi, "x": 1.0, "key": key, "pot": 127.0, "lo": 0, "hi": 127}                 # the engine's Filter Blend / Filter 2 Blend destinations: a fraction of the knob's 0..2 range
    if s.get("filter_1_on"):
        if name == "filter_1_cutoff":
            return {"lr": LR_DST["cutoff"], "x": 128.0 / d2m.FILTER_WIDTH, "key": "flt_freq", "pot": 128.0, "lo": 0, "hi": 127}
        if name == "filter_1_resonance":
            return {"lr": LR_DST["res"], "x": 1.0, "key": "flt_res", "pot": 127.0, "lo": 0, "hi": 127}
    if s.get("filter_2_on") and name == "filter_2_cutoff":
        return {"lr": 23, "x": 128.0 / d2m.FILTER_WIDTH, "key": "flt2_freq", "pot": 128.0, "lo": 0, "hi": 127}
    if s.get("filter_2_on") and name == "filter_2_resonance":
        return {"lr": None, "x": 0.0, "key": "flt2_res", "pot": 127.0, "lo": 0, "hi": 127}
    if s.get("filter_fx_on"):
        if name == "filter_fx_cutoff":
            return {"lr": 24, "x": 128.0 / d2m.FILTER_WIDTH, "key": "fxf_freq", "pot": 128.0, "lo": 0, "hi": 127}
        if name == "filter_fx_mix":
            return {"lr": 42, "x": 1.0, "key": "fxf_wet", "pot": 127.0, "lo": 0, "hi": 127}
        if name == "filter_fx_resonance":
            return {"lr": None, "x": 0.0, "key": "fxf_res", "pot": 127.0, "lo": 0, "hi": 127}
    if s.get("distortion_on") and name in ("distortion_drive", "distortion_filter_cutoff", "distortion_filter_resonance", "distortion_filter_blend"):
        key = {"distortion_drive": "dist_drive", "distortion_filter_cutoff": "dist_flt_cut", "distortion_filter_resonance": "dist_flt_res", "distortion_filter_blend": "dist_flt_blend"}[name]
        lr = {"distortion_drive": 57, "distortion_filter_cutoff": 58, "distortion_filter_resonance": 59, "distortion_filter_blend": 60}[name]      # the engine's Dist Drive / Flt Cut / Flt Res / Flt Blend destinations (a fraction of the control's range each)
        if name != "distortion_drive" and int(s.get("distortion_filter_order", 0)) not in (1, 2):
            return None
        return {"lr": lr, "x": 1.0, "key": key, "pot": 127.0, "lo": 0, "hi": 127}
    if name == "distortion_mix" and s.get("distortion_on"):
        return {"lr": 25, "x": 1.0, "key": "dist_mix", "pot": 127.0, "lo": 0, "hi": 127}       # the engine's "Dist Wet" (Vital's mix is a linear crossfade, a route adds to the stored value)
    if name == "compressor_mix" and s.get("compressor_on"):
        return {"lr": 26, "x": 1.0, "key": "comp_mix", "pot": 127.0, "lo": 0, "hi": 127}       # "Comp Wet"
    if name in FX_MIX_KEYS and s.get(FX_MIX_KEYS[name][0]):
        return {"lr": None, "x": 0.0, "key": FX_MIX_KEYS[name][1], "pot": 127.0, "lo": 0, "hi": 127}
    return None


def fold_meta_modulation(s: dict) -> tuple[list[float], set[int]]:
    """Routes whose destination is another route's amount ("modulation_N_amount"). Measured (calibration/vital_route_probe.py + work/meta_probe.py):
    the target's amount moves by 2 * meta amount * source (the amount control spans -1..1), a bipolar meta by 2 * amount * (source - 0.5).
    Static sources (a macro at its saved value, velocity at the nominal value) are folded into the target's amount here; the folded meta routes
    are returned by index and left out of the route list. Other sources (LFO, envelope, mod wheel, note...) stay dropped."""
    mods = s.get("modulations", [])
    base = [float(s.get(f"modulation_{i + 1}_amount", 0.0)) for i in range(len(mods))]
    metas = []
    for j, m in enumerate(mods):
        dest, src = m.get("destination", ""), m.get("source", "")
        if not src or s.get(f"modulation_{j + 1}_bypass") or not (dest.startswith("modulation_") and dest.endswith("_amount")):
            continue
        try:
            n = int(dest[len("modulation_"):-len("_amount")]) - 1
        except ValueError:
            continue
        if not 0 <= n < len(mods):
            continue
        v = static_source_value(s, src)
        if v is None:
            continue
        if route_shaped(s, j):
            v = route_static_value(s, j, v)
        metas.append((j, n, v - (0.5 if s.get(f"modulation_{j + 1}_bipolar") else 0.0)))
    amt = list(base)
    for _ in range(4):                                                  # a meta route can itself be scaled by another: iterate to a fixed point
        nxt = list(base)
        for j, n, v in metas:
            nxt[n] += 2.0 * amt[j] * v
        amt = nxt
    return amt, {j for j, _, _ in metas}


def keytrack_audio_hz(s: dict, k: int) -> float:
    """Vital's keytracked LFO (sync 4) runs at the note's own frequency (+ transpose + tune; TempoChooser, operators.cpp): the rate for middle C, or 0 when the LFO is not keytracked."""
    if int(s.get(f"lfo_{k}_sync", 0)) != 4:
        return 0.0
    return 440.0 * 2.0 ** ((60.0 + s.get(f"lfo_{k}_keytrack_transpose", 0.0) + s.get(f"lfo_{k}_keytrack_tune", 0.0) - 69.0) / 12.0)


AUDIO_DIST_DST = {"dist_drive": 0, "dist_flt_cut": 1, "dist_flt_res": 2, "dist_flt_blend": 3}


# the engine's cutoff destinations (filter 1, filter 2, Filter FX, distortion filter): an envelope route there sweeps the whole audio band, which a route of the same size to a wavetable position or a
# drive does not, so they come first when the routes outnumber the engine's two spare envelopes (Railgun: env 3 opens its distortion's low-pass from 8 st, and without it the zap plays dark)
CUTOFF_DST = {13, 23, 24, 58}
ENV_CUTOFF_WEIGHT = 2.0
LAST_DROPPED_ENV: list = []                 # (vital envelope, engine destination index) of the routes the last map_modulation left out for want of a spare envelope (diagnostics: work/ceil_env.py)


def map_modulation(s: dict, P: dict, routes: list, main_filter: int, notes: list[str]) -> dict:
    """The routes that are not the cutoff envelope: LFOs into the engine's four LFOs and eight routes, envelopes 1-6 into the two spare
    envelopes, macros into the engine's macros (moving the same pots, fixed offsets folded into the base), velocity folded at velocity 100."""
    LAST_DROPPED_ENV.clear()
    fold: dict[str, float] = {}
    lr: list[tuple[float, int, int, float, bool]] = []                  # (|x|, vital lfo, dst, x, bipolar)
    me: list[tuple[float, int, int, float, str]] = []                        # (|x|, vital env, dst, x)
    rn: list[tuple[float, int, int, float, bool]] = []                  # (|x|, vital random, dst, x, bipolar)
    mac: list[tuple[int, str, float]] = []
    dropped: dict[str, int] = {}
    macro_vals = [float(s.get(f"macro_control_{i}", 0.0)) for i in range(1, 5)]
    for src, dest, a, bip in routes:                                    # a macro is itself a destination: velocity (at its nominal value), the mod wheel and another macro at its saved position add to it (clamped to 0..1)
        if dest.startswith("macro_control_") and (v := static_source_value(s, src)) is not None:
            k = int(dest[len("macro_control_"):]) - 1
            macro_vals[k] = CLAMP(macro_vals[k] + a * ((v - 0.5) if bip else v), 0.0, 1.0)
    audio: dict[int, list[float]] = {}                                  # audio-rate keytracked LFO k -> its four distortion route amounts (Drive, Cutoff, Res, Blend)

    def drop(why: str) -> None:
        dropped[why] = dropped.get(why, 0) + 1

    for src, dest, a, bip in routes:
        if dest.startswith("macro_control_") and static_source_value(s, src) is not None:
            continue                                                    # folded into the macro's value above
        if dest.endswith(("_spectral_morph_amount", "_distortion_amount")) and static_source_value(s, src) is not None:
            continue                                                    # baked into the oscillator's table at the saved value (static_route_offset)
        spec = dest_spec(dest, s, P, main_filter)
        if spec is None:
            drop(f"{src.rstrip('0123456789_')}->{dest}")
            continue
        x = a * spec["x"] * (0.5 if bip else 1.0)                        # measured (calibration/vital_route_probe.py): a bipolar route adds amount * (value - 0.5), the engine's bipolar flag gives amount * (2 * value - 1)
        if src.startswith("random_") and RANDOM_LIVE and spec["lr"] is not None and not dest.endswith("_tempo"):
            rn.append((abs(a * spec["x"] * (0.5 if bip else 1.0)), int(src[7:]), spec["lr"], a * spec["x"] * (0.5 if bip else 1.0), bip))        # a live route (a modulation destination the engine has): not folded in at its mean
            continue
        if src.startswith("lfo_") and spec["key"] in AUDIO_DIST_DST and keytrack_audio_hz(s, int(src[4:])) > 30.0:
            # a keyboard-following LFO is at audio rate, which only the distortion's own audio-rate LFO can follow: the route goes there (a bipolar one as twice the amount on the unipolar value, less a fixed half)
            k = int(src[4:])
            amts = audio.setdefault(k, [0.0, 0.0, 0.0, 0.0])
            amts[AUDIO_DIST_DST[spec["key"]]] += x * (2.0 if bip else 1.0)
            if bip:
                fold[spec["key"]] = fold.get(spec["key"], 0.0) - x * spec["pot"]
            continue
        if src.startswith("lfo_"):
            if spec["lr"] is None:
                drop(f"lfo->{dest}")
            else:
                lr.append((abs(x), int(src[4:]), spec["lr"], x, bip))
        elif src.startswith("env_"):
            if spec["lr"] is None:
                drop(f"env->{dest}")
            else:
                if bip and spec["key"]:                                  # a bipolar envelope is amount * (env - 0.5): the -0.5 part is a fixed offset
                    fold[spec["key"]] = fold.get(spec["key"], 0.0) - 0.5 * a * spec["pot"]
                me.append((abs(x) * (ENV_CUTOFF_WEIGHT if spec["lr"] in CUTOFF_DST else 1.0), int(src[4:]), spec["lr"], a * spec["x"], dest))
        elif src.startswith("macro_control_"):
            k = int(src[len("macro_control_"):])
            if spec["key"] is None:
                drop(f"macro->{dest}")
                continue
            v0 = macro_vals[k - 1]
            fold[spec["key"]] = fold.get(spec["key"], 0.0) + a * spec["pot"] * ((v0 - 0.5) if bip else v0)
            mac.append((k, spec["key"], a * spec["pot"], spec["lo"], spec["hi"]))
        elif src in ("velocity", "mod_wheel") or src.startswith("const:"):
            if spec["key"] is None:
                drop(f"{src}->{dest}")
                continue
            v = static_source_value(s, src)
            fold[spec["key"]] = fold.get(spec["key"], 0.0) + a * spec["pot"] * ((v - 0.5) if bip else v)
        else:
            drop(f"{src.rstrip('0123456789_')}->{dest}")

    # static offsets (macros at their saved positions, velocity 100) go into the base values
    ranges = {}
    for spec_name in ("wave_frame", "level", "pan", "transpose"):
        pass
    for key, off in fold.items():
        lo, hi = (-72, 72) if key.endswith("_tune") else ((0, 1000) if key.endswith("_xa") else (0, 127))
        P[key] = int(round(CLAMP(P.get(key, 0) + off, lo, hi)))

    if audio:
        k = max(audio, key=lambda j: sum(abs(v) for v in audio[j]))     # one audio-rate unit: the strongest keytracked LFO; the others' distortion routes are lost
        for j in audio:
            if j != k:
                drop("audio-rate lfo->distortion (only one carried)")
        P["vdl_on"] = 1
        P["vdl_trans"] = pot(64 + CLAMP(s.get(f"lfo_{k}_keytrack_transpose", 0.0) + s.get(f"lfo_{k}_keytrack_tune", 0.0), -48.0, 48.0) / 0.75)
        for name, v in zip(("vdl_drive", "vdl_cut", "vdl_res", "vdl_blend"), audio[k]):
            P[name] = pot(64 + 63 * CLAMP(v, -1, 1))
            if abs(v) > 1.0:
                notes.append(f"audio-rate LFO distortion route {v:.2f} is past the engine's full range: limited")
        for j, v in enumerate(lfo_shape(s["lfos"][k - 1])):
            P[f"vdl_s{j}"] = v
        notes.append(f"LFO {k} follows the keyboard at an audio rate ({keytrack_audio_hz(s, k):.0f} Hz at middle C): its distortion routes run in the distortion stage at the note's pitch")
    # LFOs: up to four, ranked by the strength of their routes
    strength: dict[int, float] = {}
    for w, k, *_ in lr:
        strength[k] = strength.get(k, 0.0) + w
    chosen = sorted(strength, key=lambda k: -strength[k])[:4]
    for slot, k in enumerate(chosen, 1):
        lf = s["lfos"][k - 1]
        sync = int(s.get(f"lfo_{k}_sync", 0))
        keyflat = False
        if sync in (1, 2, 3):
            hz = 1.0 / lfo_period_s(sync, s.get(f"lfo_{k}_tempo", 4.0))
            P[f"lfo{slot}_tempo"] = 1                  # the rate is the one at 120 BPM; the engine scales it by the host tempo
            notes.append(f"LFO {k} is tempo-synced: it follows the host tempo")
        else:
            hz = 2.0 ** s.get(f"lfo_{k}_frequency", 0.0)
            if sync == 4:
                # Vital's keytracked LFO runs at the note's own frequency (+ transpose + tune; TempoChooser, operators.cpp): audio rate for a normal note, which the engine's block-rate LFOs cannot do
                hz_key = keytrack_audio_hz(s, k)
                if hz_key > 30.0:
                    keyflat = True
                    notes.append(f"LFO {k} follows the keyboard at an audio rate ({hz_key:.0f} Hz at middle C): its other routes are carried as its average level, the grit of the fast modulation is not reproduced")
                else:
                    hz = hz_key
                    notes.append(f"LFO {k} follows the keyboard: carried at its rate for middle C ({hz_key:.1f} Hz)")
        P[f"lfo{slot}_rate"] = pot(127.0 * math.log(CLAMP(hz, 0.05, 120.0) / 0.05) / math.log(2400.0))
        mode = int(s.get(f"lfo_{k}_sync_type", 0))
        P[f"lfo{slot}_mode"] = 1 if mode == 1 else (2 if mode in (2, 3) else 0)
        shape = lfo_shape(lf)
        hi = lfo_shape(lf, 64)                                                           # the 64-point shape the engine reads instead (a gate's or a step's narrow edges survive it)
        if keyflat:
            shape = [int(round(float(np.mean(shape))))] * len(shape)
            hi = [int(round(float(np.mean(hi))))] * len(hi)
        for j, v in enumerate(shape):
            P[f"lfo{slot}_s{j}"] = v
        P[f"lfo{slot}_hr"] = 1
        for j, v in enumerate(hi):
            P[f"lfo{slot}_h{j}"] = v
        P[f"lfo{slot}_smooth"] = 0
        # Vital's LFO Smooth (synth_lfo.cpp: a one-pole low-pass with half life 0.2 * 2^smooth_time seconds, so a time constant of 0.2885 * 2^smooth_time), Delay, Fade In and Phase. Fade In is
        # skipped by Vital while Smooth is on (the one-pole replaces the fade blend); the engine's smoother has the same law as Serum's, 2 ms to 0.7 s.
        P["lfo_vlaw"] = 1
        smooth_on = bool(s.get(f"lfo_{k}_smooth_mode", 1.0))
        tau = 0.2885 * 2.0 ** s.get(f"lfo_{k}_smooth_time", -7.5)
        if smooth_on and tau >= 0.0025:
            P[f"lfo{slot}_smooth"] = pot(max(1.0, 127.0 * math.log(tau / 0.002) / math.log(350.0)))
            if tau > 0.7:
                notes.append(f"LFO {k} smoothing {tau:.1f} s is past the engine's 0.7 s: shortened")
        if s.get(f"lfo_{k}_delay_time", 0.0) > 0:
            P[f"lfo{slot}_delay"] = pot(127.0 * CLAMP(s[f"lfo_{k}_delay_time"] / 4.0, 0.0, 1.0))
        if s.get(f"lfo_{k}_fade_time", 0.0) > 0 and not smooth_on:
            P[f"lfo{slot}_fade"] = pot(127.0 * CLAMP(s[f"lfo_{k}_fade_time"] / 8.0, 0.0, 1.0))
        if abs(s.get(f"lfo_{k}_phase", 0.0)) > 1e-3:
            P[f"lfo{slot}_phase"] = pot(127.0 * CLAMP(s[f"lfo_{k}_phase"], 0.0, 1.0))
    slot_of = {k: i for i, k in enumerate(chosen, 1)}
    for w, k, dst, x, bip in lr:
        if k not in slot_of:
            drop("lfo routes past the engine's four LFOs")
    # Vital's random modulators: a generator each (style, rate, tempo), a new value for every note, carried as route sources 5..8
    for k in sorted({r[1] for r in rn}):
        P[f"rn{k}_style"] = int(CLAMP(int(s.get(f"random_{k}_style", 0)), 0, 3))
        sync = int(s.get(f"random_{k}_sync", 0))
        if sync in (1, 2, 3):
            hz = 1.0 / lfo_period_s(sync, s.get(f"random_{k}_tempo", 4.0))
            P[f"rn{k}_tempo"] = 1
        else:
            hz = 2.0 ** s.get(f"random_{k}_frequency", 1.0)
            if sync == 4:
                notes.append(f"random {k} follows the keyboard: carried at its free rate")
        P[f"rn{k}_rate"] = pot(127.0 * math.log(CLAMP(hz, 0.05, 120.0) / 0.05) / math.log(2400.0))
        if int(s.get(f"random_{k}_sync_type", 0)):
            notes.append(f"random {k} is synced to the song position: it draws a new value per note instead")
    combined = [(w, slot_of[k], dst, x, bip) for w, k, dst, x, bip in lr if k in slot_of] + [(w, 4 + k, dst, x, bip) for w, k, dst, x, bip in rn]
    combined.sort(key=lambda r: -r[0])
    for n, (w, src_slot, dst, x, bip) in enumerate(combined[:8], 1):
        P[f"lr{n}_src"] = src_slot
        P[f"lr{n}_dst"] = dst
        P[f"lr{n}_amt"] = pot(64 + 63 * CLAMP(x, -1, 1))
        P[f"lr{n}_bip"] = 1 if bip else 0
        if abs(x) > 1.0:
            notes.append(f"LFO route amount {x:.2f} is past the engine's full range: limited")
    for w, src_slot, dst, x, bip in combined[8:]:
        drop("lfo routes past the engine's 8")
    # envelope routes: the two strongest onto the engine's spare envelopes
    me.sort(key=lambda r: -r[0])
    for n, (w, k, dst, x, _dn) in enumerate(me[:2], 1):
        P[f"me{n}_dst"] = dst
        P[f"me{n}_amt"] = pot(64 + 63 * CLAMP(x, -1, 1))
        t4 = lambda key: env_seconds(s, key)
        P[f"me{n}_a"] = d2m.pot_attack(t4(f"env_{k}_attack"))
        if attack_curve_pot(s, k):
            P[f"me{n}_apow"] = attack_curve_pot(s, k)
        hp, dsec = env_hold_decay(s, k)
        P[f"me{n}_d"] = d2m.pot_time(dsec)
        P[f"me{n}_s"] = pot(127 * CLAMP(s[f"env_{k}_sustain"], 0, 1))
        P[f"me{n}_r"] = d2m.pot_time(t4(f"env_{k}_release"))
        P[f"me{n}_delay"], P[f"me{n}_hold"] = env_stage_pot(s, k, "delay"), hp
    for w, k, dst, x, dn in me[2:]:
        drop("envelope routes past the engine's 2")
        LAST_DROPPED_ENV.append((k, dn))
    # macros: the engine's macro knobs hold the saved positions and move the same pots
    for k in range(4):
        P[f"mac{k + 1}"] = pot(127 * macro_vals[k])
    names = [""] * 8
    routes_out = []
    for k, key, travel, lo, hi in sorted(mac, key=lambda r: -abs(r[2]))[:d2m.MAX_MACRO_ROUTES]:
        routes_out.append((k, key, travel))
    for k, key, travel in routes_out:
        names[k - 1] = d2m._macro_name(s.get(f"macro{k}", "") or f"Macro {k}") or f"Macro {k}"
    if len(mac) > d2m.MAX_MACRO_ROUTES:
        drop("macro routes past the engine's limit")
    if routes_out:
        notes.append("macros: " + "; ".join(f"{names[k - 1]} moves {', '.join(sorted({key for mk, key, _ in routes_out if mk == k}))}" for k in sorted({r[0] for r in routes_out})))
    if dropped:
        notes.append("not carried: " + ", ".join(f"{n} x{c}" for n, c in sorted(dropped.items(), key=lambda t: -t[1])[:10]))
    return {"routes": sorted(routes_out), "names": names, "stored": macro_vals}


# ---------------------------------------------------------------------------
# The mapper
# ---------------------------------------------------------------------------
# --- the sample oscillator (measured with calibration/vital_sample_probe.py: a 100 Hz sine sample at root note 60 plays at its own pitch there, one octave per twelve notes with key
# tracking on and at its own pitch everywhere with it off; Transpose is in semitones and Tune in semitones too (-1..1); the level is squared, -10.15 dB at 1.0 on a 0.5-amplitude sine; Loop plays
# the whole sample round, Bounce goes forward and back, with Loop off it plays once)
VITAL_SMP_SCALE = 0.95             # engine pot per Vital level pot, from calibration/vital_sample_ab.py


def vital_sample_audio(sm: dict) -> tuple[np.ndarray, float] | None:
    import base64
    try:
        a = np.frombuffer(base64.b64decode(sm["samples"]), "<i2").astype(np.float32) / 32768.0
        b = np.frombuffer(base64.b64decode(sm.get("samples_stereo", sm["samples"])), "<i2").astype(np.float32) / 32768.0
    except Exception:
        return None
    n = min(len(a), len(b), int(sm.get("length", len(a))))
    if n < 2:
        return None
    audio = np.stack([a[:n], b[:n]], axis=1)
    if np.array_equal(audio[:, 0], audio[:, 1]):
        audio = audio[:, :1]
    return audio, float(sm.get("sample_rate", 44100))


def sample_oscillator(s: dict, P: dict, wt_root: Path, subdir: str, notes: list[str]) -> None:
    """Vital's one sample oscillator as the engine's first sample slot: the audio is written as a one-region set (root note 60, looped whole when Loop is on)."""
    level = float(s.get("sample_level", 0.0))
    routed = any(m.get("destination") == "sample_level" and abs(s.get(f"modulation_{i + 1}_amount", 0)) > 1e-6 for i, m in enumerate(s.get("modulations", [])))
    if level < 0.005 and not routed:
        return                                                      # a sample that is on at level 0 and never raised is silent
    got = vital_sample_audio(s.get("sample", {}))
    if got is None:
        notes.append("sample oscillator: no audio in the preset")
        return
    audio, rate = got
    loop = (0.0, 1.0) if s.get("sample_loop") else None
    ping = bool(s.get("sample_bounce")) and loop is not None
    region = SE._region(audio, rate, 0, 127, 60, loop, 0.0, ping_pong=ping)
    digest = hashlib.md5(region).hexdigest()[:12]
    dest = wt_root.parent / "Samples" / subdir / f"vital_{digest}.tbs"
    SE.write_set([region], dest)
    P["smp_set"] = f"{d2m.MOVE_SAMPLE_ROOT}/{subdir}/{dest.name}"
    P["smp_level"] = pot(127 * level * VITAL_SMP_SCALE)
    P["smp_fixed"] = 0 if s.get("sample_keytrack") else 1
    vshift = float(s.get("voice_transpose", 0.0)) + float(s.get("voice_tune", 0.0))
    P["smp_tune"] = int(round(CLAMP(float(s.get("sample_transpose", 0.0)) + math.floor(vshift + 0.5), -48, 48)))
    P["smp_fine"] = int(round(CLAMP(100.0 * (float(s.get("sample_tune", 0.0)) + vshift - math.floor(vshift + 0.5)), -100, 100)))
    dst = int(s.get("sample_destination", 3))
    P["smp_post"] = {3: 1, 4: 3}.get(dst, 0)
    P["smp_bal"] = {0: 0, 1: 127, 2: 64}.get(dst, 0)
    P["smp_pan"] = int(round(CLAMP(50.0 * float(s.get("sample_pan", 0.0)), -50, 50)))
    if s.get("sample_random_phase"):
        notes.append("sample oscillator: random start phase not carried (starts at the beginning)")
    if abs(s.get("sample_transpose_quantize", 0)) > 1e-6:
        notes.append("sample oscillator: transpose quantize not carried")



# The engine's decay and release (measured with the amp envelope on a sine, calibration/vital_env_probe.py) are a power curve of about -4.9 that ends exactly at the set time, as Vital's are with its own power
ENGINE_FALL_POWER = -4.9
_FALL_CACHE: dict = {}


def _power_scale(x: np.ndarray, p: float) -> np.ndarray:
    return x if abs(p) < 0.01 else (np.exp(p * x) - 1.0) / (np.exp(p) - 1.0)


def env_fall_factor(power: float, sustain: float, squared: bool) -> float:
    """How many times Vital's decay or release time the engine's must be set to fall like Vital's does. Vital's stage goes from 1 to the sustain along a power curve and ends exactly at its time; the engine's is one of
    a different power, so the factor is the best fit over the stage: in dB for the amp envelope (Vital squares it into the gain, which the engine's sustain and fall are not) and linearly for the rest."""
    key = (round(power, 2), round(sustain, 2), squared)
    if key not in _FALL_CACHE:
        x = np.linspace(0.0, 1.0, 400)
        v = 1.0 - (1.0 - sustain) * _power_scale(x, power)
        target = v * v if squared else v
        floor = sustain * sustain if squared else sustain
        best, best_err = 1.0, 1e30
        for f in np.linspace(0.3, 6.0, 286):
            u = x / f
            eng = floor + (1.0 - floor) * np.where(u < 1.0, 1.0 - _power_scale(np.minimum(u, 1.0), ENGINE_FALL_POWER), 0.0)
            if squared:
                err = ((20 * np.log10(np.maximum(target, 1e-3)) - 20 * np.log10(np.maximum(eng, 1e-3))) ** 2).mean()
            else:
                err = ((target - eng) ** 2).mean()
            if err < best_err:
                best, best_err = float(f), err
        _FALL_CACHE[key] = best
    return _FALL_CACHE[key]


ENV_HOLD_FIT = True
_HOLD_CACHE: dict = {}


def env_hold_decay(s: dict, k: int) -> tuple[int, float]:
    """The engine's Hold pot and decay seconds for Vital envelope k on an engine envelope that keeps its analog decay (the spare and filter envelopes). A Vital decay power well above 0 holds near 1
    for most of the stage and then falls fast, which no stretch of the engine's fall can draw (Railgun's env 3, power 6.6: the old fit hit its 6x limit); when Vital's own Hold is 0, the flat part goes
    into the engine's Hold and the fall is fitted to the rest (least squares on the value, over the whole stage). Gives the plain env_seconds fit when that is as good."""
    hold_pot = env_stage_pot(s, k, "hold")
    dec = env_seconds(s, f"env_{k}_decay")
    if not ENV_HOLD_FIT or hold_pot or float(s.get(f"env_{k}_hold", 0.0)) > 0:
        return hold_pot, dec
    T = max(float(s[f"env_{k}_decay"]), 0.0) ** 4
    power, sustain = float(s.get(f"env_{k}_decay_power", 0.0)), CLAMP(float(s.get(f"env_{k}_sustain", 0.0)), 0.0, 1.0)
    key = (round(power, 2), round(sustain, 2))
    if key not in _HOLD_CACHE:
        x = np.linspace(0.0, 1.5, 600)
        target = np.where(x < 1.0, 1.0 - (1.0 - sustain) * _power_scale(np.minimum(x, 1.0), power), sustain)
        best, err0 = (1e30, 0.0, 1.0), 1e30
        for hf in np.linspace(0.0, 0.95, 39):
            for f in np.geomspace(0.05, 6.0, 120):
                u = np.maximum(x - hf, 0.0) / f
                eng = sustain + (1.0 - sustain) * np.where(u < 1.0, 1.0 - _power_scale(np.minimum(u, 1.0), ENGINE_FALL_POWER), 0.0)
                err = float(((target - eng) ** 2).mean())
                if err < best[0] - 1e-9:
                    best = (err, float(hf), float(f))
            if hf == 0.0:
                err0 = best[0]
        _HOLD_CACHE[key] = best[1:] if best[0] < 0.5 * err0 else (0.0, 1.0)                # only when the hold at least halves the error: a near-exponential decay keeps the plain fit
    hf, f = _HOLD_CACHE[key]
    if hf <= 0.0 or T <= 0.0:
        return hold_pot, dec
    return pot(127.0 * (hf * T / 4.0) ** 0.25), f * T


def env_seconds(s: dict, key: str, squared: bool = False) -> float:
    """An envelope time in seconds (the stored value to the 4th), with Vital's decay and release shapes translated to the engine's (see env_fall_factor)."""
    sec = max(float(s[key]), 0.0) ** 4
    if key.endswith(("_decay", "_release")):
        n = key.split("_")[1]
        sustain = float(s.get(f"env_{n}_sustain", 0.0)) if key.endswith("_decay") else 0.0
        sec *= env_fall_factor(float(s.get(key + "_power", 0.0)), CLAMP(sustain, 0.0, 1.0), squared)
    return sec


ATTACK_CURVE = True
ATTACK_CURVE_MIN = 0.01                                                 # seconds: a shorter attack is the engine's own 3 ms floor either way


def attack_curve_pot(s: dict, k: int) -> int:
    """The engine's hidden attack-curve pot for Vital envelope k: Vital's attack stage is a power curve (env_N_attack_power; 0 is a straight line, a negative power starts slowly, a positive one rises at
    once) that ends exactly at the set time, which the engine's one-pole attack (about -15 dB after 3 % of the time, a power of about +6) cannot draw (calibration/vital_attack_probe.py).
    0 keeps the analog attack: used when the attack is too short to hear."""
    if not ATTACK_CURVE or max(float(s.get(f"env_{k}_attack", 0.0)), 0.0) ** 4 < ATTACK_CURVE_MIN:
        return 0
    return pot(64 + CLAMP(float(s.get(f"env_{k}_attack_power", 0.0)), -20.0, 20.0) * 63.0 / 20.0)


def fold_fx_routes(s: dict, routes: list) -> int:
    """Add the macro / velocity routes to effect controls (FX_FOLD_RANGE) into the stored values, at the macro's saved value and velocity 100; the folded routes are removed from `routes`. Returns how many."""
    n = 0
    tempo_shift: dict[str, float] = {}
    for r in list(routes):
        if r[1].endswith("_tempo") and r[0].startswith("random"):
            continue                                                    # a random modulator picks a new tempo step each note: no single step stands for it
        if r[1] in FX_FOLD_RANGE and r[1] in s and static_source_value(s, r[0]) is not None:
            lo, hi = FX_FOLD_RANGE[r[1]]
            v = static_source_value(s, r[0])
            add = r[2] * (hi - lo) * ((v - 0.5) if r[3] else v)
            if r[1].endswith("_tempo"):
                tempo_shift[r[1]] = tempo_shift.get(r[1], 0.0) + add
            else:
                s[r[1]] = CLAMP(float(s[r[1]]) + add, lo, hi)
            routes.remove(r)
            n += 1
    # a tempo index (an indexed control) moved by routes, measured through the plugin (Space Station, mod wheel 0..1 into lfo_2_tempo): the stored index is taken to the nearest step and the
    # routes' sum moves it one step every 1.0 of index, the first step coming at +0.2 (thresholds at 0.2-0.26, 1.20-1.22, 2.14-2.27 and below 3.24): index = round(base) + floor(shift + 0.8)
    for k, sh in tempo_shift.items():
        s[k] = CLAMP(float(round(float(s[k])) + math.floor(sh + 0.8)), *FX_FOLD_RANGE[k])
    return n


def filter_cutoff_note(s: dict, fi: int, voice_shift: float) -> float:
    """The filter's cutoff as the engine plays it at middle C: the voice's transpose + tune shift the note that feeds the filter's key tracking too (Jupiter Bass at note 60 with a voice transpose of -24 renders exactly as note 36 with none), so the cutoff moves by shift x key tracking."""
    return s[f"filter_{fi}_cutoff"] + voice_shift * CLAMP(float(s.get(f"filter_{fi}_keytrack", 0)), 0.0, 1.0)


def static_route_offset(s: dict, routes: list, dest: str) -> float:
    """What the routes with a static source (a macro at its saved value, velocity at the nominal value) add to a control that is baked into a table, in the control's own 0..1 units."""
    off = 0.0
    for src, d, a, bip in routes:
        if d != dest:
            continue
        v = static_source_value(s, src)
        if v is None:
            continue
        off += a * ((v - 0.5) if bip else v)
    return off


def lfo_route_mean(s: dict, routes: list, dest: str) -> float:
    """What the LFO routes to a control that the engine cannot move live add to it on average, in the control's own 0..1 units: each LFO at the mean of its shape over a cycle."""
    off = 0.0
    for src, d, a, bip in routes:
        mo = re.fullmatch(r"lfo_([1-8])", src)
        if d != dest or not mo:
            continue
        lf = (s.get("lfos") or [{}] * 8)[int(mo.group(1)) - 1]
        mean = sum(lfo_shape(lf)) / (16 * 127.0)
        off += a * ((mean - 0.5) if bip else mean)
    return off


VITAL_ENV_RANGE = {"attack": 2.37842, "decay": 2.37842, "release": 2.37842, "sustain": 1.0, "delay": 1.4142135624, "hold": 1.4142135624}      # the stored range of an envelope control (SynthParameters): times are the value ** 4 seconds


# Vital's oscillator-to-oscillator modulation sources (ProducersModule::getFirstModulationIndex / getSecondModulationIndex): the oscillator that "A" and "B" mean for each oscillator
MOD_SOURCE = {1: (2, 3), 2: (1, 3), 3: (1, 2)}
RM_AMOUNT_SCALE = 1.0                                                                          # engine RM amount per Vital RM amount; to be fitted against the plugin


def map_vital(d: dict, name: str, wt_root: Path, subdir: str = "Vital") -> dict:
    s = d["settings"]
    P = dict(d2m.TABLOR_DEFAULTS)
    paths: dict[str, str] = {"wt1_table": "Init", "wt2_table": "Init", "wt3_table": "Init"}
    carried: list[str] = []
    notes: list[str] = []
    off = lambda x: not s.get(x)

    # --- the modulation matrix: only the envelope 2 -> filter 1 cutoff route is carried so far
    amounts, folded = fold_meta_modulation(s)
    routes = []
    for i, m in enumerate(s.get("modulations", [])):
        if i in folded:
            continue
        if m.get("source") and m.get("destination") and not s.get(f"modulation_{i + 1}_bypass") and abs(amounts[i]) > 1e-6:
            src = m["source"]
            if ROUTE_CURVES and route_shaped(s, i) and (v := static_source_value(s, src)) is not None:
                src = f"const:{route_static_value(s, i, v):.6f}"           # a static source through the route's own curve / power: a fixed value
            routes.append((src, m["destination"], amounts[i], bool(s.get(f"modulation_{i + 1}_bipolar"))))
    # macros and velocity aimed at an envelope's attack / decay / release / sustain: added to the stored value (a fraction of its range; times are the value to the 4th) at the macro's saved value and velocity 100
    s = dict(s)
    env_folded = 0
    for r in list(routes):
        m = re.fullmatch(r"env_([1-6])_(delay|attack|hold|decay|release|sustain)", r[1]) or re.fullmatch(r"filter_[12]_(mix)", r[1])      # a filter's mix is a 0..1 control like a sustain
        if m and static_source_value(s, r[0]) is not None:
            key, rng = r[1], VITAL_ENV_RANGE.get(m.group(m.lastindex), 1.0)
            v = static_source_value(s, r[0])
            s[key] = CLAMP(float(s[key]) + r[2] * rng * ((v - 0.5) if r[3] else v), 0.0, rng)
            routes.remove(r)
            env_folded += 1
    fx_folded = fold_fx_routes(s, routes)
    routed = {r[1] for r in routes if static_source_value(s, r[0]) is None}          # destinations an LFO / envelope still moves: an effect they can raise from zero stays on
    if fx_folded:
        notes.append(f"{fx_folded} macro / velocity route(s) to an effect control are added at the saved macro value and velocity {int(VELOCITY_NOMINAL * 127)}, so they do not move live")
    if env_folded:
        notes.append(f"{env_folded} macro / velocity route(s) to an envelope time or sustain or a filter mix are added at the saved macro value and velocity {int(VELOCITY_NOMINAL * 127)}, so they do not move live")
    if folded:
        notes.append(f"meta-modulation: {len(folded)} route(s) that scale another route's amount are folded in at the macro's saved value / velocity {int(VELOCITY_NOMINAL * 127)}, so they do not move live")
    env_cut = [r for r in routes if r[0] == "env_2" and r[1] == "filter_1_cutoff"]
    env_cut2 = [r for r in routes if r[0] == "env_2" and r[1] == "filter_2_cutoff"]
    left = [r for r in routes if r not in env_cut and r not in env_cut2]

    # --- oscillators
    main_filter = 1 if s.get("filter_1_on") else (2 if s.get("filter_2_on") else 0)
    pd_baked: dict[int, bool] = {}
    sweep_pos: dict[int, int] = {}
    voice_shift = float(s.get("voice_transpose", 0.0)) + float(s.get("voice_tune", 0.0))      # semitones; SynthVoiceHandler adds both to the note that feeds the oscillators (and the filters' key tracking)
    for o in (1, 2, 3):
        k = f"wt{o}"
        if not s.get(f"osc_{o}_on"):
            P[f"{k}_level"] = 0
            continue
        table = render_table(s["wavetables"][o - 1], notes)
        smt = int(s.get(f"osc_{o}_spectral_morph_type", 0))
        sma = float(s.get(f"osc_{o}_spectral_morph_amount", 0.0)) + static_route_offset(s, routes, f"osc_{o}_spectral_morph_amount")
        pdt = int(s.get(f"osc_{o}_distortion_type", 0))
        pda = CLAMP(float(s.get(f"osc_{o}_distortion_amount", 0.5)) + static_route_offset(s, routes, f"osc_{o}_distortion_amount"), 0.0, 1.0)
        # a live LFO / envelope route to the distortion or morph amount on an oscillator whose frame is not moved: the amount runs along the table's frame axis (amount_sweep_table), the route drives the position
        live = {}
        if AMOUNT_SWEEP and table is not None and not any(r[1] == f"osc_{o}_wave_frame" for r in routes):
            for kind, dname, ok in (("pd", f"osc_{o}_distortion_amount", 1 <= pdt <= 6), ("morph", f"osc_{o}_spectral_morph_amount", smt in VM.KNOWN)):
                rs = [r for r in routes if r[1] == dname and r[0].startswith(("lfo_", "env_")) and abs(r[2]) > 0.02]
                if ok and rs:
                    live[kind] = rs
        if live:
            kind = max(live, key=lambda kd: sum(abs(r[2]) for r in live[kd]))
            base_amt = pda if kind == "pd" else CLAMP(sma, 0.0, 1.0)
            table = amount_sweep_table(table, CLAMP(float(s[f"osc_{o}_wave_frame"]) / 255.0 * 127.0, 0.0, 127.0), kind, smt, CLAMP(sma, 0.0, 1.0), pdt, pda)
            for lst in (routes, left):
                for r in list(lst):
                    if r in live[kind]:
                        lst[lst.index(r)] = (r[0], f"osc_{o}_wave_frame", r[2], r[3])
            sweep_pos[o] = pot(127.0 * base_amt)
            pd_baked[o] = kind == "pd" or bool(pdt and 1 <= pdt <= 6)
            notes.append(f"osc {o}: its {'distortion' if kind == 'pd' else 'morph'} amount is moved live by an LFO / envelope: carried by sweeping the amount along the table position")
        elif table is not None and smt in VM.KNOWN:
            morphed = VM.morph_table(table, smt, CLAMP(sma, 0.0, 1.0))
            if morphed is not None:
                table = morphed
                if any(r[1] == f"osc_{o}_spectral_morph_amount" for r in routes):
                    notes.append(f"osc {o}: spectral morph is baked at its saved amount (macros and velocity folded in), so a live route to it does not move it")
        if table is not None and pdt and not live:
            baked = bake_phase_distortion(table, pdt, pda)
            if baked is not None:
                table = baked
                pd_baked[o] = True
                if any(m.get("destination") == f"osc_{o}_distortion_phase" for m in s.get("modulations", [])):
                    notes.append(f"osc {o}: phase distortion is baked at its saved amount, so a route to it does not move it")
        if table is not None:
            paths[f"{k}_table"] = write_table(table, wt_root, subdir)
        else:
            notes.append(f"osc {o}: no wavetable source found, plays Init")
        P[f"{k}_pos"] = sweep_pos[o] if o in sweep_pos else pot(s[f"osc_{o}_wave_frame"] / 255.0 * 127)
        P[f"{k}_level"] = pot(127 * s[f"osc_{o}_level"])                                     # GUESS: Vital's level is also squared before the mix
        P[f"{k}_pan"] = pot(64 + 63 * s[f"osc_{o}_pan"])
        P[f"{k}_tune"] = round(float(CLAMP(s[f"osc_{o}_transpose"] + s[f"osc_{o}_tune"] + voice_shift, -72, 72)), 3)      # semitones, fractional (the loader keeps the fraction: a fine tune is -1..1 st); Vital adds the voice's transpose and tune to the note itself
        voices = int(s[f"osc_{o}_unison_voices"])
        P[f"{k}_uni"] = int(CLAMP(voices, 1, 16))
        # measured (calibration/vital_stack_probe.py): the stack's half-width in semitones is range * (detune / 10)^2, whatever the power; the power shapes the spacing of the voices, (e^(p|x|) - 1) / (e^p - 1).
        # The engine's Vital unison law reads the Detune pot as 48 * (pot / 127)^2 semitones (with decimals: a pot of 9.87 is a stored value, the knob shows it rounded) and the power from its own parameter
        # the engine's detune does not move live: an LFO route to the detune range is folded in at the LFO's mean over a cycle (Vital's range is 0..48 st, a route's amount a fraction of it). A range pushed below 0
        # spreads the stack as far the other way (measured), so its size is what counts. Space Station: LFO 1 shrinks oscillator 1's range and widens oscillator 3's.
        rng = s[f"osc_{o}_detune_range"] + 48.0 * lfo_route_mean(s, routes, f"osc_{o}_detune_range")
        semis = min(abs(rng), 48.0) * CLAMP(s[f"osc_{o}_unison_detune"] / 10.0, 0.0, 1.0) ** 2
        P["uni_vital"] = 1
        P[f"{k}_detune"] = round(127.0 * math.sqrt(CLAMP(semis, 0.0, 48.0) / 48.0), 3)
        P[f"{k}_upow"] = round(float(CLAMP(s[f"osc_{o}_detune_power"], -8.0, 8.0)), 3)
        P[f"{k}_ublend"] = round(100.0 * CLAMP(s.get(f"osc_{o}_unison_blend", 0.8), 0.0, 1.0), 2)
        P[f"{k}_ustack"] = int(CLAMP(s.get(f"osc_{o}_stack_style", 0), 0, 10)) if voices > 1 else 0
        # a voice starts at random x Random Phase of a cycle, plus the Phase knob as a constant offset (synth_oscillator.cpp reset / process): carried as the engine's Osc Phase
        P[f"{k}_rph"] = pot(127 * CLAMP(s.get(f"osc_{o}_random_phase", 1.0), 0.0, 1.0))
        P[f"{k}_phs"] = pot(127 * (s.get(f"osc_{o}_phase", 0.0) % 1.0))
        P[f"{k}_spread"] = pot(127 * s[f"osc_{o}_stereo_spread"])
        dest = int(s[f"osc_{o}_destination"])
        P[f"{k}_post"] = {3: 1, 4: 3}.get(dest, 0)                                            # Effects: past the filters, into the effects; Direct Out: past the effects as well
        P[f"{k}_bal"] = {0: 0, 1: 127, 2: 64}.get(dest, 0)                                    # the engine's balance between filter 1 (0) and filter 2 (127); Dual Filters splits it
        if dest == 2 and bool(s.get("filter_1_on")) != bool(s.get("filter_2_on")):
            P[f"{k}_bal"] = 0 if s.get("filter_1_on") else 127                                # measured: Dual with one filter off plays only the filtered half (the off filter's half is silent, not dry)
        pdt_o = int(s.get(f"osc_{o}_distortion_type", 0))
        if pdt_o in (10, 11):                                                                  # ring modulation by another oscillator: the engine's RM warp from Osc A/B/C
            src_osc = MOD_SOURCE[o][pdt_o - 10]
            P[f"{k}_xw"], P[f"{k}_xs"] = 5, src_osc                                           # engine warp 5 = RM; the source enum is Sub, Osc A, B, C
            P[f"{k}_xa"] = int(round(1000 * pda * RM_AMOUNT_SCALE))
            P["xchain"] = 1                                                                   # Vital feeds the modulator's own output, with its own modulation, into the carrier
            carried.append(f"osc {o}: ring modulation by osc {src_osc}")
        if pdt_o in (7, 8):                                                                   # FM by another oscillator: Vital's is a phase modulation, the engine's PD warp read at Vital's depth (Warp Chain on)
            src_osc = MOD_SOURCE[o][pdt_o - 7]
            P[f"{k}_xw"], P[f"{k}_xs"] = 4, src_osc
            P[f"{k}_xa"] = int(round(1000 * pda))
            P["xchain"] = 1
            carried.append(f"osc {o}: FM by osc {src_osc}")
        if int(s.get(f"osc_{o}_distortion_type", 0)) > 6 and int(s.get(f"osc_{o}_distortion_type", 0)) not in (7, 8, 10, 11) and abs(s.get(f"osc_{o}_distortion_amount", 0.5) - 0.5) > 1e-3:
            notes.append(f"osc {o}: phase distortion type {int(s[f'osc_{o}_distortion_type'])} not carried")
        if int(s.get(f"osc_{o}_spectral_morph_type", 0)) not in VM.KNOWN and int(s.get(f"osc_{o}_spectral_morph_type", 0)) and abs(s.get(f"osc_{o}_spectral_morph_amount", 0)) > 1e-3:
            notes.append(f"osc {o}: spectral morph type {int(s[f'osc_{o}_spectral_morph_type'])} not carried")
    if s.get("sample_on"):
        sample_oscillator(s, P, wt_root, subdir, notes)
    macro = None   # filled in by the modulation stage once the FX are mapped (it moves FX mixes too)

    # --- amp envelope (seconds are stored as seconds ** 0.25)
    t = lambda key: env_seconds(s, key)
    P["vca_a"] = d2m.pot_attack(t("env_1_attack"))
    if attack_curve_pot(s, 1):
        P["vca_apow"] = attack_curve_pot(s, 1)
    dpow = float(s.get("env_1_decay_power", 0.0))
    if dpow >= DPOW_HOLD or dpow <= DPOW_FAST:                                                                 # a strong positive power holds the level and then plunges, which the engine's one-pole fall cannot follow (Kick Drum 1 at 5.5: 24 -> 4.6; powers of 1.8 to 2 fit as before): the engine draws Vital's own curve (adsr.h setDecayPower), over the stored time itself
        P["vca_dpow"] = pot(64 + CLAMP(dpow, -20.0, 20.0) * 63.0 / 20.0)
        P["vca_d"] = d2m.pot_time(max(float(s["env_1_decay"]), 0.0) ** 4)
    else:
        P["vca_d"] = d2m.pot_time(env_seconds(s, "env_1_decay", True))
    P["vca_s"] = pot(127 * CLAMP(s["env_1_sustain"], 0, 1) ** 2)                        # Vital squares the amplitude envelope into the gain (measured: sustain 0.5 plays at 0.25); the engine sustain is the gain itself
    P["vca_r"] = d2m.pot_time(env_seconds(s, "env_1_release", True))
    P["vca_delay"], P["vca_hold"] = env_stage_pot(s, 1, "delay"), env_stage_pot(s, 1, "hold")

    # --- the two voice filters (cutoff is a MIDI note, 8..136, the same space the engine uses)
    for fi, pre, ecut in ((1, "flt", env_cut), (2, "flt2", env_cut2)):
        if not s.get(f"filter_{fi}_on") or s.get(f"filter_{fi}_mix", 1.0) < 0.02:
            P[f"{pre}_freq"] = 127                                                            # no filter (or one mixed fully dry, which passes its input untouched): wide open
            if s.get(f"filter_{fi}_on"):
                notes.append(f"filter {fi} is mixed fully dry at the saved macro value: left out")
            continue
        mdl, sty, bl = int(s[f"filter_{fi}_model"]), int(s[f"filter_{fi}_style"]), s[f"filter_{fi}_blend"]
        P[f"{pre}_type"], why = filter_type(mdl, sty, bl)
        P["flt_vdl"] = 1                                                                        # Vital's filter drive law (the engine's Filter Drive Law switch is shared by both filters)
        if why:
            notes.append(f"filter {fi}: {why}")
        if VITAL_PHASER and mdl == 7:
            if LFO_BLEND_MEAN and not VITAL_BLEND_LIVE:
                bl = CLAMP(bl + 2.0 * lfo_route_mean(s, routes, f"filter_{fi}_blend"), 0.0, 2.0)
            P[f"{pre}_type"] = 1 if sty == 1 else 0                                       # the Style (its sign), carried by the type's low bit (engine flt_vb = 2, comb.h VitalPhaserFilter)
            P[f"{pre}_vb"] = 2
            P[f"{pre}_blend"] = int(round(127.0 * CLAMP(bl, 0.0, 2.0) / 2.0))
            notes.pop() if why and notes and notes[-1].endswith(why) else None
        elif VITAL_FORMANT and mdl == 5 and sty in (0, 1):
            # Vital's Formant model (engine flt_vb = 3, formant.h): four band-pass resonators between the style's vowels. It ignores the cutoff, resonance and key tracking knobs; its own are X / Y, Spread, Transpose and Resonance.
            P[f"{pre}_vb"] = 3
            P[f"{pre}_fst"] = sty
            P[f"{pre}_fx"] = pot(127.0 * CLAMP(s.get(f"filter_{fi}_formant_x", 0.5), 0.0, 1.0))
            P[f"{pre}_fy"] = pot(127.0 * CLAMP(s.get(f"filter_{fi}_formant_y", 0.5), 0.0, 1.0))
            P[f"{pre}_fsp"] = pot(64.0 + 64.0 * CLAMP(s.get(f"filter_{fi}_formant_spread", 0.0), -1.0, 1.0))
            P[f"{pre}_ftr"] = pot(64.0 + CLAMP(s.get(f"filter_{fi}_formant_transpose", 0.0), -12.0, 12.0) * 64.0 / 12.0)
            P[f"{pre}_fres"] = pot(127.0 * CLAMP(s.get(f"filter_{fi}_formant_resonance", 0.85), 0.0, 1.0))
            notes.pop() if why and notes and notes[-1].endswith(why) else None
        elif FILTER_BLEND and mdl in FILTER_BLEND_MODELS:
            if LFO_BLEND_MEAN and not VITAL_BLEND_LIVE:
                bl = CLAMP(bl + 2.0 * lfo_route_mean(s, routes, f"filter_{fi}_blend"), 0.0, 2.0)               # an LFO moving the Blend knob, at the mean of its shape
            P[f"{pre}_type"] = 1 if sty == 1 else 0                                       # the slope; the Blend crossfade picks low / band / high (engine flt_vb)
            P[f"{pre}_vb"] = 1
            P[f"{pre}_blend"] = int(round(127.0 * CLAMP(bl, 0.0, 2.0) / 2.0))
            P[f"{pre}_vbg"] = int(round(127.0 * FILTER_BLEND_DROP_DB[mdl] / 20.0))
        cut = filter_cutoff_note(s, fi, voice_shift)
        P[f"{pre}_freq"] = pot(cut - 8.0)
        if mdl == 6:
            P.update(comb_params(pre, sty, bl, s.get(f"filter_{fi}_blend_transpose", 0.0)))
            P[f"{pre}_cfine"] = int(round(CLAMP(64 + 100.0 * (cut - 8.0 - round(cut - 8.0)), 0, 127)))      # the comb is tuned in cents: the cutoff pot is whole semitones
        P[f"{pre}_res"] = pot(127 * s[f"filter_{fi}_resonance"])                              # GUESS
        P[f"{pre}_drive"] = pot(127 * s[f"filter_{fi}_drive"] / 30.0)
        kt = float(s.get(f"filter_{fi}_keytrack", 0))                                     # measured (work/kt_probe.py): the cutoff moves by (note - 60) * keytrack semitones, the engine's flt_key does the same at pot 127 = 1
        P[f"{pre}_key"] = pot(127 * CLAMP(kt, 0.0, 1.0))
        if kt < -1e-3:
            notes.append(f"filter {fi} negative key tracking not carried")
        P[f"flt{fi}_mix"] = pot(127.0 * CLAMP(s.get(f"filter_{fi}_mix", 1.0), 0.0, 1.0))                 # measured (calibration/vital_fmix_probe.py): a linear crossfade, the engine's hidden filter Mix
        if s.get(f"filter_{fi}_filter_input"):
            # measured (calibration/vital_serial_probe.py): the filter that takes the other's output gets its own oscillators PLUS the other's output, and the other filter's output then
            # no longer reaches the mix on its own; the engine's routing slot 3 ("Filter") does exactly that
            if s.get(f"filter_{3 - fi}_on") and not s.get(f"filter_{3 - fi}_filter_input"):
                P[f"flt{3 - fi}_out"] = 3
            else:
                notes.append(f"filter {fi} takes filter {3 - fi}'s output, which is off or loops back: serial routing not carried")
        if ecut:
            amt = ecut[0][2]
            P[f"{pre}_env"] = pot(64 + 63 * CLAMP(amt * 128.0 / d2m.FILTER_WIDTH, -1, 1))
            P["flt_a"] = d2m.pot_attack(t("env_2_attack"))                                    # the engine's two filters share one envelope
            if attack_curve_pot(s, 2):
                P["flt_apow"] = attack_curve_pot(s, 2)
            hp2, dsec2 = env_hold_decay(s, 2)
            P["flt_d"] = d2m.pot_time(dsec2)
            P["flt_s"] = pot(127 * CLAMP(s["env_2_sustain"], 0, 1))
            P["flt_r"] = d2m.pot_time(t("env_2_release"))
            P["flt_delay"], P["flt_hold"] = env_stage_pot(s, 2, "delay"), hp2
    if s.get("filter_2_on") and s.get("filter_2_mix", 1.0) >= 0.02:
        carried.append("filter 2")
    if s.get("filter_fx_on"):
        # Vital's Filter FX is a third filter with its own model, cutoff (MIDI note), resonance, drive and mix: the engine's Filter FX stage, same cutoff space as the voice filters
        mdl, sty, bl = int(s["filter_fx_model"]), int(s["filter_fx_style"]), s["filter_fx_blend"]
        P["fxf_on"] = 1
        P["flt_vdl"] = 1                                                                        # the same saturating stage as the voice filters, at every drive
        P["fxf_type"], why = filter_type(mdl, sty, bl)
        if why:
            notes.append(f"filter FX: {why}")
        P["fxf_freq"] = pot(s["filter_fx_cutoff"] - 8.0)
        P["fxf_res"] = pot(127 * s["filter_fx_resonance"])
        P["fxf_wet"] = pot(127 * CLAMP(s["filter_fx_mix"], 0.0, 1.0))
        carried.append("filter FX")
        if abs(s.get("filter_fx_keytrack", 0)) > 1e-3:
            P["fxf_key"] = pot(64 + 63 * CLAMP(float(s["filter_fx_keytrack"]), -1.0, 1.0))             # measured (calibration/vital_fxkey_probe.py): the cutoff moves by keytrack x (the last note - 60) semitones
        if s.get("filter_fx_drive", 0) > 0.0:
            P["fxf_drive"] = pot(127 * CLAMP(s["filter_fx_drive"], 0.0, 30.0) / 30.0)                  # Vital's drive law (the same stage as the voice filters')

    # --- voice
    poly = int(s.get("polyphony", 8))
    P["voice_mode"] = 1 if poly == 1 else 0
    P["voices"] = int(CLAMP(poly, 1, 8))
    P["legato"] = 1 if s.get("legato") else 0
    # Vital's Stereo Routing (a Stereo Mode 0 preset; the older legacy mode ignores it): out = x cos(phi) + swap(x) sin(phi), phi = (1 - routing) pi/4 (checked against the plugin: mid x (cos + sin), side x (cos - sin)).
    # The engine's master Width scales the side against the mid, so the ratio of the two is the width (0 = mono); the mid's 0..3 dB lift is level, which the play-level pass takes care of
    if "stereo_mode" in s and int(s["stereo_mode"]) == 0 and float(s.get("stereo_routing", 1.0)) < 0.999:
        phi = (1.0 - CLAMP(float(s["stereo_routing"]), 0.0, 1.0)) * math.pi / 4.0
        P["mwidth"] = pot(64.0 * (math.cos(phi) - math.sin(phi)) / (math.cos(phi) + math.sin(phi)))
    P["pb_range"] = int(CLAMP(s.get("pitch_bend_range", 2), 0, 24))
    if s.get("portamento_time", -10) > -9.9:
        # Vital's portamento (calibration/vital_porta_probe.py): 2^time seconds for the whole glide, or for each octave with Scale, a linear ramp in pitch; a glide starts from the previous note while
        # its key is held, or after any note with Force. The engine's Glide knob in its Vital law (hidden controls: glide_vlaw / glide_vscale / glide_vforce) does that.
        secs = CLAMP(2.0 ** s["portamento_time"], 0.001, 16.0)
        P["glide_mode"], P["glide_vlaw"] = 2, 1
        P["glide"] = pot(127.0 * math.log(secs / 0.001) / math.log(16000.0))
        P["glide_vscale"] = 1 if s.get("portamento_scale", 0) > 0.5 else 0
        P["glide_vforce"] = 1 if s.get("portamento_force", 0) > 0.5 else 0
        if abs(s.get("portamento_slope", 0.0)) > 1.0:
            notes.append(f"portamento slope {s['portamento_slope']:.1f} is played as a straight glide")

    # --- FX chain: Vital's effects onto the engine's FX block
    P["fx_on"] = 1
    P["fx_space"] = 127                                                                       # the engine scales reverb and delay mixes by this; 127 is 1:1
    # reverb: Vital's decay is measured (calibration/vital_fx_probe.py); the engine's rev_decay is T60 = 0.2 s * 150^(pot / 127), which the same probe confirmed
    P["rev_on"] = 1 if s.get("reverb_on") else 0
    if s.get("reverb_on"):
        # Vital's own reverb module runs in the engine (fx.h processReverbVital) behind the shared knobs: Mix is its equal-power crossfade, Decay the T60 (2^reverb_decay_time seconds, held to the engine's 0.2..30 s),
        # Damp / Lo cut its input band (Pre High / Pre Low), and the size, shelves, chorus and pre-delay have knobs of their own
        P["rev_vital"] = 1
        P["rev_mix"] = pot(127.0 * CLAMP(s["reverb_dry_wet"], 0.0, 1.0))
        P["rev_decay"] = pot_log(CLAMP(2.0 ** s["reverb_decay_time"], 0.2, 30.0), 0.2, 30.0)
        P["rev_damp"] = eq_freq_pot(note_hz(s.get("reverb_pre_high_cutoff", 110.0)))
        P["rev_lo"] = eq_freq_pot(note_hz(s.get("reverb_pre_low_cutoff", 0.0)))
        P["rev_vsize"] = pot(127.0 * CLAMP(s.get("reverb_size", 0.5), 0.0, 1.0))
        P["rev_predelay"] = pot(127.0 * CLAMP(s.get("reverb_delay", 0.0) / 0.3, 0.0, 1.0))
        P["rev_vlocut"] = pot(127.0 * CLAMP(s.get("reverb_low_shelf_cutoff", 0.0), 0.0, 130.0) / 130.0)
        P["rev_vlogain"] = pot(127.0 * CLAMP(-s.get("reverb_low_shelf_gain", 0.0), 0.0, 24.0) / 24.0)
        P["rev_vhicut"] = pot(127.0 * CLAMP(s.get("reverb_high_shelf_cutoff", 90.0), 0.0, 130.0) / 130.0)
        P["rev_vhigain"] = pot(127.0 * CLAMP(-s.get("reverb_high_shelf_gain", -1.0), 0.0, 24.0) / 24.0)
        P["rev_vchorus"] = pot(127.0 * CLAMP(s.get("reverb_chorus_amount", 0.2), 0.0, 1.0))
        P["rev_vchorusf"] = pot(127.0 * (CLAMP(s.get("reverb_chorus_frequency", -2.0), -8.0, 3.0) + 8.0) / 11.0)
        carried.append("reverb")
    # delay, measured: free time is 1 / 2^frequency seconds; synced it is 2^(7 - tempo index) seconds at 120 BPM (x1.5 dotted, x2/3 triplet),
    # which is what the engine's dly_sync reads (times at 120 BPM, then scaled to the host tempo)
    P["dly_on"] = 1 if s.get("delay_on") else 0
    if s.get("delay_on"):
        sync = int(s.get("delay_sync", 0))
        def ms(freq_key, tempo_key):
            if sync:
                return 1000.0 * 2.0 ** (7 - round(s[tempo_key])) * {1: 1.0, 2: 1.5, 3: 2.0 / 3.0}.get(sync, 1.0)
            return 1000.0 / (2.0 ** s[freq_key])
        left_ms = ms("delay_frequency", "delay_tempo")
        right_ms = ms("delay_aux_frequency", "delay_aux_tempo") if s.get("delay_style", 0) > 0 else left_ms
        if max(left_ms, right_ms) > 1500.0:
            notes.append(f"delay time {max(left_ms, right_ms) / 1000:.1f} s is past the engine's 1.5 s: shortened")
        P["dly_mix"] = wet_pot("delay", s["delay_dry_wet"])
        P["dly_sync"] = 1 if sync else 0
        P["dly_time"] = pot_log(left_ms, 1.0, 1500.0)
        P["dly_timer"] = pot_log(right_ms, 1.0, 1500.0)
        P["dly_fb"] = pot(127 * CLAMP(s["delay_feedback"], 0, 0.97))
        P["dly_mode"] = 1 if s.get("delay_style", 0) >= 2 else 0
        # Vital's delay filter (effects/delay.cpp) is a band-pass on everything written into the line: a one-pole low-pass at note (cutoff + 96 spread) and a one-pole high-pass at note (cutoff - 96 spread), with the gain
        # 1 + f_low / f_high that the engine's delay filter has too, so the engine's Lo / Hi corners are those two notes
        rad = 96.0 * CLAMP(float(s.get("delay_filter_spread", 1.0)), 0.0, 1.0)
        cut = float(s.get("delay_filter_cutoff", 60.0))
        P["dly_lo"] = eq_freq_pot(note_hz(cut - rad))
        P["dly_hi"] = eq_freq_pot(note_hz(cut + rad))
        carried.append("delay")
    # chorus (frequency is log2 Hz; the engine's rate knob is cubic over 0..20 Hz)
    if s.get("chorus_on"):
        # Vital's chorus module (the engine's Vital Chorus, ported from chorus_module.cpp): 1-4 voice pairs, delays in log2 seconds (-10..-5.64 over the pot), depth adds up to 30 ms, bipolar feedback, a band-pass in the loop
        P["chor_on"] = 1
        P["chor_vital"] = 1
        P["chor_mix"] = pot(127 * CLAMP(s["chorus_dry_wet"], 0.0, 1.0))
        sync = int(s.get("chorus_sync", 0))
        hz = 1.0 / lfo_period_s(sync, s.get("chorus_tempo", 4.0)) if sync in (1, 2, 3) else 2.0 ** s["chorus_frequency"]      # tempo-synced at the 120 BPM the engine renders at
        P["chor_rate"] = pot(127 * CLAMP(hz / 20.0, 0, 1) ** (1 / 3))
        P["chor_depth"] = pot(127 * s["chorus_mod_depth"])
        P["chor_d1"] = pot(127 * (CLAMP(s["chorus_delay_1"], -10.0, -5.64386) + 10.0) / 4.35614)
        P["chor_d2"] = pot(127 * (CLAMP(s["chorus_delay_2"], -10.0, -5.64386) + 10.0) / 4.35614)
        P["chor_fb"] = pot(127 * (CLAMP(s["chorus_feedback"], -0.95, 0.95) / 0.95 + 1.0) / 2.0)
        P["chor_voices"] = int(CLAMP(round(s.get("chorus_voices", 4)), 1, 4))
        P["chor_vcut"] = pot(127 * (CLAMP(s.get("chorus_cutoff", 60.0), 8.0, 136.0) - 8.0) / 128.0)
        P["chor_vspread"] = pot(127 * CLAMP(s.get("chorus_spread", 1.0), 0.0, 1.0))
        carried.append("chorus")
    def fx_rate_hz(pre: str) -> float:
        sync = int(s.get(f"{pre}_sync", 0))                                               # tempo-synced at the 120 BPM the engine renders at
        return 1.0 / lfo_period_s(sync, s.get(f"{pre}_tempo", 4.0)) if sync in (1, 2, 3) else 2.0 ** s[f"{pre}_frequency"]
    if s.get("flanger_on") and s.get("flanger_center", 64.0) < 8.0 and s.get("flanger_dry_wet", 0.0) > 0.01:
        notes.append(f"flanger centre {s['flanger_center']:.0f} is below the engine's range (8..136): flanger not carried")
    elif s.get("flanger_on"):
        # Vital's flanger (the engine's Vital Flanger, from flanger_module.cpp): a delay of 1 / the centre note's frequency, Depth scales it, bipolar feedback, equal-power mix
        P["flan_on"] = 1
        P["flan_vital"] = 1
        P["flan_mix"] = pot(127 * CLAMP(s["flanger_dry_wet"], 0.0, 1.0))
        P["flan_rate"] = pot(127 * CLAMP(fx_rate_hz("flanger") / 20.0, 0, 1) ** (1 / 3))
        P["flan_depth"] = pot(127 * CLAMP(s["flanger_mod_depth"], 0.0, 1.0))
        P["flan_fb"] = pot(127 * (CLAMP(s["flanger_feedback"], -1.0, 1.0) + 1.0) / 2.0)
        P["flan_vcenter"] = pot(127 * (CLAMP(s["flanger_center"], 8.0, 136.0) - 8.0) / 128.0)
        P["flan_phase"] = pot(127 * CLAMP(s.get("flanger_phase_offset", 0.0), 0.0, 1.0))
        carried.append("flanger")
    if s.get("phaser_on"):
        # Vital's phaser (the engine's Vital Phaser, from phaser.cpp / phaser_filter.cpp): 4 / 8 / 12 all-pass stages by Blend, cutoff = centre note +- depth semitones, a linear mix
        P["phas_on"] = 1
        P["phas_vital"] = 1
        P["phas_mix"] = pot(127 * CLAMP(s["phaser_dry_wet"], 0.0, 1.0))
        P["phas_rate"] = pot(127 * CLAMP(fx_rate_hz("phaser") / 20.0, 0, 1) ** (1 / 3))
        P["phas_depth"] = pot(127 * CLAMP(s["phaser_mod_depth"] / 48.0, 0, 1))
        P["phas_fb"] = pot(127 * (CLAMP(s["phaser_feedback"], -1.0, 1.0) + 1.0) / 2.0)
        P["phas_vcenter"] = pot(127 * (CLAMP(s["phaser_center"], 8.0, 136.0) - 8.0) / 128.0)
        P["phas_vblend"] = pot(127 * CLAMP(s.get("phaser_blend", 1.0), 0.0, 2.0) / 2.0)
        P["phas_phase"] = pot(127 * CLAMP(s.get("phaser_phase_offset", 0.0), 0.0, 1.0))
        carried.append("phaser")
    # distortion: type 0 Soft Clip, 1 Hard Clip, 2 Linear Fold, 3 Sine Fold, 4 Bit Crush, 5 Down Sample (menu strings); drive in dB
    P["dist_mix"] = 0
    if s.get("distortion_on") and (s.get("distortion_mix", 0) > 0 or "distortion_mix" in routed):
        ty = int(s["distortion_type"])
        # Vital's measured law (calibration/vital_dist_probe.py, vital_distfilt_probe.py), the engine's Distortion Calibrated = Vital: drive in dB -30..30 over the pot's 0..127, the mix a linear crossfade,
        # the curve one of tanh / hard clip / triangle fold / sin(pi/2 x) / hold, and the filter a Q 0.5..14 state-variable before (order 1) or after (order 2) the curve, on the wet part only
        P["dist_cal"] = 2
        P["dist_mode"] = {0: 0, 1: 2, 2: 5, 3: 3, 4: 6, 5: 4}[ty]
        P["dist_drive"] = pot((s["distortion_drive"] + 30.0) * 127.0 / 60.0)
        P["dist_mix"] = pot(127 * s["distortion_mix"])
        if ty == 5:
            notes.append("distortion Down Sample: a plain hold of the measured length (Vital's own output past ~12 dB drive is a ping-pong that is not reproduced)")
        order = int(s.get("distortion_filter_order", 0))
        if order in (1, 2):
            P["dist_flt"] = order
            P["dist_flt_cut"] = pot((s.get("distortion_filter_cutoff", 60.0) - 8.0) * 127.0 / 128.0)
            P["dist_flt_res"] = pot(127 * CLAMP(s.get("distortion_filter_resonance", 0.0), 0, 1))
            P["dist_flt_blend"] = pot(127 * CLAMP(s.get("distortion_filter_blend", 0.0), 0, 2) / 2.0)
        carried.append("distortion")
    # master volume: Vital's volume knob is a cubic gain (calibration/vital_comp_probe.py: -31.1 dB from 4879 to 1500, 60 dB per decade; -9.5 dB at 4879 from the compressor fit)
    vol_db = VITAL_VOLUME_DB_AT_4879 + 60.0 * math.log10(max(s.get("volume", 4879.26), 1.0) / 4879.26)
    P["volume"] = d2m.pot_volume(vol_db - ENGINE_PRE_MASTER_OFFSET_DB)
    # compressor: Vital's three bands (low, band, high), each with an upward ratio under a lower threshold, a downward ratio over an upper one and a gain; it sits before the master volume
    # (calibration/vital_comp_probe.py measured the static curves and a model of exactly that fits to 2 dB). The engine's multiband compressor is the same design with one shared ratio.
    P["comp_on"] = 0
    if s.get("compressor_on") and (s.get("compressor_mix", 1.0) > 0.01 or "compressor_mix" in routed):
        eb = int(CLAMP(s.get("compressor_enabled_bands", 0), 0, 3))
        # the engine's Vital compressor law (fx.h processCompVital, from Vital's compressor.cpp): thresholds, ratios and gains go over per band; the levels are the engine's, so thresholds take the pre-master offset
        P["comp_on"] = 1
        P["comp_multi"] = 2
        P["vc_bands"] = eb
        for i, b in enumerate(("low", "band", "high"), 1):
            P[f"vc_ut{i}"] = pot((s[f"compressor_{b}_upper_threshold"] + ENGINE_PRE_MASTER_OFFSET_DB + 100.0) / 112.0 * 127.0)
            P[f"vc_lt{i}"] = pot((s[f"compressor_{b}_lower_threshold"] + ENGINE_PRE_MASTER_OFFSET_DB + 100.0) / 112.0 * 127.0)
            P[f"vc_ur{i}"] = pot(127 * CLAMP(s[f"compressor_{b}_upper_ratio"], 0.0, 1.0))
            P[f"vc_lr{i}"] = pot(64 + 63.5 * CLAMP(s[f"compressor_{b}_lower_ratio"], -1.0, 1.0))
            g = CLAMP(s[f"compressor_{b}_gain"], -30.0, 30.0)
            P[f"vc_g{i}"] = pot(64 + (g / 30.0 * 63.0 if g >= 0 else g / 30.0 * 64.0))
        P["vc_att"] = pot(127 * CLAMP(s.get("compressor_attack", 0.5), 0.0, 1.0))
        P["vc_rel"] = pot(127 * CLAMP(s.get("compressor_release", 0.5), 0.0, 1.0))
        P["comp_mix"] = pot(127 * CLAMP(s.get("compressor_mix", 1.0), 0.0, 1.0))
        P["comp_makeup"] = 0
        carried.append("compressor")
        notes.append(f"compressor carried with Vital's own law (bands {eb}); the per-band mix is one shared mix")
    # EQ: low shelf, band (bell), high shelf
    bands = []
    for tag, ty in (("low", 2), ("band", 1), ("high", 3)):
        g = s.get(f"eq_{tag}_gain", 0.0)
        if abs(g) > 0.1:
            bands.append((ty, note_hz(s[f"eq_{tag}_cutoff"]), g, s.get(f"eq_{tag}_resonance", 0.5)))
    P["eq_on"] = 1 if (s.get("eq_on") and bands) else 0
    if s.get("eq_on") and bands:
        for n, (ty, hz, g, q) in enumerate(bands, 1):
            P[f"eq{n}_type"] = ty
            P[f"eq{n}_freq"] = eq_freq_pot(hz)
            P[f"eq{n}_gain"] = pot(64 + 63 * CLAMP(g / 24.0, -1, 1))
            P[f"eq{n}_q"] = pot(127 * CLAMP(q, 0, 1))                                          # GUESS
        carried.append("EQ")
    P["wide_on"] = 0
    P["fx_wide"] = 0
    macro = map_modulation(s, P, left, main_filter, notes)
    # effect order: the engine's main chain runs seven stages in an order set by fx_order (the rank of the permutation, 0 = distortion, Filter FX, comp, chorus / flanger / phaser, delay, reverb, EQ)
    order = effect_order(s.get("effect_chain_order", 0.0))
    mods = [x for x in ("chorus", "flanger", "phaser") if s.get(f"{x}_on")]
    stage_of = {"distortion": 0, "filter_fx": 1, "compressor": 2, "chorus": 3, "flanger": 3, "phaser": 3, "delay": 4, "reverb": 5, "eq": 6}
    at = {fx: i for i, fx in enumerate(order)}
    pos = [min((at[x] for x in (mods or ["chorus"])), default=0) if st == 3 else at[next(f for f, g in stage_of.items() if g == st)] for st in range(7)]
    seq = sorted(range(7), key=lambda st: pos[st])
    P["fx_order"] = stage_rank(seq)
    if len(mods) > 1:
        idx = sorted(at[x] for x in mods)
        between = [x for x in order[idx[0]:idx[-1] + 1] if x not in mods and s.get(f"{x}_on")]
        if between:
            notes.append(f"chorus / flanger / phaser run as one block in the engine; Vital has {', '.join(between)} between them")
    for o in (1, 2, 3):                                                                       # an oscillator at level 0 is only audible if a route (LFO, envelope, macro) raises it
        if s.get(f"osc_{o}_on") and P[f"wt{o}_level"] == 0:
            lvl_dst = {1: 3, 2: 4, 3: 19}[o]
            moved = (any(P[f"lr{r}_dst"] == lvl_dst and P[f"lr{r}_src"] for r in range(1, 9)) or any(P[f"me{e}_dst"] == lvl_dst for e in (1, 2))
                     or any(key == f"wt{o}_level" for _, key, _ in macro["routes"]))
            if not moved:
                notes.append(f"osc {o}: level 0 with nothing carried to raise it")
    if not P.get("smp_set") and not any(P[f"wt{o}_level"] > 0 or any(P[f"lr{r}_dst"] == {1: 3, 2: 4, 3: 19}[o] and P[f"lr{r}_src"] for r in range(1, 9))
               or any(P[f"me{e}_dst"] == {1: 3, 2: 4, 3: 19}[o] for e in (1, 2)) for o in (1, 2, 3)):
        notes.append("SILENT: no oscillator is audible (the sound comes from a route that is not carried)")
    return {"name": name, "params": P, "paths": paths, "carried_fx": carried, "notes": notes, "macro": macro,
            "fx_in_preset": [fx for fx in ("reverb", "delay", "chorus", "flanger", "phaser", "distortion", "compressor", "eq", "filter_fx") if s.get(f"{fx}_on")]}


def blob(m: dict) -> str:
    parts = [f"{k}={v}" for k, v in m["params"].items()] + [f"{k}={v}" for k, v in m["paths"].items()]
    mc = m.get("macro")
    if mc and mc["routes"]:
        parts.append("mac=" + ",".join(f"{k}:{key}:{travel:.1f}" for k, key, travel in mc["routes"]))
        parts.append("macn=" + "|".join(mc["names"]))
    return "TBLR2;" + ";".join(parts) + ";"


def convert(path: Path, out: Path, subdir: str = "Vital", play_level: bool = False) -> dict:
    """Convert one preset into out/move/... . With play_level the volume is set last so a chord plays at the same loudness as every other preset (play_level.py, as for Serum imports); it needs
    calibration/tablor_cli.exe and reads the tables just written from out/move."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    name = d2m.ascii_name(Path(path).stem)
    m = map_vital(d, name, out / "move/data/UserData/UserLibrary/Wavetables", subdir)
    state = blob(m)
    if play_level:
        import play_level as PL
        PL.MOVE_ROOT = Path(out) / "move"                # the tables this call wrote
        state, info = PL.fit_state(state, name)
        m["params"]["volume"] = int(PL.getk(state, "volume", m["params"].get("volume", 64)))
        m["notes"].append(f"volume set so a chord plays at the common loudness ({info['from']} -> {info['to']}; Vital's own level is not kept)" + ("; held down by the peak ceiling" if info["held_by_peak"] else ""))
        state = blob(m)
    pre = out / "move/data" / d2m.MOVE_PRESET_DIR
    pre.mkdir(parents=True, exist_ok=True)
    (pre / f"{name}.json").write_text(json.dumps({"name": name, "module": "tinyhouse", "version": 1, "state": state}) + "\n", encoding="utf-8")
    return m


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("presets", nargs="+", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("out/vital"))
    ap.add_argument("--no-play-level", action="store_true", help="keep the volume the preset's own settings give (default: set it so every preset plays a chord at the same loudness; needs calibration/tablor_cli.exe, and the tables are read from -o/move)")
    a = ap.parse_args()
    for p in a.presets:
        m = convert(p, a.out, play_level=not a.no_play_level)
        print(f"{m['name']}: FX carried {m['carried_fx']} of {m['fx_in_preset']}; {len(m['notes'])} notes")
        for n in m["notes"]:
            print("   -", n)


if __name__ == "__main__":
    main()
