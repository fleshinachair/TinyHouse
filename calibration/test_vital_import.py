# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Checks of the Vital front end that need no plugin: python -m pytest calibration/test_vital_import.py (or run it directly)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "converter"))
import vital_import as VI                                           # noqa: E402
import vital_morph as VM                                            # noqa: E402


def _frame(seed: int = 1, nh: int = 40) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = np.arange(2048) / 2048.0
    return sum(np.sin(2 * np.pi * (h + 1) * x + rng.uniform(0, 6.28)) / (h + 1) for h in range(nh))


def _power(f: np.ndarray) -> np.ndarray:
    return np.abs(np.fft.rfft(f))[1:60]


def test_morph_neutral_amounts_do_nothing():
    t = _frame()[None]
    assert VM.morph_table(t, 3, 0.5) is None and VM.morph_table(t, 7, 1.0) is None and VM.morph_table(t, 8, 0.0) is None
    assert VM.morph_table(t, 1, 0.5) is None and VM.morph_table(t, 10, 0.5) is None          # vocode and Shepard need the played pitch


def test_low_and_high_pass_split_the_spectrum():
    t = _frame()[None]
    lo, hi = VM.morph_table(t, 7, 0.4)[0], VM.morph_table(t, 8, 0.4)[0]
    far = np.r_[0:14, 17:59]                                                                   # cutoff harmonic 16: the two ramps overlap only within one harmonic of it
    assert np.allclose((_power(lo) + _power(hi))[far], _power(t[0])[far], rtol=1e-6, atol=1e-6)


def test_phase_disperse_keeps_the_magnitudes():
    t = _frame()[None]
    out = VM.morph_table(t, 9, 0.7)[0]
    assert np.allclose(_power(out), _power(t[0]), rtol=1e-6, atol=1e-6) and not np.allclose(out, t[0], atol=1e-3)


def test_stretch_doubles_the_harmonics():
    x = np.arange(2048) / 2048.0
    t = np.sin(2 * np.pi * 3 * x)[None]
    out = np.abs(np.fft.rfft(VM.morph_table(t, 3, 0.625)[0]))                                   # a = 0.625: scale 2, harmonic 3 -> 1 + 2 * 2 = 5
    assert out.argmax() == 5


def test_keyframe_interpolation_endpoints_and_modes():
    a, b = _frame(1), _frame(2)
    keys = [(0.0, a), (255.0, b)]
    for style in (0, 1, 2):
        for mode in (0, 1):
            assert np.allclose(VI._interp_frames(keys, 0.0, style, mode), a, atol=1e-9)
    for mode in (0, 1):
        assert np.allclose(VI._interp_frames(keys, 255.0, 1, mode), b, atol=1e-9)
    assert np.allclose(VI._interp_frames(keys, 127.5, 1, 0), (a + b) / 2)
    assert not np.allclose(VI._interp_frames(keys, 127.5, 1, 1), (a + b) / 2, atol=1e-3)     # the frequency mode is not a time-domain crossfade


def test_envelope_times_follow_the_engine_curve():
    # the engine's own curve is a power of -4.9 that ends at the set time: a Vital stage of that power needs the same time
    assert abs(VI.env_fall_factor(VI.ENGINE_FALL_POWER, 0.0, False) - 1.0) < 0.05
    # a gentler power (-2) takes longer on the engine to fall the same way; a squared gain (the amp envelope) shortens it
    assert VI.env_fall_factor(-2.0, 0.0, False) > 1.5 > 1.0 > VI.env_fall_factor(-2.0, 0.0, True) * 0.9
    s = {"env_2_decay": 0.8, "env_2_decay_power": -2.0, "env_2_sustain": 0.0, "env_2_attack": 0.5}
    assert abs(VI.env_seconds(s, "env_2_attack") - 0.5 ** 4) < 1e-12                              # attack is not reshaped
    assert VI.env_seconds(s, "env_2_decay") > 0.8 ** 4 * 1.5

