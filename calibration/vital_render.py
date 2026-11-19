# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Render a Vital preset (.vital) headless through the real Vital VST3, for calibration and A/B checks.

    python calibration/vital_render.py "My Preset.vital" out.wav [--note 60 --vel 100 --hold 1.5 --dur 3.0]

How a preset gets into the plugin (found by saving the plugin's own state and reading it):
  state file  = b"VC2!" + uint32 LE length + XML + NUL          (what DawDreamer's save_state / load_state use)
  XML         = <VST3PluginState><IComponent>SIZE.BASE64</IComponent></VST3PluginState>, with JUCE's base64
                (alphabet ".A-Za-z0-9+", six bits per character, low bits first)
  IComponent  = b"VstW" header + a VST2 bank ("CcnK" size "FBCh" ...), whose chunk is Vital's state: the .vital
                JSON as plain text, then padding and b"JUCEPrivateData"
So a preset is loaded by swapping the JSON in the chunk of a template state that the plugin saved itself.

Needs: dawdreamer (GPL, used here only as a local tool, never shipped), numpy, Vital installed
(VITAL_VST overrides the default path).
"""
from __future__ import annotations

import os
import re
import struct
import sys
import tempfile
from pathlib import Path

VITAL_VST = os.environ.get("VITAL_VST", r"C:\Program Files\Common Files\VST3\Vital.vst3")
SR, BLOCK = 44100, 512
_ALPHA = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+"
_IDX = {c: i for i, c in enumerate(_ALPHA)}


def juce_b64_decode(text: str, size: int) -> bytes:
    bits = nb = 0
    out = bytearray()
    for ch in text:
        if ch not in _IDX:
            continue
        bits |= _IDX[ch] << nb
        nb += 6
        while nb >= 8:
            out.append(bits & 255)
            bits >>= 8
            nb -= 8
    return bytes(out[:size])


def juce_b64_encode(data: bytes) -> str:
    out = []
    bits = nb = 0
    for byte in data:
        bits |= byte << nb
        nb += 8
        while nb >= 6:
            out.append(_ALPHA[bits & 63])
            bits >>= 6
            nb -= 6
    if nb:
        out.append(_ALPHA[bits & 63])
    return "".join(out)


def split_state(state: bytes) -> tuple[bytes, int, int]:
    """(inner IComponent bytes, offset of the bank chunk, chunk size) of a plugin state file."""
    m = re.search(rb"<IComponent>(\d+)\.(.*?)</IComponent>", state, re.S)
    if not m:
        raise ValueError("not a VST3 state from a JUCE plugin")
    inner = juce_b64_decode(m.group(2).decode(), int(m.group(1)))
    i = inner.find(b"CcnK")
    cs_off = i + 8 + 20 + 128                       # CcnK, size, then FBCh version fxID fxVersion numPrograms, 128 reserved bytes
    return inner, cs_off + 4, struct.unpack(">I", inner[cs_off:cs_off + 4])[0]


def state_with_preset(template: bytes, preset_json: bytes) -> bytes:
    """The template state with its Vital chunk replaced by this preset (the JSON as text)."""
    inner, ch_off, ch_len = split_state(template)
    chunk = inner[ch_off:ch_off + ch_len]
    tail = chunk[chunk.rfind(b"}") + 1:]              # padding + JUCEPrivateData, kept as the plugin wrote it
    new_chunk = preset_json + tail
    i = inner.find(b"CcnK")
    head = bytearray(inner[:ch_off - 4]) + struct.pack(">I", len(new_chunk))
    head[i + 4:i + 8] = struct.pack(">I", len(head) - (i + 8) + len(new_chunk))      # CcnK size covers everything after the size field
    new_inner = bytes(head) + new_chunk
    xml = ('<?xml version="1.0" encoding="UTF-8"?> <VST3PluginState><IComponent>%d.%s</IComponent></VST3PluginState>'
           % (len(new_inner), juce_b64_encode(new_inner))).encode()
    return b"VC2!" + struct.pack("<I", len(xml)) + xml + b"\x00"


class VitalHost:
    """One Vital instance. render() loads a preset and plays notes; each call builds a fresh engine so renders do not leak into each other."""

    def __init__(self, vst: str = VITAL_VST):
        import dawdreamer as daw
        self.daw, self.vst = daw, vst
        self.tmp = Path(tempfile.mkdtemp(prefix="vital_render_"))
        eng = daw.RenderEngine(SR, BLOCK)
        syn = eng.make_plugin_processor("v", vst)
        syn.save_state(str(self.tmp / "template.bin"))
        self.template = (self.tmp / "template.bin").read_bytes()

    def render(self, preset: bytes | str | Path, notes=((60, 100, 0.0, 1.5),), dur: float = 3.0):
        """Stereo float array (2, n) at 44.1 kHz. notes: (midi note, velocity, start s, length s)."""
        raw = preset if isinstance(preset, bytes) else Path(preset).read_bytes()
        path = self.tmp / "preset_state.bin"
        path.write_bytes(state_with_preset(self.template, raw))
        eng = self.daw.RenderEngine(SR, BLOCK)
        syn = eng.make_plugin_processor("v", self.vst)
        syn.load_state(str(path))
        for n, v, t0, ln in notes:
            syn.add_midi_note(int(n), int(v), float(t0), float(ln))
        eng.load_graph([(syn, [])])
        eng.render(float(dur))
        return eng.get_audio()


def main() -> None:
    import argparse
    import numpy as np
    import soundfile as sf
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("preset")
    ap.add_argument("out")
    ap.add_argument("--note", type=int, default=60)
    ap.add_argument("--vel", type=int, default=100)
    ap.add_argument("--hold", type=float, default=1.5)
    ap.add_argument("--dur", type=float, default=3.0)
    a = ap.parse_args()
    audio = VitalHost().render(a.preset, ((a.note, a.vel, 0.0, a.hold),), a.dur)
    sf.write(a.out, np.asarray(audio).T, SR)
    print(f"{a.out}: peak {np.abs(audio).max():.3f}, rms {np.sqrt((audio ** 2).mean()):.4f}")


if __name__ == "__main__":
    main()