def test_audio_source_slides_its_window_between_keyframes():
    import base64
    audio = (np.sin(2 * np.pi * np.arange(20000) / 50.0) * 0.5 * 32767).astype("<i2")          # a tone, then a bright burst of noise in the second half
    audio[10000:] = (np.random.default_rng(3).uniform(-1, 1, 10000) * 16000).astype("<i2")
    comp = {"type": "Audio File Source", "audio_file": base64.b64encode(audio.tobytes()).decode(), "window_size": 2048.0, "interpolation_style": 1,
            "keyframes": [{"position": 0, "start_position": 0.0}, {"position": 256, "start_position": 12000.0}]}
    frames = VI.render_group({"components": [comp]}, [])
    mid = frames[len(frames) // 2]                                                             # frame 64 of 128: a window about 6000 samples in (the tone), not a blend of the two end frames
    assert np.abs(np.fft.rfft(mid))[100:].sum() < 0.2 * np.abs(np.fft.rfft(frames[-1]))[100:].sum()


def test_audio_rate_keytracked_lfo_goes_to_the_distortion_unit():
    s = {"lfos": [{"points": [0.0, 1.0, 0.5, 0.0, 1.0, 1.0], "powers": [0.0, 0.0]}] * 8, "distortion_on": 1.0, "distortion_filter_order": 1.0,
         "lfo_2_sync": 4.0, "lfo_2_keytrack_transpose": 12.0, "lfo_2_keytrack_tune": 0.0, "modulations": []}
    P: dict = {}
    routes = [("lfo_2", "distortion_drive", -0.16, False), ("lfo_2", "distortion_filter_cutoff", 0.3, True)]
    VI.map_modulation(s, P, routes, 1, [])
    assert P["vdl_on"] == 1 and P["vdl_trans"] == 64 + 16                                       # +12 semitones: pot 64 + 12 / 0.75
    assert P["vdl_drive"] == round(64 + 63 * -0.16) and P["vdl_cut"] == round(64 + 63 * 0.3)    # a bipolar 0.3 is 0.15 x 2 on the unipolar value
    assert P.get("lr1_src", 0) == 0                                                             # nothing left for the block-rate LFOs
    assert P["vdl_s8"] == 127 and P["vdl_s0"] == 0                                              # the inverted triangle: 0 at the start, 1 half-way


def test_macro_routes_to_effect_controls_are_added_at_the_saved_value():
    s = {"reverb_decay_time": 1.0, "macro_control_2": 0.5, "eq_high_gain": 14.0, "macro_control_1": 1.0, "reverb_dry_wet": 0.2}
    routes = [("macro_control_2", "reverb_decay_time", 0.25, False), ("macro_control_1", "eq_high_gain", 0.5, False), ("macro_control_1", "reverb_dry_wet", 0.1, False), ("lfo_1", "reverb_size", 0.3, False)]
    assert VI.fold_fx_routes(s, routes) == 2
    assert abs(s["reverb_decay_time"] - (1.0 + 0.25 * 12.0 * 0.5)) < 1e-9                       # a fraction of the -6..6 range at half a macro
    assert s["eq_high_gain"] == 15.0                                                            # clamped to the band's +-15 dB
    assert [r[1] for r in routes] == ["reverb_dry_wet", "reverb_size"]                          # the mix (handled as a live route) and the LFO route stay


def test_voice_transpose_moves_a_key_tracked_cutoff():
    s = {"filter_1_cutoff": 80.0, "filter_1_keytrack": 1.0, "filter_2_cutoff": 80.0, "filter_2_keytrack": 0.0}
    assert VI.filter_cutoff_note(s, 1, -24.0) == 56.0 and VI.filter_cutoff_note(s, 2, -24.0) == 80.0


def test_velocity_and_wheel_routes_into_a_macro_move_its_saved_value():
    s = {"macro_control_1": 0.2, "macro_control_2": 0.9, "macro_control_3": 0.0, "macro_control_4": 0.0, "mod_wheel": 0.5}
    routes = [("velocity", "macro_control_1", 0.5, False), ("mod_wheel", "macro_control_2", 0.5, False), ("velocity", "macro_control_3", -1.0, False)]
    stored = VI.map_modulation(s, dict(VI.d2m.TABLOR_DEFAULTS), routes, 1, [])["stored"]
    assert abs(stored[0] - (0.2 + 0.5 * VI.VELOCITY_NOMINAL)) < 1e-9
    assert stored[1] == 1.0 and stored[2] == 0.0                                                # clamped to 0..1


def test_vitalbank_is_opened_like_a_pack_and_its_presets_are_found():
    import io
    import zipfile
    import import_web as W
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:                           # a Vital bank: a zip of <Bank>/Presets/*.vital
        z.writestr("My Bank/Presets/01 - Pad.vital", "{}")
        z.writestr("My Bank/Presets/02 - Lead.vital", "{}")
    W.reset()
    try:
        assert W.add_files({"My Bank.vitalbank": buf.getvalue()}) == 2
        assert W.preset_names() == ["01 - Pad", "02 - Lead"]
    finally:
        W.reset()


def test_portamento_is_carried_in_vitals_law():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import vital_route_probe as RP
    d = RP.base_preset()
    s = d["settings"]
    s["portamento_time"], s["portamento_scale"], s["portamento_force"] = -1.0, 1.0, 1.0           # 0.5 s per octave, glides after any note
    m = VI.map_vital(d, "p", Path("/nonexistent"), "Probe")
    P = m["params"]
    assert P["glide_mode"] == 2 and P["glide_vlaw"] == 1 and P["glide_vscale"] == 1 and P["glide_vforce"] == 1
    secs = 0.001 * 16000.0 ** (P["glide"] / 127.0)
    assert abs(secs - 0.5) / 0.5 < 0.06, secs                                                    # the knob is a log scale over 14 octaves of time, 127 steps: a few % at most
    assert not any("portamento" in n and "not carried" in n for n in m["notes"])


def test_lfo_delay_fade_phase_and_smooth_are_carried():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import vital_lfo_probe as LP
    d = LP.base_preset()
    s = d["settings"]
    s.update(lfo_1_delay_time=1.0, lfo_1_fade_time=4.0, lfo_1_phase=0.5, lfo_1_smooth_mode=0.0)
    P = VI.map_vital(d, "p", Path("/nonexistent"), "Probe")["params"]
    assert P["lfo1_delay"] == 32 and P["lfo1_fade"] == 64 and P["lfo1_phase"] == 64 and P["lfo1_smooth"] == 0
    s.update(lfo_1_smooth_mode=1.0, lfo_1_smooth_time=-2.0)                    # smoothing on: a 0.072 s time constant, and Vital ignores the fade
    P = VI.map_vital(d, "p", Path("/nonexistent"), "Probe")["params"]
    tau = 0.002 * 350.0 ** (P["lfo1_smooth"] / 127.0)
    assert abs(tau - 0.0721) / 0.0721 < 0.06 and "lfo1_fade" not in P


def test_wavetable_modifiers_follow_vitals_algorithms():
    import vital_wtmod as WM
    x = 0.8 * np.sin(2 * np.pi * np.arange(2048) / 2048.0)
    pos = np.array([0.0])
    fold = lambda boost: WM.apply_modifier({"type": "Wave Folder", "keyframes": [{"position": 0, "fold_boost": boost}]}, x[None], pos)[0]
    assert np.abs(fold(1.0) - x).max() < 1e-9                                  # a boost of 1 leaves a wave under full scale alone
    harm = lambda w: np.abs(np.fft.rfft(w))[2:].sum() / np.abs(np.fft.rfft(w))[1]
    assert harm(fold(3.0)) > harm(x) + 0.3                                     # more boost folds in harmonics
    lp = WM.apply_modifier({"type": "Frequency Filter", "style": 0, "normalize": False, "keyframes": [{"position": 0, "cutoff": 0.0, "shape": 0.0}]},
                           (np.sin(2 * np.pi * np.arange(2048) * 5 / 2048.0) + x)[None], pos)[0]
    assert np.abs(np.fft.rfft(lp))[5] < 1e-6 and np.abs(np.fft.rfft(lp))[1] > 100       # a low pass at bin 1 removes the 5th harmonic and keeps the first
    ph = WM.apply_modifier({"type": "Phase Shift", "style": 4, "keyframes": [{"position": 0, "phase": 0.0, "mix": 1.0}]}, x[None], pos)[0]
    assert np.allclose(np.abs(np.fft.rfft(ph)), np.abs(np.fft.rfft(x)), atol=1e-6)      # Clear keeps every harmonic's level


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
