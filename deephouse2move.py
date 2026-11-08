#!/usr/bin/env python3
"""Serum 2 pack -> .vital JSON -> Tablor presets for Ableton Move (Schwung).

    pip install serum2-preset-loader
    python deephouse2move.py "Loopsy - DEEP HOUSE.SerumPack" -o out \
        [--serum-tables "<your Serum 2 Presets>/Tables"] [--wt-subdir "Deep House"]

Stages
  1. extract  unzip the .SerumPack (or read a folder of .SerumPreset files),
              decode each preset, copy every packaged wavetable (.wav).
  2. vital    write out/vital/<name>.vital. Stock key names/units are Vital's as
              best known, but the files were NOT load-tested in Vital. The
              authoritative values live in the "serum2move" block (Hz, seconds,
              semitones, percent), which is what stage 3 reads.
  3. tablor   read the .vital files, map to Tablor's 71 controls, write
              out/move/data/UserData/schwung/presets/tinyhouse/<name>.json and the
              wavetables to out/move/data/UserData/UserLibrary/Wavetables/.
              Copy out/move/data/UserData/ onto the Move. A fidelity report
              (report.md / report.json) lists everything Tablor cannot do.

"serum2vital" is not a published package (PyPI 404); decoding uses the real
`serum2-preset-loader`, whose measured unit curves are reused below.

These presets target the patched Tablor 1.5.0-dh (branch deephouse-engine of
schwung-tablor): three oscillators, unison to 16, hard sync, tune to +/-48, filter
drive. Stock 1.3.3 ignores the keys it does not know and caps unison at 4, so they
would load but thin out.

Tablor facts (keys, ranges, curves, TBLR2 blob, preset path) come from
schwung-tablor (CONTROLS.md, param_map.h, voice.h, tablor_plugin.cpp). Serum unit
curves were measured by serum2-preset-loader on
Serum 2.1.5; this pack is 2.0.24, so treat absolute values as close, not exact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

import fxmap
import sample_export as SE

try:
    from serum2_preset_loader.codec import decode_preset_file
    from serum2_preset_loader.params import PARAMS
except ImportError:
    sys.exit("Missing dependency: pip install serum2-preset-loader")

MOVE_WT_ROOT = "/data/UserData/UserLibrary/Wavetables"
MOVE_SAMPLE_ROOT = "/data/UserData/UserLibrary/Samples"
MOVE_PRESET_DIR = "UserData/schwung/presets/tinyhouse"

# ---------------------------------------------------------------------------
# Serum 2 curves (from serum2-preset-loader.params) and defaults
# ---------------------------------------------------------------------------
CUTOFF_NOTES = 12 * math.log2(2697.0)        # knob span in semitones (~136.7)
FILTER_WIDTH = 12 * math.log2(20000 / 440) + 69   # Tablor's filterWidth (~135.3)


def serum_cutoff_hz(norm: float) -> float:
    return 8.18 * 2697.0 ** norm


DEFAULTS = {
    "detune": PARAMS["osc_a_detune"].default_stored,     # absent => 0.25
    "reso": PARAMS["filter1_reso"].default_stored,       # absent => 10.0
    "cutoff": PARAMS["filter1_cutoff"].default_stored,   # absent => 0.5
    "env": [{k: PARAMS[f"env{n}_{k}"].default_stored
             for k in ("attack", "decay", "sustain", "release")} for n in (1, 2, 3, 4)],
    "osc_level": 0.75,    # assumption: not in the loader's table
}


def _pp(node) -> dict:
    p = node.get("plainParams") if isinstance(node, dict) else None
    return p if isinstance(p, dict) else {}


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def ascii_name(s: str) -> str:
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = s.encode("ascii", "replace").decode().replace("?", "")
    s = re.sub(r'[\\/:*?"<>|;=\r\n]', " ", s)
    return re.sub(r"\s+", " ", s).strip(" .") or "Untitled"


# ---------------------------------------------------------------------------
# Stage 1: extract + decode
# ---------------------------------------------------------------------------
def extract(src: Path, out: Path) -> tuple[list[Path], dict[str, Path]]:
    if src.is_file():
        raw = out / "_extracted"
        raw.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(src) as z:
            z.extractall(raw)             # zipfile strips absolute/.. members
        root = raw
    else:
        root = src
    presets = sorted(root.rglob("*.SerumPreset"))
    tables: dict[str, Path] = {}
    for wav in sorted(root.rglob("*.wav")):
        tables[wav.name.lower()] = wav
    return presets, tables


def _noise_click(node: dict) -> bool:
    n = node.get("NoiseOsc3", {})
    return bool(_pp(n).get("kParamOneShot", 0)) and 0 < (n.get("numFrames") or 10 ** 9) < 11025


def route_curve(slot: dict):
    """A route's own curve (kParamMainCurveData on, the points in flex[0]) as 33 samples of the value it maps the source to, or None for a straight route."""
    flex = slot.get("flex") or []
    cv = flex[0] if flex and isinstance(flex[0], dict) else {}
    if _pp(slot).get("kParamMainCurveData", 0.0) < 0.5 or not cv.get("xVals") or len(cv["xVals"]) != len(cv.get("yVals") or []):
        return None
    return [curve_value(cv["xVals"], cv["yVals"], cv.get("curveVals") or [], k / 32.0) for k in range(33)]


def aux_curve(slot: dict):
    """A route's aux curve (kParamAuxCurveData on, the points in flex[1]) as 33 samples of the scale it gives the depth for an aux value x = k / 32, or None for a straight route.
    Measured (calibration/probe_aux_curve.py): the depth is amount x curve(x), the curve read as 1 - y like every Serum curve, and it REPLACES the straight x: a macro at 0 / .25 / .5 / .75 / 1
    on a curve (0.8 -> 0.2) gave 0.20 / .35 / .50 / .66 / .79, velocity and note number (x = value / 127) on (0.43 -> 0) gave 0.64 / .77 / .90 / 1.0 at 20 / 60 / 100 / 127, and a random aux
    (21, 22: uniform 0..1 per note) on (0.274 -> 0.166) gave 0.73..0.81, mean 0.78."""
    flex = slot.get("flex") or []
    cv = flex[1] if len(flex) > 1 and isinstance(flex[1], dict) else {}
    if _pp(slot).get("kParamAuxCurveData", 0.0) < 0.5 or not cv.get("xVals") or len(cv["xVals"]) != len(cv.get("yVals") or []):
        return None
    return [curve_value(cv["xVals"], cv["yVals"], cv.get("curveVals") or [], k / 32.0) for k in range(33)]


def aux_scale(aux: int, curve, macros: list) -> float:
    """The factor a route's aux source gives its depth in a static preset: the aux's value x (a macro at its saved value, velocity VELOCITY_NOMINAL / 127, note 60 / 127, a random aux or an
    envelope / LFO gate at its mean) through the route's aux curve when it has one (probe_aux_curve.py), else x itself."""
    if 25 <= aux <= 32:
        x = macros[aux - 25] / 100.0
    elif aux == 16:
        x = VELOCITY_NOMINAL / 127.0
    elif aux == 17:
        x = 60 / 127.0
    else:
        x = None                                         # random per note (21, 22) or an envelope / LFO gate: the mean over x
    if curve is None:
        return 0.5 if x is None else x
    if x is None:
        return (sum(curve) - 0.5 * (curve[0] + curve[-1])) / (len(curve) - 1)
    pos = _clamp(x, 0.0, 1.0) * 32.0
    i = min(int(pos), 31)
    return curve[i] + (curve[i + 1] - curve[i]) * (pos - i)


def physical(tree: dict, header: dict) -> dict:
    """Decode a Serum tree into plain physical units."""
    g = _pp(tree.get("Global0", {}))
    oscs = []
    for i in range(3):
        node = tree.get(f"Oscillator{i}", {})
        p = _pp(node)
        wt = tree.get(f"Oscillator{i}", {}).get(f"WTOsc{i}", {})
        wtp = _pp(wt)
        kind = p.get("kParamType", "kOsc_WT")
        oscs.append({
            "id": i,
            "enabled": bool(p.get("kParamEnable", 1.0 if i == 0 else 0.0)),
            "kind": kind,
            "sample": SE.sample_osc(tree, i),                      # a Sample / Multisample oscillator's file and settings (sample_export.py)
            "level": p.get("kParamVolume", DEFAULTS["osc_level"]),
            "octave": p.get("kParamOctave", 0.0),
            "semitone": p.get("kParamPitch", p.get("kParamCoarsePit", 0.0)),
            "fine_cents": p.get("kParamFine", 0.0),
            "pan": p.get("kParamPan", 0.0),
            "unison": int(p.get("kParamUnison", 1)),
            "detune": p.get("kParamDetune", DEFAULTS["detune"]),
            "detune_wid": p.get("kParamDetuneWid"),      # how far the stack spreads, not stereo
            "stereo_pct": p.get("kParamUnisonStereo"),
            "table": wt.get("relativePathToWT"),
            "embedded": wt.get("embeddedWTData") if not wt.get("relativePathToWT") else None,        # a table edited in Serum and stored in the preset (no file)
            "table_pos": wtp.get("kParamTablePos", 1.0),            # 1..256
            "warp": [{"mode": wtp.get(m), "amount": wtp.get(a, 0.0)}
                     for m, a in (("kParamWarpMenu", "kParamWarp"),
                                  ("kParamWarpMenu2", "kParamWarp2")) if wtp.get(m)],
        })
    sub_node = tree.get("Oscillator4", {})
    noise_node = tree.get("Oscillator3", {})
    flt = _pp(tree.get("VoiceFilter0", {}))
    envs = []
    for n in range(4):
        p = _pp(tree.get(f"Env{n}", {}))
        d = DEFAULTS["env"][n]
        envs.append({k: p.get(f"kParam{k.capitalize()}", d[k])
                     for k in ("attack", "decay", "sustain", "release")}
                    | {f"curve{c}": p.get(f"kParamCurve{c}") for c in (1, 2, 3)})
    mods = []
    for i in range(64):
        s = tree.get(f"ModSlot{i}", {})
        if "source" in s and s.get("destModuleTypeString"):
            mods.append({"source": s["source"][0], "aux": s["source"][1] if len(s["source"]) > 1 else 0, "dest_type": s["destModuleTypeString"],
                         "dest_id": s.get("destModuleID"), "dest_param": s.get("destModuleParamName"),
                         # kParamOut, when saved, is the route's output scale in percent (default 100): depth = amount x Out / 100 on every destination
                         # (calibration/probe_route_out.py: coarse pitch swing 11 st at 10 % with no Out, 0.1 st at Out 1, 2.1 st at Out 20; a filter route's
                         # centroid swing 63 / 30 / 6 Hz at Out none / 50 / 10). Lead - Let It Go's sine into pitch at Out 0.86 does nothing in Serum. Not for a velocity or note-number aux (16, 17): Lead - Can't Find You's
                         # Out 38-46 routes render the same in Serum with or without it, and the scale made the engine's match worse).
                         "amount_pct": _pp(s).get("kParamAmount", 0.0) * (1.0 if (s["source"][1:] or [0])[0] in (16, 17) else _pp(s).get("kParamOut", 100.0) / 100.0),
                         "bipolar": bool(_pp(s).get("kParamBipolar", 0.0)),
                         "curve": route_curve(s), "aux_curve": aux_curve(s)})
    macros = [_pp(tree.get(f"Macro{i}", {})).get("kParamValue", 0.0) for i in range(8)]
    # A route's second source (source = [main, aux]) scales its depth by the aux's value x: a macro at its saved value (a route gated by a macro at 0 does nothing: Pad - MIND's filter
    # envelope, which left its filter closed in Serum while the engine opened it), velocity (x = velocity / 127) and note number (x = note / 127) at their nominal values
    # (calibration/probe_aux_source.py), a random aux (21, 22: uniform per note) at its mean, and when the slot has an aux curve (kParamAuxCurveData) the curve's value in place
    # of x (calibration/probe_aux_curve.py; it explains Lead - Tonic Walter's route, which at velocity 100 is almost the unscaled one: a plain 100/127 made it 25 dB too dark, and the
    # old fix left every velocity / note aux at full depth). 1 (the mod wheel), 18, 19 and 24 are closed at rest (the route is off), 20 and 23 constant (left).
    for m in mods:
        aux = m.get("aux") or 0
        if aux == 1 or aux in (18, 19, 24):
            m["amount_pct"] = 0.0                  # the mod wheel (1) and the sources closed at rest (probe_aux_source.py): the route does nothing in a static preset
        elif 25 <= aux <= 32 or aux in (16, 17, 21, 22) or 2 <= aux <= 15:
            # a macro at its saved value, velocity / note at their nominal values, a random aux or an envelope / LFO gate at its mean (two vibrato routes: Keys - Billboard's
            # random-gated vibrato was carried at full depth), each through the route's aux curve when it has one (aux_scale)
            m["amount_pct"] *= aux_scale(aux, m.get("aux_curve"), macros)
    # An envelope's Sustain is a plain 0..1 knob, so a macro, velocity or note route into it is folded in at the saved macro value or the nominal velocity / note
    # (Bass - Let It Go: velocity into the filter envelope's sustain, +73 %, which at velocity 100 holds the filter at 0.57 where the preset's own value is 0).
    # The attack, decay and release knobs are logarithmic times and are left as stored.
    for m in mods:
        if m["dest_type"] == "Env" and m["dest_param"] == "kParamSustain" and isinstance(m["dest_id"], int) and 0 <= m["dest_id"] < len(envs):
            src = m["source"]
            x = (fxmap.macro_value(m, macros) if 25 <= src <= 32 else VELOCITY_NOMINAL / 127.0 if src == 16 else 60 / 127.0 if src == 17 else None)
            if x is not None:
                envs[m["dest_id"]]["sustain"] = min(max(envs[m["dest_id"]]["sustain"] + m["amount_pct"] / 100.0 * ((x - 0.5) if m["bipolar"] else x), 0.0), 1.0)
    lfos = []
    for i in range(10):
        node = tree.get(f"LFO{i}", {}) or {}
        pp, cv = _pp(node), (node.get("curveData") or {})
        lfos.append({"rate": pp.get("kParamRate"), "sync": pp.get("kParamBeatSync"), "mode": pp.get("kParamDefaultMode"), "type": pp.get("kParamType"),
                     "x": cv.get("xVals"), "y": cv.get("yVals"), "c": cv.get("curveVals"), "name": node.get("curveDisplayName")})
    # Serum's routing slots 0..4 are osc A, B, C, noise, sub. Measured with a closed filter:
    # only A reaches the filter by default, the others bypass it unless routed there.
    routed, balance, none, out, bus = {}, {}, {}, {}, {}
    for slot, source in enumerate((0, 1, 2, 3, 4)):
        rs = _pp(tree.get(f"RoutingSlot{slot}", {}))
        dest = rs.get("kParamRoutingDest")
        routed[source] = dest == "kRoutingDestFilter" if dest else source == 0
        # the balance between the two voice filters, -1 (Filter 1 only, what an unwritten one means) to +1 (Filter 2 only); stored in percent with 0 the
        # middle, and each filter gets (1 -/+ t) / 2 of the source (calibration/probe_filter2.py)
        balance[source] = _clamp(rs.get("kParamFilterBalance", -100.0) / 100.0, -1.0, 1.0)
        none[source] = dest == "kRoutingDestNone"
        out[source] = dest == "kRoutingDestDirect"                       # Direct goes past the effects racks as well; Master is only past the voice filter (the rack-0 Filter probe)
        bus[source] = [rs.get("kParamFXBus1Level", 0.0), rs.get("kParamFXBus2Level", 0.0)]       # the send to FX Bus 1 / 2, percent (an unwritten one is 0: probe_bus.py)
    # Slots 5 and 6 are the outputs of Voice Filter 1 and 2 (probe: slot 5 = None silences a source routed through Filter 1): Master (unwritten) sends the filtered signal to the main
    # rack, Direct past the racks, None nowhere, Filter into the other filter in series; each has sends to the buses of its own.
    fdest = {}
    for slot in (5, 6):
        rs = _pp(tree.get(f"RoutingSlot{slot}", {}))
        d = rs.get("kParamRoutingDest")
        fdest[slot] = {"kRoutingDestNone": 1, "kRoutingDestDirect": 2, "kRoutingDestFilter": 3}.get(d, 0)
        bus[slot] = [rs.get("kParamFXBus1Level", 0.0), rs.get("kParamFXBus2Level", 0.0)]
    # A macro, velocity or note route into a send level moves it by that fraction of the full range (folded in at the saved macro / nominal velocity, as the
    # other static folds); another source on a send level is left at the stored level.
    for m in mods:
        if m["dest_type"] == "RoutingSlot" and m["dest_param"] in ("kParamFXBus1Level", "kParamFXBus2Level") and m["dest_id"] in bus:
            src = m["source"]
            x = (fxmap.macro_value(m, macros) if 25 <= src <= 32 else VELOCITY_NOMINAL / 127.0 if src == 16 else 60 / 127.0 if src == 17 else None)
            if x is not None:
                b = int(m["dest_param"][-6]) - 1
                bus[m["dest_id"]][b] = _clamp(bus[m["dest_id"]][b] + m["amount_pct"] * ((x - 0.5) if m["bipolar"] else x), 0.0, 100.0)
    flt2 = _pp(tree.get("VoiceFilter1", {}))
    # A voice filter's Wet (percent, an unwritten one is fully wet) is a dry / wet mix of the filter: at 0 the signal goes past it untouched (calibration scratch
    # vf_wet_ab.py: Serum with such a filter on against the same render with it disabled, octave bands within 2 dB; the Factory's "filter on / off" macros store Wet 0
    # and a +100 % macro route). The routes into it are folded at the saved macro position, the nominal velocity and note, and the mod wheel at 0; an envelope or LFO route
    # leaves the filter as it is (dynamic). Only a filter whose Wet ends under 1 % and stays there is treated as off.
    def vf_wet(i, node):
        wet, dynamic = float(node.get("kParamWet", 100.0)), False
        for m in mods:
            if m["dest_type"] == "VoiceFilter" and m["dest_param"] == "kParamWet" and m["dest_id"] == i:
                src = m["source"]
                x = (fxmap.macro_value(m, macros) if 25 <= src <= 32 else VELOCITY_NOMINAL / 127.0 if src == 16 else 60 / 127.0 if src == 17 else 0.0 if src == 1 else None)
                if x is None:
                    dynamic = True
                else:
                    wet += m["amount_pct"] * ((x - 0.5) if m["bipolar"] else x)
        return _clamp(wet, 0.0, 100.0), dynamic
    wet1, dyn1 = vf_wet(0, flt)
    wet2, dyn2 = vf_wet(1, flt2)
    off1, off2 = wet1 < 1.0 and not dyn1, wet2 < 1.0 and not dyn2
    fx = [k[2:] for r in (tree.get("FXRack0", {}).get("FX", []) or [])
          for k in r if k.startswith("FX")]
    # The main rack's nodes as stored, for fxmap.py. The two bus racks are the sources' sends (bus_nodes below).
    fx_nodes = [dict(n) for n in (tree.get("FXRack0", {}).get("FX", []) or [])]
    bus_fx = sum(len(tree.get(f"FXRack{i}", {}).get("FX", []) or []) for i in (1, 2))
    return {
        "name": header.get("presetName", ""), "tags": header.get("tags", []),
        "serum_version": header.get("productVersion"),
        "mono": bool(g.get("kParamMonoToggle", 0)) or g.get("kParamPolyCount") == 1.0, "legato": bool(g.get("kParamLegato", 0)),
        "master": g.get("kParamMasterVolume"),
        "porta_s": g.get("kParamPortamentoTime") if (g.get("kParamPortaAlways") or g.get("kParamMonoToggle")) else None,
        "oscs": oscs,
        "sub": {"enabled": bool(_pp(sub_node).get("kParamEnable", 0)),
                "level": _pp(sub_node).get("kParamVolume", DEFAULTS["osc_level"]),
                "octave": _pp(sub_node).get("kParamOctave", 0.0),
                "shape": _pp(sub_node.get("SubOsc4", {})).get("kParamShape", "kSine")},
        # A noise slot can hold any sample. One played once and shorter than a quarter of a second is a click (a kick attack), not a
        # noise: the engine's continuous noise at that level would drown the sound in hiss, so it is left out.
        "noise": {"enabled": bool(_pp(noise_node).get("kParamEnable", 0)) and not _noise_click(noise_node),
                  "click": bool(_pp(noise_node).get("kParamEnable", 0)) and _noise_click(noise_node),
                  "sample": SE.noise_sample(tree),
                  "sample_name": (noise_node.get("NoiseOsc3") or {}).get("relativePathToNoiseSample") if isinstance(noise_node.get("NoiseOsc3"), dict) else noise_node.get("relativePathToNoiseSample"),
                  "type": (_pp(noise_node.get("NoiseOsc3", {})) or {}).get("kParamNoiseType") if isinstance(noise_node.get("NoiseOsc3"), dict) else None,
                  "color": (_pp(noise_node.get("NoiseOsc3", {})) or {}).get("kParamColor") if isinstance(noise_node.get("NoiseOsc3"), dict) else None,
                  "level": _pp(noise_node).get("kParamVolume", DEFAULTS["osc_level"])},
        "filter": {"enabled": bool(flt.get("kParamEnable", 0)) and not off1, "wet": wet1,
                   "model": flt.get("kParamType", DEFAULT_VOICE_FILTER),
                   "cutoff_norm": flt.get("kParamFreq", DEFAULTS["cutoff"]),
                   "cutoff_hz": serum_cutoff_hz(flt.get("kParamFreq", DEFAULTS["cutoff"])),
                   "reso_pct": flt.get("kParamReso", DEFAULTS["reso"]),
                   "drive_pct": flt.get("kParamDrive", 0.0),
                   "keytrack": flt.get("kParamKeyTrack", 0.0),
                   "var": flt.get("kParamVar")},
        "filter2_enabled": bool(flt2.get("kParamEnable", 0)) and not off2,
        "filter2": {"enabled": bool(flt2.get("kParamEnable", 0)) and not off2, "wet": wet2,
                    "model": flt2.get("kParamType", DEFAULT_VOICE_FILTER),
                    "cutoff_norm": flt2.get("kParamFreq", DEFAULTS["cutoff"]),
                    "reso_pct": flt2.get("kParamReso", DEFAULTS["reso"]),
                    "drive_pct": flt2.get("kParamDrive", 0.0),
                    "keytrack": flt2.get("kParamKeyTrack", 0.0),
                    "var": flt2.get("kParamVar")},
        "balance": balance,
        "envs": envs, "mods": mods, "macros": macros, "lfos": lfos, "fx": fx, "routed": routed,
        "fx_nodes": fx_nodes, "bus_fx": bus_fx,
        "route_none": none, "route_out": out, "bus": bus, "filter_dest": fdest, "bus_nodes": [[dict(n) for n in (tree.get(f"FXRack{i}", {}).get("FX", []) or [])] for i in (1, 2)],
    }


# ---------------------------------------------------------------------------
# Stage 2: .vital export (informational; see module docstring)
# ---------------------------------------------------------------------------
def to_vital(ph: dict) -> dict:
    s: dict = {}
    for o in ph["oscs"]:
        n = o["id"] + 1
        s[f"osc_{n}_on"] = float(o["enabled"] and o["kind"] == "kOsc_WT")
        s[f"osc_{n}_level"] = o["level"] ** 2
        s[f"osc_{n}_transpose"] = o["octave"] * 12 + o["semitone"]
        s[f"osc_{n}_tune"] = o["fine_cents"] / 100.0
        s[f"osc_{n}_unison_voices"] = float(o["unison"])
        s[f"osc_{n}_unison_detune"] = o["detune"]
        s[f"osc_{n}_wave_frame"] = _clamp(o["table_pos"] - 1, 0, 255)
    f = ph["filter"]
    s["filter_1_on"] = float(f["enabled"])
    s["filter_1_cutoff"] = 69 + 12 * math.log2(f["cutoff_hz"] / 440)
    s["filter_1_resonance"] = f["reso_pct"] / 100.0
    for n, e in enumerate(ph["envs"][:3], 1):
        s[f"env_{n}_attack"] = e["attack"] ** 0.25
        s[f"env_{n}_decay"] = e["decay"] ** 0.25
        s[f"env_{n}_sustain"] = e["sustain"] ** 2
        s[f"env_{n}_release"] = e["release"] ** 0.25
    return {"author": "converted", "preset_name": ph["name"],
            "preset_style": (ph["name"].split(" - ")[0] if " - " in ph["name"] else ""),
            "comments": "Converted from Serum 2 by deephouse2move.py (not load-tested in Vital)",
            "settings": s, "serum2move": ph}


# ---------------------------------------------------------------------------
# Stage 3: Vital JSON -> Tablor
# ---------------------------------------------------------------------------
CARRY_BUSES = True                        # the sources' sends to the two FX buses and the buses' racks (engine: Voice sends, Engine::fxBus); off: the old rack-0-only conversion
TABLOR_DEFAULTS = {
    "flt1_out": 0, "flt2_out": 0, "flt1_bus1": 0, "flt1_bus2": 0, "flt2_bus1": 0, "flt2_bus2": 0,
    "wt1_bus1": 0, "wt1_bus2": 0, "wt2_bus1": 0, "wt2_bus2": 0, "wt3_bus1": 0, "wt3_bus2": 0, "sub_bus1": 0, "sub_bus2": 0, "noise_bus1": 0, "noise_bus2": 0, "smp_bus1": 0, "smp_bus2": 0,
    "wt1_post": 0, "wt2_post": 0, "wt3_post": 0, "sub_post": 0, "noise_post": 0,
    "wt1_bal": 0, "wt2_bal": 0, "wt3_bal": 0, "sub_bal": 0, "noise_bal": 0, "smp_bal": 0,
    "flt2_type": 1, "flt2_freq": 127, "flt2_res": 0, "flt2_env": 64, "flt2_drive": 0, "flt2_key": 0, "flt_gain": 0, "flt2_gain": 0,
    "smp_set": "", "smp_level": 0, "smp_tune": 0, "smp_fine": 0, "smp_post": 0, "smp_fixed": 0,
    "wt1_pos": 0, "wt2_pos": 0, "wt1_level": 100, "wt2_level": 0, "wt1_tune": 0, "wt2_tune": 0,
    "wt1_uni": 1, "wt1_detune": 0, "wt1_spread": 0, "wt1_pan": 64,
    "wt2_uni": 1, "wt2_detune": 0, "wt2_spread": 0, "wt2_pan": 64,
    "wt3_pos": 0, "wt3_level": 0, "wt3_tune": 0, "wt3_uni": 1, "wt3_detune": 0, "wt3_spread": 0, "wt3_pan": 64,
    "wt1_sync": 0, "wt2_sync": 0, "wt3_sync": 0,
    "wt1_bend": 64, "wt1_formant": 64, "wt2_bend": 64, "wt2_formant": 64, "sub_tune": -12,
    "flt_freq": 127, "flt_res": 0, "flt_type": 1, "flt_drive": 0, "sub_level": 0, "sub_wave": 0,
    "noise_level": 0, "noise_type": 2, "noise_hp": 0, "noise_lp": 127,
    "vca_a": 51, "vca_d": 64, "vca_s": 102, "vca_r": 64, "flt_a": 51, "flt_d": 64, "flt_s": 102, "flt_r": 64,
    "me1_a": 0, "me1_d": 64, "me1_s": 0, "me1_r": 64, "me2_a": 0, "me2_d": 64, "me2_s": 0, "me2_r": 64,
    "vca_vel": 0, "vca_vlaw": 1, "flt_key": 0, "flt_vel": 0, "flt_env": 64,
    "me1_dst": 0, "me1_amt": 64, "me2_dst": 0, "me2_amt": 64,
    **{f"lfo{n}_{k}": v for n in range(1, 5) for k, v in ({"rate": 60, "mode": 0} | {f"s{j}": round(127 * (0.5 + 0.5 * math.sin(2 * math.pi * j / 16))) for j in range(16)}).items()},
    **{f"lr{r}_{k}": v for r in range(1, 9) for k, v in (("src", 0), ("dst", 0), ("amt", 64), ("bip", 0))},
    "voice_mode": 0, "voices": 8, "glide": 0, "glide_mode": 0, "legato": 0, "pb_range": 2, "volume": 100,
}
# A voice filter whose Type is not written plays a 12 dB/octave low-pass (noise through it, probe_filter2_default.py: the same slope as MgL12 to within
# 0.3 dB at 12.8 kHz, where MgL24 is 60 dB lower); the converter had assumed MgL24.
DEFAULT_VOICE_FILTER = "MgL12"
VELOCITY_NOMINAL = 100                    # the velocity a static preset's velocity routes are taken at (the engine's vca_vel default is the same 100)
# Serum's noise oscillator (calibration/probe_noise_color.py, fitted: every White / Pink / Brown at Color 0.1 .. 0.8 within 0.4-1.6 dB): Type is the spectrum (White 3 dB per
# octave up in an octave band, Pink flat, Brown 3 dB down), Color 0.5 is neutral, below it a 12 dB/octave low-pass whose corner is 460 Hz at 0.2 and x4.5 per 0.1, above it
# a 12 dB/octave high-pass with the same corners from 0.7 (0.6: 100 Hz, 0.8: 2 kHz). (Color 0.9 and up adds a rising tilt that this leaves out; 1.0 is silent.) The unwritten type
# is a noise that is flat to 1.6 kHz and rises 3-5 dB/octave above (+14 dB at 12.8 kHz re 800 Hz): White stands in. Geiger is sparse clicks, left as before.
NOISE_COLOR_NEUTRAL, NOISE_CORNER_AT = 0.5, 460.0
def noise_color_pots(color: float | None) -> tuple[int, int]:
    """(noise_hp, noise_lp) pots for a Serum noise Color."""
    if color is None or abs(color - NOISE_COLOR_NEUTRAL) < 0.04:
        return 0, 127
    pot = lambda hz: int(round(_clamp(127.0 * math.log(max(hz, 20.0) / 20.0) / math.log(1000.0), 0, 127)))
    if color < NOISE_COLOR_NEUTRAL:
        hz = NOISE_CORNER_AT * 4.5 ** ((color - 0.2) / 0.1)
        return 0, (127 if hz >= 19000 else pot(hz))
    hz = NOISE_CORNER_AT * 4.5 ** ((color - 0.7) / 0.1)
    return (0 if hz <= 24 else max(pot(hz), 1)), 127


def _interp(x, xs, ys):
    if x <= xs[0]: return ys[0]
    for i in range(1, len(xs)):
        if x <= xs[i]: return ys[i - 1] + (ys[i] - ys[i - 1]) * (x - xs[i - 1]) / (xs[i] - xs[i - 1])
    return ys[-1]


NOISE_TYPE_ENGINE = {"White": 0, "Pink": 1, "Brown": 4}


def noise_type_for(sample: str | None) -> tuple[int, str | None]:
    """The engine's noise type for the noise slot's sample. Unwritten is pink noise through a 12 dB high-pass near 950 Hz (measured on Serum,
    probe_noise_default.py), the HP12 Pink samples the same near 1.2 kHz; a plain White/Pink sample is that noise. Any other sample (a hum, a
    breath, a crackle) is not noise: pink stands in for it."""
    if not sample:
        return 2, None
    name = sample.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if "hp12 pink" in name:
        return 3, None
    if "white" in name:
        return 0, None
    if "pink" in name:
        return 1, None
    return 1, f"noise osc plays '{sample.rsplit('/', 1)[-1]}' in Serum, which is not noise; pink noise stands in"


ME_DST = {"WT1 Pos": 1, "WT2 Pos": 2, "WT1 Level": 3, "WT2 Level": 4, "WT1 Tune": 5, "WT2 Tune": 6, "WT1 Pan": 11, "WT2 Pan": 12,
          "Filter Freq": 13, "Sub Level": 15, "Noise Level": 16,
          "WT3 Pos": 18, "WT3 Level": 19, "WT1 Sync": 20, "WT2 Sync": 21, "WT3 Sync": 22, "Filter 2 Freq": 23}
STREAM_BUDGET = 64         # kStreamBudget in voice.h
MIN_POLY_VOICES = 4        # a poly preset still has to play a chord
SILENT_LEVEL = 0.02        # a stored level below this is off
MOD_LEVEL_HOLD = 0.7       # assumption: where a level modulator sits on average

# Serum-to-Tablor scaling laws. test_render_calibration.py measures these against
# Serum 2 itself and rewrites this table; see calibration/ for how each was fitted.
# master_db0 None means the preset's master volume is not mapped at all.
CAL = {
    "level_exp": 0.4995,
    "master_db0": 9.3763,
    "master_slope": 19.9112,
    "master_default_db": -2.4186,
    "cutoff_scale": 1.0,
    "cutoff_offset": -4.0,
    "res_exp": 0.3,
    "res_scale": 1.0,
    "attack_scale": 0.7551,
    "decay_scale": 1.7993,
    "release_scale": 1.6903,
    "sustain_exp": 1.9,
    "flt_env_scale": 1.0,
    "detune_scale": 3.5,
    "detune_exp": 1.2,
    "detune_wid": [[0.0, 0.163], [25.0, 0.4], [50.0, 0.822], [75.0, 1.002], [100.0, 1.052]],
    "width_scale": 1.1,
    "stereo_default": 100.0,
    "unison_gain_exp": 0.0,
    "sub_scale": 0.8,
    "key_scale": 1.0,
    "key_pivot": 31.5,
    "smp_scale": 1.0,
    "attack_curve": [[30.0, 1.4733], [40.0, 1.4146], [50.0, 1.3125], [62.0, 0.7551], [75.0, 0.367]],
    "decay_curve": [[30.0, 2.1034], [40.0, 2.1034], [50.0, 1.9576], [60.0, 1.5731], [67.0, 1.2642], [75.0, 1.0191], [85.0, 0.57], [96.0, 0.3188]],
    "release_curve": [[30.0, 2.1034], [40.0, 2.1034], [50.0, 1.9576], [60.0, 1.5731], [67.0, 1.2642], [75.0, 0.8799], [85.0, 0.5305], [96.0, 0.3188]],
    "drive_exp": 1.0,
    "drive_scale": 1.0,
}
SUB_WAVE = {"kSine": 0, "kTriangle": 1, "kSaw": 2, "kSquare": 3, "kRoundRect": 0, "kPulse": 4}
# Serum filter model -> (Tablor flt_type index, exact?)
FILTER_MAP = {
    "MgL24": (1, True), "L24": (1, True),
    "MgL18": (1, False), "L18": (1, False), "DirtyMg": (1, False), "LadderAcid": (1, False), "ZDF_A": (1, False),
    "MgL12": (0, False), "L12": (0, True), "MgL6": (0, False), "L6": (0, False),
    "H24": (3, True), "H12": (2, True), "H6": (2, False), "B24": (5, True), "B12": (4, True),
    "LadderMg": (1, False), "LadderEMS": (1, False), "LN12": (0, False),     # ladders and a 12 dB low-pass: the nearest engine filter
}
# Voice filter laws, measured by probe_vfilter.py / probe_vreso.py: a held saw through each Serum filter model at several cutoffs and
# resonances, and the engine's cutoff and resonance pots searched for the pair whose harmonic transfer matches. Cutoff: the pots to add
# to the closed-form law (by the cutoff knob, linear between the points) - an 18 dB model needs an open 24 dB engine filter to match, a
# 6 dB model an open 12 dB one. Resonance: Serum's Moog-type ladders show no peak up to 40 %, the band-pass types need a lot.
FILTER_CUTOFF_DELTA = {
    "MgL24": [(0.35, 1.5), (0.7, 1.5)], "L24": [(0.35, 1.5), (0.7, 1.5)],
    "MgL18": [(0.35, 7.0), (0.7, 7.0)], "L18": [(0.35, 7.0), (0.7, 7.0)],
    "L12": [(0.35, 3.0), (0.7, 2.0)], "MgL12": [(0.35, 3.0), (0.7, 2.0)],
    "MgL6": [(0.35, 31.0), (0.45, 25.0), (0.55, 20.0), (0.7, 12.0)], "L6": [(0.35, 31.0), (0.45, 25.0), (0.55, 20.0), (0.7, 12.0)],
    "H24": [(0.3, -1.5), (0.4, 0.8), (0.5, 1.6), (0.6, -0.1), (0.7, -1.7), (0.8, 1.6)],       # noise through the filter (calibration/probe_hp24_low.py, fits within 0.5-1.5 dB);
    "H12": [(0.3, -1.5), (0.4, 0.8), (0.5, 1.6), (0.6, -0.1), (0.7, -1.7), (0.8, 1.6)],       # the saw-harmonic fit these replace (+7) read the first harmonics, too few points
    "B24": [(0.35, 0.0), (0.45, 3.0), (0.55, 4.0), (0.7, 4.0)], "B12": [(0.35, 0.0), (0.45, 3.0), (0.55, 4.0), (0.7, 4.0)],
}
FILTER_RES_POT = {            # resonance percentage -> engine resonance pot, by Serum model (default: no resonance)
    "L12": [(0.0, 0), (12.0, 12), (20.0, 0)], "L24": [(0.0, 0), (12.0, 12), (20.0, 0)],
    "B24": [(0.0, 48), (5.0, 60), (12.0, 72), (20.0, 80), (30.0, 88), (40.0, 96)],
    "B12": [(0.0, 48), (5.0, 60), (12.0, 72), (20.0, 80), (30.0, 88), (40.0, 96)],
}


# Models fitted one by one against the engine's filter types (calibration/fit_filter_model.py): {model: {"type", "delta": [[cutoff, pots]], "poor", "rms_db"}}
FILTER_MODELS_FILE = Path(__file__).resolve().parent / "calibration/filter_models.json"
FILTER_MODELS: dict = json.loads(FILTER_MODELS_FILE.read_text(encoding="utf-8")) if FILTER_MODELS_FILE.exists() else {}


def filter_model_key(model: str, var) -> str:
    """The fitted entry for a filter: models with a Var knob (LH6, comb, formant) are fitted at the Var values in use, 'LH6@80'."""
    if var is None:
        return model
    near = [(abs(float(k.partition("@")[2]) - var), k) for k in FILTER_MODELS if k.partition("@")[0] == model and "@" in k]
    near = [x for x in near if x[0] <= 15]
    return min(near)[1] if near else model


def filter_cutoff_delta(model: str, cut: float) -> float:
    if model in FILTER_MODELS:
        return interp([tuple(x) for x in FILTER_MODELS[model]["delta"]], cut)
    return interp(FILTER_CUTOFF_DELTA.get(model, [(0.0, 0.0)]), cut)


FILTER_RES_FILE = Path(__file__).resolve().parent / "calibration/filter_res.json"
FILTER_RES: dict = json.loads(FILTER_RES_FILE.read_text(encoding="utf-8")) if FILTER_RES_FILE.exists() else {}      # calibration/fit_filter_res.py


def filter_res_pot(model: str, pct: float) -> int:
    if model in FILTER_RES:                                       # measured against Serum, model by model
        return round(_clamp(interp([tuple(x) for x in FILTER_RES[model]["table"]], pct), 0, 127))
    return round(_clamp(interp(FILTER_RES_POT.get(model, [(0.0, 0)]), pct), 0, 127))


# Serum's Bend amount (0..1) against the engine's bend pot above its centre 64, from the harmonics of a sine bent in both (calibration/bend probe:
# the engine's bend is gentle near 0 and steep near 1; the straight line 64 + 63 x amount put 3x too many harmonics on small amounts).
# Serum's filter Drive (percent) against the engine's flt_drive pot, from the harmonics a bass sine and a saw gain through an open MgL24 in both
# (calibration/probe_drive.py). The straight line pot = 1.27 x percent had the engine distorting 2-4x too hard up to 50 %: AVAION's 10 % drive put a
# -24 dB third harmonic on a 16 Hz sine where Serum's is below -140 dB.
FILTER_DRIVE_POT = [(0.0, 0.0), (10.0, 4.0), (25.0, 8.0), (50.0, 30.0), (75.0, 65.0), (100.0, 90.0)]
# What the voice filter's Drive does in Serum: a plain gain, every harmonic by the same amount, no distortion at ordinary levels (calibration/probe_vdrive_gain.py
# and probe_vdrive_saw.py). On a quiet noise it is +3.4 / 7.6 / 14.1 / 19.6 / 24 dB at 10 / 25 / 50 / 75 / 100 %; on a loud saw it stops near +13 dB. The table is
# the saw's (oscillator levels are loud); the engine's gain stage after the filter supplies it and its saturating Drive pot stays at 0 (FILTER_DRIVE_POT is no longer used).
FILTER_DRIVE_GAIN_DB = [(0.0, 0.0), (10.0, 3.3), (25.0, 7.6), (50.0, 12.5), (75.0, 13.0), (100.0, 13.0)]
# The engine's saturating Drive pot (FILTER_DRIVE_POT) stays: it is what puts the harmonics a loud low note gets from Serum's ladder (Bass - AVAION's 320 Hz band
# is 12 dB short without it). Its gain is a little under Serum's, so the gain stage after the filter supplies the difference: Serum's gain above (on a loud saw)
# minus the engine's with that pot on a noise at volume 0.75 (calibration/probe_vdrive_gain.py).
FILTER_DRIVE_MAKEUP_DB = [(0.0, 0.0), (10.0, 2.8), (25.0, 5.7), (50.0, 10.6), (75.0, 11.1), (100.0, 11.1)]
# Serum's drive makes no harmonics on a saw at 50-100 % (every harmonic moves by the same dB), so the engine's saturating pot stops at its 25 % value.
FILTER_DRIVE_POT_USED = [(0.0, 0.0), (10.0, 4.0), (25.0, 8.0), (100.0, 8.0)]
BEND_POT = [(0.0, 64.0), (0.03, 65.0), (0.12, 65.0), (0.2, 66.0), (0.3, 67.0), (0.4, 70.0), (0.5, 74.0), (0.7, 86.0), (0.85, 100.0), (1.0, 116.0)]
BEND_WARPS = {"kBendPos": +1, "kBendPosNeg": +1, "kBendNeg": -1, "kPD_OSC": +1, "kASYMPos": +1, "kPWM": +1}
# Serum's PWM warp on a saw (calibration/probe_pwm.py): the harmonics 2..9 match the engine's Bend at these pots to within 1-2.5 dB (amount 0.2: pot 90 or 38,
# 0.4: 104 or 24, 0.6: 122 or 6; 0.8 is beyond the engine's range). A pulse-width warp and a bend are both an asymmetric phase map; the phases differ, the magnitudes do not.
# Serum's Sync warp on a saw (calibration/probe_sync.py): the engine's sync pot whose harmonics are closest, 0.2 to 0.7 within 0.3-2.2 dB. Amounts under 0.15 are no sync.
SYNC_POT = [(0.0, 0.0), (0.15, 0.0), (0.2, 6.0), (0.3, 20.0), (0.4, 44.0), (0.5, 64.0), (0.6, 88.0), (0.7, 108.0), (0.8, 124.0), (1.0, 127.0)]
PWM_BEND_POT = [(0.0, 64.0), (0.2, 90.0), (0.4, 104.0), (0.6, 122.0), (0.8, 127.0), (1.0, 127.0)]
WARP_NOISE_FLOOR = 0.02    # a warp mode with ~0 amount is not audible


def pot_time(t: float) -> int:        # Tablor potEnvTime inverse: 1 ms .. 10 s
    return round(_clamp((math.log10(_clamp(t, 0.001, 10.0)) + 3) / 4 * 127, 0, 127))


def pot_attack(t: float) -> int:      # Tablor potAttackTime inverse: 0.5 ms + 10 s * x^5
    return round(127 * min(1.0, max(0.0, (t - 0.0005) / 10.0) ** 0.2))


def interp(table: list, x: float) -> float:
    """Piecewise-linear lookup in a measured [[x, y], ...] table, flat past its ends."""
    if x <= table[0][0]:
        return table[0][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return table[-1][1]


def env_time(seconds: float, curve, table: list, absent: float) -> float:
    """Serum's curve moves a stage's real length a long way: a 1 s decay falls 20 dB in
    1.04 s at curve 30 and 0.04 s at curve 100. Tablor has one shape, so length is
    what can be matched. A curve the preset does not store is Serum's default."""
    return seconds * (interp(table, curve) if table and curve is not None else absent)


def pot_env(e: dict, decay_mult: float = 1.0) -> tuple[int, int, int, int]:    # every Tablor envelope reads attack this way
    return (pot_attack(env_time(e["attack"], e.get("curve1"), CAL["attack_curve"], CAL["attack_scale"])),
            pot_time(env_time(e["decay"], e.get("curve2"), CAL["decay_curve"], CAL["decay_scale"]) * decay_mult),
            round(127 * _clamp(e["sustain"], 0, 1) ** CAL["sustain_exp"]),
            pot_time(env_time(e["release"], e.get("curve3"), CAL["release_curve"], CAL["release_scale"]) * decay_mult))


FILTER_FITS = Path(__file__).resolve().parent / "calibration/filter_fits.json"
WIDTH_FITS = Path(__file__).resolve().parent / "calibration/width_fits.json"
CHAIN_FITS = Path(__file__).resolve().parent / "calibration/chain_fits.json"
TUNE_FITS = Path(__file__).resolve().parent / "calibration/tune_fits.json"
STEREO_FITS = Path(__file__).resolve().parent / "calibration/stereo_fits.json"
LEVEL_FITS = Path(__file__).resolve().parent / "calibration/level_fits.json"
# FAITHFUL: the preset is translated from Serum's own settings and nothing is tuned per preset against a loss function.
# What is calibrated is the engine's unit conversions (global constants, measured by playing test patches through Serum:
# CAL, calibration/lfo_scales.json) and each preset's loudness (APPLY_LEVEL_FITS). The per-preset fits (filter cutoff and
# envelope, FX width, amp envelope, shelves, stereo width) stay on disk for comparison and are applied only with FAITHFUL = False.
FAITHFUL = True
# HYBRID_DRY_FITS: the one per-preset correction kept on top of the faithful translation. The closed-form translation of the VOICE
# filter (cutoff, envelope depth and decay) is off per patch, and the dry fits of exactly those knobs, and of the unison spread,
# were validated on notes they were not fitted on (filter spectrum error 18.9 -> 8.6 dB; width 4.9 -> 1.5 dB). They touch no
# effect, LFO or distortion: structure is still translated unconditionally. Retire this when the voice filter's cutoff law and
# envelope curves are calibrated by probe instead.
HYBRID_DRY_FITS = False
APPLY_FILTER_FITS = (not FAITHFUL) or HYBRID_DRY_FITS    # the fit scripts in calibration/ turn these off to see the mapper's own values
APPLY_WIDTH_FITS = (not FAITHFUL) or HYBRID_DRY_FITS
APPLY_CHAIN_FITS = not FAITHFUL
APPLY_TUNE_FITS = not FAITHFUL
APPLY_STEREO_FITS = not FAITHFUL
APPLY_LEVEL_FITS = True
_fits: dict = {}


def _fit_for(path: Path, name: str):
    if path not in _fits:
        _fits[path] = ({fxmap.norm_name(k): v for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
                       if path.exists() else {})
    fit = _fits[path].get(fxmap.norm_name(name))
    return fit if fit and fit.get("kept") else None


def apply_filter_fit(name: str, P: dict) -> list:
    """The cutoff, envelope depth and envelope decay measured for this preset against Serum
    (calibration/fit_filter.py), in place of the mapper's closed-form ones. The closed form is right
    on average and wrong for any one patch: it cannot see the envelope's curve, which decides how
    fast and how far the filter closes. A fit is only used if the mapper still produces the values
    it started from, so a changed mapper or table cannot apply a stale one."""
    fit = _fit_for(FILTER_FITS, name) if APPLY_FILTER_FITS else None
    if not fit:
        return []
    if any(P[k] != v for k, v in fit["start"].items()):
        return ["filter fit not applied: the mapper no longer gives the values it was fitted from"]
    P.update(fit["fit"])
    return ["filter fitted against Serum: " + ", ".join(
        f"{k[4:]} {fit['start'][k]}->{fit['fit'][k]}" for k in ("flt_freq", "flt_env", "flt_d", "flt_res") if k in fit["fit"])]


def apply_width_fit(name: str, P: dict) -> list:
    """The unison spread measured for this preset against Serum (calibration/fit_width.py): the
    side/mid of a stack is not a function of voice count alone, so a per-preset scale on the
    spread knobs matches what a law cannot. Same staleness guard as the filter fit."""
    fit = _fit_for(WIDTH_FITS, name) if APPLY_WIDTH_FITS else None
    if not fit:
        return []
    if any(P[k] != v for k, v in fit["start"].items()):
        return ["width fit not applied: the mapper no longer gives the values it was fitted from"]
    P.update(fit["fit"])
    return [f"unison width fitted against Serum (spread x{fit['scale']:.2f}, side/mid {fit['before_db']:+.1f} -> {fit['after_db']:+.1f} dB, Serum {fit['serum_db']:+.1f})"]


def apply_chain_fit(name: str, P: dict) -> list:
    """Filter and stereo-FX knobs measured against Serum's FULL chain (calibration/fit_chain.py),
    after the FX block has set its own: the dry fits above matched the oscillators alone, and a patch
    is balanced as a whole. Replaces the dry filter and width fits for the knobs it covers. Same
    staleness guard: it only applies if the mapper still produces the values it started from."""
    fit = _fit_for(CHAIN_FITS, name) if APPLY_CHAIN_FITS else None
    if not fit:
        return []
    if any(P[k] != v for k, v in fit["start"].items()):
        return ["full-chain fit not applied: the mapper no longer gives the values it was fitted from"]
    P.update(fit["fit"])
    return ["fitted against Serum's full chain: " + ", ".join(f"{k} {fit['start'][k]}->{fit['fit'][k]}" for k in fit["fit"])]


def apply_tune_fit(name: str, P: dict) -> list:
    """The amp envelope (decay, sustain, release), the two shelves and, for a sound whose Serum rack has a
    Distortion, Drive, measured against Serum's full chain (calibration/fit_tune.py). Runs last of the fits,
    on top of everything above, with the same stale-fit guard."""
    fit = _fit_for(TUNE_FITS, name) if APPLY_TUNE_FITS else None
    if not fit:
        return []
    if any(P[k] != v for k, v in fit["start"].items()):
        return ["tune fit not applied: the mapper no longer gives the values it was fitted from"]
    P.update(fit["fit"])
    return ["tuned against Serum's full chain: " + ", ".join(f"{k} {fit['start'][k]}->{fit['fit'][k]}" for k in fit["fit"])]


def apply_stereo_fit(name: str, P: dict) -> list:
    """The Master Width that puts this preset's side/mid where Serum's full chain has it (calibration/fit_stereo.py).
    Runs after the tune fit, with the same stale-fit guard."""
    fit = _fit_for(STEREO_FITS, name) if APPLY_STEREO_FITS else None
    if not fit:
        return []
    if any(P[k] != v for k, v in fit["start"].items()):
        return ["stereo fit not applied: the mapper no longer gives the values it was fitted from"]
    P.update(fit["fit"])
    return [f"stereo width matched to Serum's side/mid (master width {fit['start']['mwidth']}->{fit['fit']['mwidth']}, side/mid {fit['before_db']:+.1f} -> {fit['after_db']:+.1f} dB, Serum {fit['target_db']:+.1f})"]


SIG_NEW_ZERO = {f"{k}_bus{b}" for k in ("wt1", "wt2", "wt3", "sub", "noise", "smp", "flt1", "flt2") for b in (1, 2)} | {"flt1_out", "flt2_out"}


def params_sig(P: dict) -> str:
    """Everything that decides a preset's loudness except the volume itself."""
    skip = {"volume", "fx_on", "eq_on", "wide_on", "dly_on", "rev_on",       # the switches follow from the other values
            "decay"}                                                          # and the Decay macro is neutral in every preset
    if not P.get("dist_cal", 0) and P.get("dist_drive", 0) == 0:       # a distortion that is off does not change the level
        skip = skip | {k for k in P if k.startswith("dist_")}
    skip = skip | {"comp_on"}                                     # the switch follows from the others
    if not any(P.get(k, 0) for k in ("wt1_post", "wt2_post", "wt3_post", "sub_post", "noise_post")):
        skip = skip | {"wt1_post", "wt2_post", "wt3_post", "sub_post", "noise_post"}
    for pre in ("chor", "flan", "phas"):                        # a modulated effect that is off (no mix) does not change the level
        if not P.get(f"{pre}_on", 0) or not P.get(f"{pre}_mix", 0):
            skip = skip | {k for k in P if k.startswith(pre + "_")}
    if not P.get("smp_set"):                                    # no sample set: its other controls do nothing
        skip = skip | {k for k in P if k.startswith("smp_")}
    if not P.get("fxf_on", 0):
        skip = skip | {k for k in P if k.startswith("fxf_")}
    if not any(P.get(k, 0) for k in ("wt1_bal", "wt2_bal", "wt3_bal", "sub_bal", "noise_bal", "smp_bal")):      # no source reaches Filter 2
        skip = skip | {k for k in P if k.startswith("flt2_")}
    if P.get("comp_mix", 0) == 0 or P.get("comp_ratio", 0) == 0:  # a compressor that is off does not change the level
        skip = skip | {k for k in P if k.startswith("comp_")}
    if not any(P.get(f"lr{r}_src", 0) for r in range(1, 9)):      # nor does an LFO block with no route
        skip = skip | {k for k in P if k.startswith(("lfo", "lr"))}
    skip = skip | {k for k, v in P.items() if k in SIG_NEW_ZERO and not v}      # keys added after the fits were measured count only when they are set
    return hashlib.md5(json.dumps({k: v for k, v in sorted(P.items()) if k not in skip}).encode()).hexdigest()


def apply_level_fit(name: str, P: dict) -> list:
    """The volume that puts the preset's full-chain RMS where Serum's is (calibration/fit_level.py),
    capped so the output limiter never takes more than a couple of dB. Replaces the clip guard's
    volume. Only used if every other parameter is what it was measured with."""
    fit = _fit_for(LEVEL_FITS, name) if APPLY_LEVEL_FITS else None
    if not fit:
        return []
    if fit["sig"] != params_sig(P):
        return ["level fit not applied: other parameters changed since it was measured"]
    P["volume"] = fit["volume"]
    return [f"volume matched to Serum's full-chain RMS ({fit['start_rms_db']:+.1f} -> {fit['rms_db']:+.1f} dB vs Serum {fit['serum_rms_db']:+.1f}"
            + ((f"; volume is at maximum, {fit['shortfall_db']:.1f} dB short" if fit["volume"] >= 127 else f"; limiter-capped, {fit['shortfall_db']:.1f} dB short")
               if fit['shortfall_db'] > 1.5 else "") + ")"]


UNISON_LEVEL_FIT = False                     # the oscillator-level correction below (osc_survey.py level residuals). Off: refitting twelve mixed presets with it made the chain loss worse
                                             # (14.8 -> 15.3, 1-3 kHz error 3.8 -> 4.9 dB); the residual is against the master law, which is itself a mean over presets


def unison_level_db(unison: int) -> float:
    """How much louder an oscillator of this unison count has to be made in the engine to match Serum (calibration/osc_survey.py: each oscillator alone, level against the master law,
    148 oscillators): a single voice comes out 2.7 dB quiet, and a stack's level falls 1.6 dB per doubling of the count faster than Serum's (4.6 dB high at 2 voices, 1.9 low at 16)."""
    return 2.7 if unison <= 1 else -4.61 + 1.61 * math.log2(unison)


def pot_level(v: float, unison: int = 1, wt: bool = False) -> int:
    # the engine's level pot is squared, so a gain of g dB is a pot factor of 10^(g / 40); the correction is for the wavetable oscillators (sub, noise and sample have their own laws)
    fix = 10 ** (unison_level_db(unison) / 40.0) if (wt and UNISON_LEVEL_FIT) else 1.0
    return round(_clamp(127 * _clamp(v, 0, 1) ** CAL["level_exp"] * unison ** CAL["unison_gain_exp"] * fix, 0, 127))


MASTER_MAX = 0.8 * 10 ** (18 / 20)         # engine kMasterMax: 0.8 as before, times the +18 dB the range was extended by
POT_SCALE = math.sqrt(10 ** (18 / 20))     # a pot of the old range is this many times a pot of the new one


def master_vs_default_db(m) -> float:
    """How far a preset's Master Volume (0..1; None is unwritten, the default) is above the master the effects were measured at, in dB at the output."""
    if m is None or CAL["master_db0"] is None:
        return 0.0
    m_default = 10 ** ((CAL["master_default_db"] - CAL["master_db0"]) / CAL["master_slope"])
    return 20 * math.log10(max(m, 1e-4) / m_default)


def pot_volume(gain_db: float) -> int:                # engine: master = (pot / 127)^2 * MASTER_MAX
    return round(_clamp(127 * math.sqrt(10 ** (gain_db / 20) / MASTER_MAX), 0, 127))


def fit_unison(unison: list[int], budget: int) -> list[int]:
    """Tablor's fitUnison (voice.h): the stacks each voice actually gets."""
    want = sum(unison)
    if want <= budget:
        return list(unison)
    out = [max(1, u * budget // want) for u in unison]
    while sum(out) > budget and max(out) > 1:
        out[out.index(max(out))] -= 1
    return out


def resolve_table(ref, pack: dict[str, Path], serum_tables: Path | None,
                  wt_out: Path, subdir: str) -> tuple[str | None, str | None]:
    """Return (Move path or None, normalised ref). Copies the file into wt_out."""
    if not ref:
        return None, None
    rel = ref.replace("\\", "/").lstrip("/")
    for pre in ("S2 Tables/", "Tables/"):
        if rel.startswith(pre):
            rel = rel[len(pre):]
    # The path Serum stores names the file: a pack may ship a table with the same name as a factory one ("Basic Shapes.wav" is a sine
    # first in Analog/, something else in the Deep House pack), so the exact path under Serum's Tables folder wins over a name match.
    src = None
    if serum_tables:
        for cand in (serum_tables / rel, serum_tables / "S2 Tables" / rel):
            if cand.is_file():
                src = cand
                break
    if src is None:
        src = pack.get(Path(rel).name.lower())
        dest_rel = Path(rel).name if src else rel
    else:
        dest_rel = rel
    if src is None:
        return None, rel
    dest = wt_out / subdir / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.copyfile(src, dest)
    return f"{MOVE_WT_ROOT}/{subdir}/{Path(dest_rel).as_posix()}", rel


# A wavetable edited in Serum is stored inside the preset (WTOsc `embeddedWTData`, a flat list of 2048-sample frames) and the oscillator has no table path; 34 oscillators of the
# 626 factory presets (BA - 303 Reality's saw, ...) did, and played the engine's "Init" table (a pulse) instead. It is written out as a float WAV in Serum's own layout (a `clm `
# chunk, frame size 2048, stepped: the interpolation digit of Serum's Default Shapes) and loaded like any other table.
EMBEDDED_FRAME = 2048


def write_embedded_table(data: list, wt_out: Path, subdir: str) -> str:
    arr = [float(x) for x in data]
    n = len(arr) // EMBEDDED_FRAME * EMBEDDED_FRAME
    arr = arr[:n] if n else arr
    digest = hashlib.md5(struct.pack(f"<{len(arr)}f", *arr)).hexdigest()[:12]
    name = f"embedded_{digest}.wav"
    dest = wt_out / subdir / "embedded" / name
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        clm = b"<!>2048 01000000 wavetable (www.xferrecords.com)"
        pcm = struct.pack(f"<{len(arr)}f", *arr)
        fmt = struct.pack("<HHIIHH", 3, 1, 44100, 44100 * 4, 4, 32)
        body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"clm " + struct.pack("<I", len(clm)) + clm + (bytes(1) if len(clm) % 2 else b"") + b"data" + struct.pack("<I", len(pcm)) + pcm
        dest.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return f"{MOVE_WT_ROOT}/{subdir}/embedded/{name}"


LFO_DEFAULT_HZ = 2.0       # measured on Serum (calibration/probe_lfo2.py): a default LFO runs at 1.85-2 Hz and is a sine
LFO_MIN_ROUTE = 0.003      # a route weaker than this (a fraction of the destination's range) is not worth a slot


LFO_SCALES = Path(__file__).resolve().parent / "calibration/lfo_scales.json"
LFO_SCALE: dict = json.loads(LFO_SCALES.read_text(encoding="utf-8")) if LFO_SCALES.exists() else {}


def lfo_class(dest_name: str) -> str:
    """Which engine calibration constant governs a destination: a route's depth is converted from Serum's fraction of the
    destination's range into the engine's units, and the units differ by kind."""
    if dest_name in ("Filter Freq", "Filter 2 Freq"):
        return "cutoff"
    if dest_name.endswith("Level"):
        return "level"
    if dest_name.endswith("Pos"):
        return "pos"
    if dest_name.endswith("Tune"):
        return "tune"
    return "pan" if dest_name.endswith("Pan") else "other"


# Serum LFO curves, measured by playing constant/ramp/bent curves through Serum (probe in the transcript of calibration/probe_lfo_level.py):
# a point's stored y is measured down from the top, so the LFO's value there is 1 - y; a segment from y0 to y1 travels the fraction x**p of
# the way at x (0..1) along it, with p set by the segment's stored bend c (0.5 is straight, higher stays near y0 longer, lower leaves at once).
LFO_BEND_C = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
LFO_BEND_LOGP = [math.log(v) for v in (0.133, 0.230, 0.430, 1.0, 1.984, 3.495, 5.983)]


def lfo_bend_exponent(c: float) -> float:
    c = _clamp(c, 0.02, 0.98)
    xs, ys = LFO_BEND_C, LFO_BEND_LOGP
    if c <= xs[0]:
        return math.exp(ys[0] + (c - xs[0]) * (ys[1] - ys[0]) / (xs[1] - xs[0]))
    if c >= xs[-1]:
        return math.exp(ys[-1] + (c - xs[-1]) * (ys[-1] - ys[-2]) / (xs[-1] - xs[-2]))
    for j in range(len(xs) - 1):
        if xs[j] <= c <= xs[j + 1]:
            return math.exp(ys[j] + (c - xs[j]) / (xs[j + 1] - xs[j]) * (ys[j + 1] - ys[j]))
    return 1.0


def curve_value(xs: list, ys: list, cs: list, xq: float) -> float:
    """A Serum curve (the points joined by segments whose bend is stored) at x = xq, as the value 0..1 it gives: the stored y is measured down from the top, so the value is 1 - y.
    Used for LFO shapes and for a route's own curve (a macro route into a send level: probe on Keys - Loop, whose reverb send at macro 35 % is 0.82, not 0.35)."""
    pts = sorted(range(len(xs)), key=lambda i: (xs[i], i))
    seg = 0
    for j in range(len(pts) - 1):                               # the last segment that starts at or before xq
        if xs[pts[j]] <= xq:
            seg = j
    a, b = pts[seg], pts[min(seg + 1, len(pts) - 1)]
    w = xs[b] - xs[a]
    t = 0.0 if w <= 1e-9 else _clamp((xq - xs[a]) / w, 0, 1)
    c = cs[a] if a < len(cs) else 0.5
    if abs(c - 0.5) > 1e-6 and t > 0:
        t = t ** lfo_bend_exponent(c)
    return _clamp(1.0 - (ys[a] + (ys[b] - ys[a]) * t), 0.0, 1.0)


def lfo_shape_pots(l: dict) -> list:
    """16 points (0..127) of one cycle of a Serum LFO shape: its points joined by segments whose bend is stored,
    or a sine when it has none."""
    xs, ys, cs = l.get("x") or [], l.get("y") or [], l.get("c") or []
    if len(xs) < 2 or len(xs) != len(ys):
        return [round(127 * (0.5 + 0.5 * math.sin(2 * math.pi * k / 16))) for k in range(16)]
    return [round(127 * curve_value(xs, ys, cs, k / 16)) for k in range(16)]


def map_lfos(ph: dict, P: dict, other: list, slot_of: dict, appr: list, note: list) -> list:
    """Serum LFO routes onto the engine's four LFOs and eight routes. Returns the routes that were NOT carried."""
    def dest(key):
        slot = slot_of.get(key[2])
        if key[0] == "VoiceFilter" and key[1] == "kParamFreq":
            return ("Filter 2 Freq" if key[2] == 1 else "Filter Freq"), CUTOFF_NOTES / FILTER_WIDTH * CAL["flt_env_scale"]
        if key[0] == "WTOsc" and key[1] == "kParamTablePos" and slot:
            return f"WT{slot} Pos", 1.0
        if key[0] == "Oscillator" and key[1] == "kParamVolume":
            return (f"WT{slot} Level" if slot else {3: "Noise Level", 4: "Sub Level"}.get(key[2])), 1.0
        # measured (probe_lfo_pitch.py, a constant LFO): a unipolar route moves coarse pitch 128 semitones per 100 %, fine pitch 2 (it stops
        # at +-1), the octave knob 120 (in whole octaves); the engine's tune unit is an octave (12 semitones)
        if key[0] == "Oscillator" and key[1] == "kParamFine" and slot in (1, 2):
            return f"WT{slot} Tune", 2.0 / 12.0
        if key[0] == "Oscillator" and key[1] == "kParamCoarsePit" and slot in (1, 2):
            return f"WT{slot} Tune", 128.0 / 12.0
        if key[0] == "Oscillator" and key[1] == "kParamOctave" and slot in (1, 2):
            return f"WT{slot} Tune", 120.0 / 12.0
        if key[0] == "Oscillator" and key[1] == "kParamPan" and slot in (1, 2):
            return f"WT{slot} Pan", 1.0
        return None, 1.0
    routes, left = [], []
    for item in other:
        src, key, a = item[:3]
        bip = item[3] if len(item) > 3 else False
        if not isinstance(src, int) or not 6 <= src <= 15:        # envelopes that did not get a slot are in here as "envN"
            left.append(item)
            continue
        if abs(a) < LFO_MIN_ROUTE:
            left.append(item)
            continue
        d, k = dest(key)
        if d in ME_DST:
            cls = lfo_class(d)
            # measured on Serum with a constant LFO (probe_lfo_level.py, probe_vcutoff_mod.py, probe_lfo_pitch.py): a unipolar route moves its
            # destination by 1.0 x amount at LFO value 1 (in the knob domain: the square root of the amplitude for level, the cutoff knob,
            # whole-range for the others), a bipolar one by 0.5 x amount at +1 and -0.5 at -1
            k *= LFO_SCALE.get(cls, 1.0)
            if bip:
                k *= 0.5
            v = a * k
            if cls == "tune":                                     # the engine squares a tune route's amount (up to 4 octaves)
                if abs(v) * 12 < 0.25:
                    left.append(item)                             # under a quarter of a semitone: inaudible
                    continue
                amt = math.copysign(math.sqrt(min(abs(v) / 4.0, 1.0)), v)
            else:
                amt = _clamp(v, -1, 1)
            fine_b = float(ph["oscs"][key[2]]["fine_cents"]) if (key[0] == "Oscillator" and key[1] == "kParamFine" and not bip) else None
            routes.append((abs(amt), src - 6, d, amt, bip, fine_b, a))
        else:
            left.append(item)
    routes.sort(key=lambda r: -r[0])
    weight = Counter()
    for w, l, d, a, _bip, *_ in routes:
        weight[l] += w
    chosen = [l for l, _ in weight.most_common(4)]
    taken = [r for r in routes if r[1] in chosen][:8]
    left += [(r[1] + 6, ("dropped", r[2], 0), r[3]) for r in routes if r not in taken]
    # Serum's Fine knob stops at +-100 cents, and the stop is applied to the knob's value with the modulation added (probe_lfo_pitch.py: "it stops at +-1"; Lynnic's LFO3 at -100 % into
    # both oscillators' Fine holds the pitch a semitone down from the time the LFO passes half its height, where a linear route would carry on to two). The engine's route has no stop,
    # so an LFO whose routes are all unipolar Fine routes of one base and depth gets the stop built into its table and a depth that ends at the clipped swing.
    clipped = {}
    for l in chosen:
        mine = [r for r in taken if r[1] == l]
        if ph["lfos"][l].get("type") in ("RandomSH",) or not mine or any(r[5] is None for r in mine) or max(r[6] for r in mine) - min(r[6] for r in mine) > 0.05:
            continue                                      # a route to something else, or routes of different depth: left linear
        fa = sum(r[6] for r in mine) / len(mine)
        fb = _clamp(sum(r[5] for r in mine) / len(mine), -100.0, 100.0)       # the oscillators' base Fine values differ by a few cents at most
        e = [_clamp(fb + 200.0 * fa * s / 127.0, -100.0, 100.0) - fb for s in lfo_shape_pots(ph["lfos"][l])]     # the pitch offset in cents at each table point
        peak = max(abs(x) for x in e)
        if peak < 25.0:                                   # under a quarter of a semitone: nothing to reshape
            continue
        v = math.copysign(peak / 1200.0, fa)              # octaves at the table's top
        clipped[l] = ([round(127 * abs(x) / peak) for x in e], math.copysign(math.sqrt(min(abs(v) / 4.0, 1.0)), v))
    for n, l in enumerate(chosen, 1):
        d = ph["lfos"][l]
        hz = d["rate"] if (d.get("rate") is not None and not d.get("sync")) else LFO_DEFAULT_HZ
        P[f"lfo{n}_rate"] = round(_clamp(127 * math.log(max(hz, 0.05) / 0.05) / math.log(2400), 0, 127))
        P[f"lfo{n}_mode"] = 0 if d.get("mode") in (None, 0, 0.0) else 1
        # a Random S&H LFO has no period, so a sine table puts a tone on its destination that Serum does not have (Lead - Other Side:
        # its 26 Hz Random S&H into an oscillator's level was a steady tremolo against Serum's flute-like note). Built flat at its mid level instead: a
        # bipolar route then adds nothing and a unipolar one a steady half-depth offset, the random LFO's average.
        flat = d.get("type") in ("RandomSH",)
        if flat:
            note.append(f"LFO {l + 1} ({d.get('type')}) built as its steady mid level: a sine's tone is not in Serum's note")
        for k, v in enumerate([64] * 16 if flat else clipped[l][0] if l in clipped else lfo_shape_pots(d)):
            P[f"lfo{n}_s{k}"] = v
    for r, (_, l, d, a, bip, *_) in enumerate(taken, 1):
        if l in clipped:
            a = clipped[l][1]
        P[f"lr{r}_src"], P[f"lr{r}_dst"], P[f"lr{r}_amt"] = chosen.index(l) + 1, ME_DST[d], round(_clamp(64 + 63 * a, 0, 127))
        P[f"lr{r}_bip"] = 1 if bip else 0
    if taken:
        note.append(f"{len(taken)} LFO route(s) carried from {len(chosen)} LFO(s), each with Serum's own polarity")
    return left


def map_to_tablor(vital: dict, pack, serum_tables, wt_out, subdir) -> dict:
    ph = vital["serum2move"]
    P = dict(TABLOR_DEFAULTS)
    crit: list[str] = []      # audibly changes the sound -> tier C
    appr: list[str] = []      # close but not exact        -> tier B
    note: list[str] = []
    paths = {"wt1_table": "Init", "wt2_table": "Init", "wt3_table": "Init"}
    missing: list[str] = []

    # ---- static macro offsets are folded into the base values --------------
    fold = Counter()
    keyfold = Counter()                          # a note-number route to a filter's cutoff, as semitones of cutoff per semitone of pitch
    routes = []                                  # envelope routes
    other = []                                   # LFO/velocity/etc: not representable
    mod_wheel_idle = 0
    bip_env = 0
    for m in ph["mods"]:
        src, a = m["source"], m["amount_pct"] / 100.0
        key = (m["dest_type"], m["dest_param"], m["dest_id"])
        if m["dest_type"].startswith("FX") or m["dest_type"] in ("Env", "Global", "LFOPointModBus"):
            continue
        if 25 <= src <= 32:
            mv = fxmap.macro_value(m, ph["macros"])
            fold[key] += a * ((mv - 0.5) if m.get("bipolar") else mv)       # a bipolar route is the source minus one half (calibration/probe_macro_bipolar.py, probe_note_route.py), not 2x - 1
        elif src in (16, 17):
            # Velocity and note number are x / 127 (calibration/probe_aux_source.py, the same sources as an aux). They move with every note; the preset is static, so
            # a velocity is taken at the nominal velocity (the engine's own default) and a note at middle C, which is where the engine's key tracking pivots. A note
            # route to the filter cutoff is carried exactly as key tracking as well (voice_filter_pots: the part per semitone), so only the value at middle C is here.
            x = (VELOCITY_NOMINAL if src == 16 else 60) / 127.0
            fold[key] += a * ((x - 0.5) if m.get("bipolar") else x)
            if src == 17 and key[:2] == ("VoiceFilter", "kParamFreq"):
                keyfold[key[2]] += a / 127.0 * CUTOFF_NOTES          # notes of cutoff per semitone of pitch (the same slope unipolar or bipolar)
        elif src == 1:
            # The mod wheel rests at 0 (the Move has none): a unipolar route adds nothing at rest, a bipolar one sits half its depth below the base.
            if m.get("bipolar"):
                fold[key] += a * -0.5
            mod_wheel_idle += 1
        elif 2 <= src <= 5:                      # measured: modulation source ids 2..5 are Env1..Env4 (probe in the notes of calibration/probe_venv.py)
            routes.append((src - 2, key, a))
            if m.get("bipolar") and key == ("VoiceFilter", "kParamFreq", 0):
                # A bipolar route is the source minus one half, so once the envelope has fallen the cutoff sits half the depth below the stored one and the
                # engine's unipolar envelope only gets the depth: fold the offset into the base (Lead - Let It Go: Env2 -> cutoff +17 % bipolar, sustain 0)
                fold[key] += a * -0.5
                bip_env += 1
        else:
            other.append((src, key, a, bool(m.get("bipolar"))))
    if fold:
        note.append(f"{len(fold)} macro-driven target(s) folded in at the saved macro position")
    if bip_env:
        note.append(f"{bip_env} bipolar envelope route(s) to the cutoff: the base sits half their depth lower")
    if mod_wheel_idle:
        note.append(f"{mod_wheel_idle} mod-wheel route(s) rest at the wheel's zero (no wheel on the Move)")

    # ---- oscillators --------------------------------------------------------
    # Many of these presets store an oscillator's level as 0 and supply all of it
    # from a modulator. A route Tablor cannot carry (LFO, velocity) must not mute
    # the oscillator, so its level is held at a representative fixed value.
    held = Counter()
    for item in other:
        src, key, a = item[:3]
        if isinstance(src, int) and (6 <= src <= 15 or src in (16, 17)):       # an LFO route is carried (map_lfos), velocity and note are folded in at their nominal value
            continue
        if key[0] == "Oscillator" and key[1] == "kParamVolume" and a > 0:
            held[key[2]] += a * MOD_LEVEL_HOLD

    def level_of(osc_id, base):
        level = base + fold[("Oscillator", "kParamVolume", osc_id)]
        if level < SILENT_LEVEL and held[osc_id]:
            level += held[osc_id]
            appr.append(f"osc {'ABCNS'[osc_id]} level comes from an LFO/velocity route, held fixed")
        return _clamp(level, 0, 1)

    def eff_level(o):
        return levels[o["id"]]

    levels = {o["id"]: level_of(o["id"], o["level"]) for o in ph["oscs"] if o["enabled"]}

    wt_oscs = [o for o in ph["oscs"] if o["enabled"] and o["kind"] == "kOsc_WT"]
    odd = [o for o in ph["oscs"] if o["enabled"] and o["kind"] != "kOsc_WT"]
    # The engine's one sample oscillator carries the loudest Sample / Multisample oscillator whose files are on this machine.
    smp = None
    for o in sorted((o for o in odd if o.get("sample") and eff_level(o) > 0.02), key=lambda o: -eff_level(o)):
        got = SE.export_info(o["sample"], wt_out.parent / "Samples" / subdir)
        if got:
            smp = (o, got[0], got[1])
            break
    if not smp and ph["noise"].get("click") and ph["noise"].get("sample"):
        # a one-shot click or hit in the noise slot (a kick attack): the sample oscillator plays it once, at its own pitch
        nz0 = ph["noise"]
        got = SE.export_info(nz0["sample"], wt_out.parent / "Samples" / subdir)
        if got:
            smp = ({"id": 3, "noise": True, "level": nz0["level"]}, got[0], got[1])
    for o in odd:
        if smp and o is smp[0]:
            continue
        if eff_level(o) <= 0.02:
            continue                                   # an oscillator that is on but silent adds nothing
        crit.append(f"osc {'ABC'[o['id']]} is a {o['kind'][5:]} oscillator (Tablor has one sample oscillator and no other kind)")
    def bent(o):
        return any(w["mode"] in BEND_WARPS and w["amount"] >= WARP_NOISE_FLOOR for w in o["warp"])

    wt_oscs.sort(key=lambda o: -eff_level(o))
    keep = wt_oscs[:3]                     # Serum has three wavetable oscs, so did the fork
    if len(keep) == 3 and bent(keep[2]):   # WT3 has no Bend: give it an osc that needs none
        for i in (1, 0):
            if not bent(keep[i]):
                keep[i], keep[2] = keep[2], keep[i]
                break
    slot_of = {o["id"]: n for n, o in enumerate(keep, 1)}

    # Voices sets each voice's share of the stream budget, so a preset that wants
    # wide stacks asks for fewer voices, down to enough for a chord. Mono has it all.
    wanted = [o["unison"] for o in keep]
    if ph["mono"]:
        P["voice_mode"], P["voices"] = 1, 1
        P["legato"] = int(ph["legato"])
    elif wanted:
        P["voices"] = _clamp(STREAM_BUDGET // max(sum(wanted), 1), MIN_POLY_VOICES, 8)
    fitted = fit_unison(wanted, STREAM_BUDGET // P["voices"])
    for o, got in zip(keep, fitted):
        if got < o["unison"]:
            (crit if got * 2 < o["unison"] else appr).append(
                f"osc {'ABC'[o['id']]} unison {o['unison']} fits as {got} "
                f"({P['voices']} voices share {STREAM_BUDGET} streams)")

    for n, o in enumerate(keep, 1):
        k = f"wt{n}"
        pos = _clamp((o["table_pos"] - 1) / 255 + fold[("WTOsc", "kParamTablePos", o["id"])], 0, 1)
        P[f"{k}_pos"] = round(pos * 127)
        P[f"{k}_level"] = pot_level(eff_level(o), o["unison"], wt=True)
        tune = o["octave"] * 12 + o["semitone"]
        P[f"{k}_tune"] = round(_clamp(tune, -48, 48))
        if abs(tune) > 48:
            appr.append(f"{k} tune {tune:+.0f} st clamped to +/-48")
        P[f"{k}_uni"] = _clamp(o["unison"], 1, 16)
        detune = _clamp(o["detune"] + fold[("Oscillator", "kParamDetune", o["id"])], 0.0, 1.0) ** CAL["detune_exp"] * CAL["detune_scale"]     # macro-driven detune at the saved position
        if CAL["detune_wid"] and o["detune_wid"] is not None:
            detune *= interp(CAL["detune_wid"], o["detune_wid"])
        P[f"{k}_detune"] = round(_clamp(detune, 0, 1) * 127)
        stereo = o["stereo_pct"] if o["stereo_pct"] is not None else CAL["stereo_default"]
        P[f"{k}_spread"] = (round(_clamp(stereo / 100 * CAL["width_scale"], 0, 1) * 127)
                            if P[f"{k}_uni"] > 1 else 0)
        P[f"{k}_pan"] = round(_clamp(64 + 63 * o["pan"] / 100, 0, 127))
        for w in o["warp"]:
            if w["amount"] < WARP_NOISE_FLOOR:
                continue
            if w["mode"] == "kEvenOdd" and w["amount"] >= 0.9:
                # Even/Odd at its end keeps only the even harmonics (calibration/osc_survey.py: Lead - HotLap's oscillator starts at the second harmonic in Serum): the
                # same wave an octave up. The harmonic amplitudes are those of the table's even harmonics, which the engine cannot take out; the pitch is right.
                P[f"{k}_tune"] = round(_clamp(P[f"{k}_tune"] + 12, -48, 48))
                appr.append(f"{k} warp EvenOdd approximated by an octave up (the engine cannot remove the odd harmonics)")
            elif w["mode"] == "kSync":
                P[f"{k}_sync"] = round(interp(SYNC_POT, _clamp(w["amount"], 0, 1)))
            elif w["mode"] in BEND_WARPS and n == 3:
                appr.append(f"{k} warp {w['mode'][1:]} dropped (WT3 has no Bend)")
            elif w["mode"] in BEND_WARPS:
                table = PWM_BEND_POT if w["mode"] == "kPWM" else BEND_POT
                P[f"{k}_bend"] = round(_clamp(64 + BEND_WARPS[w["mode"]] * (interp(table, _clamp(w["amount"], 0, 1)) - 64), 0, 127))
                if w["mode"] not in ("kPD_OSC", "kPWM") and not w["mode"].startswith("kBend"):
                    appr.append(f"{k} warp {w['mode'][1:]} approximated with Tablor Bend")
            else:
                crit.append(f"{k} warp {w['mode'][1:]} unsupported (no FM/RM/fold in Tablor)")
        path, ref = resolve_table(o["table"], pack, serum_tables, wt_out, subdir)
        if path is None and not ref and o.get("embedded"):
            path = write_embedded_table(o["embedded"], wt_out, subdir)
            note.append(f"{k}: the preset's own embedded wavetable ({len(o['embedded']) // EMBEDDED_FRAME} frames)")
        paths[f"{k}_table"] = path or "Init"
        if path is None and ref:
            missing.append(ref)
    if not keep:
        P["wt1_level"] = 0

    # ---- sub + noise --------------------------------------------------------
    sub = ph["sub"]
    if sub["enabled"]:
        P["sub_level"] = round(_clamp(pot_level(level_of(4, sub["level"])) * CAL["sub_scale"], 0, 127))
        P["sub_wave"] = SUB_WAVE.get(sub["shape"], 0)
        P["sub_tune"] = round(_clamp(sub["octave"] * 12, -48, 48))
    nz = ph["noise"]
    if nz.get("click"):
        note.append("noise osc plays a short one-shot click in Serum; not carried")
    noise_db = 0.0                    # the slot's level against the engine's White at the same volume, for a route into its volume as well
    if nz["enabled"]:
        P["noise_level"] = pot_level(level_of(3, nz["level"]))
        P["noise_type"], kind = noise_type_for(nz.get("sample_name"))
        if kind:
            standin = SE.noise_standin(nz["sample_name"])
            if standin:
                # not noise: a noise with the sample's own octave spectrum and level stands in (sample_export.noise_standin)
                P["noise_type"], P["noise_hp"], P["noise_lp"] = standin[0], standin[1], standin[2]
                noise_db = standin[3]
                P["noise_level"] = pot_level(level_of(3, nz["level"]) * 10 ** (noise_db / 20))
                kind = f"noise osc plays '{nz['sample_name'].rsplit('/', 1)[-1]}' in Serum, which is not noise; a noise with that sample's octave spectrum stands in"
            note.append(kind)
        elif nz.get("sample_name"):
            # a White / Pink / HP12 sample is that noise and Serum's Color still acts on it (calibration/probe_noise_hp12.py)
            P["noise_hp"], P["noise_lp"] = noise_color_pots(nz.get("color"))
            if P["noise_type"] == 3 and nz.get("color") is not None:
                # HP12 Pink with a Color above neutral: pink through one 12 dB/octave high-pass whose corner is 1.1 kHz at no Color, 1.9 kHz at 0.6, 5.3 kHz at 0.79, 12 kHz at 1.0
                c = nz["color"]
                if c >= 0.55:
                    P["noise_type"], P["noise_lp"] = 1, 127
                    P["noise_hp"] = round(_interp(c, [0.55, 0.6, 0.79, 1.0], [75, 84, 103, 119]))
                else:
                    P["noise_hp"] = 0               # the type's own 1.2 kHz high-pass; a low Color is a low-pass on top
        if not nz.get("sample_name"):
            # the noise oscillator itself: its Type is the spectrum and its Color two 12 dB/octave filters (NOISE_COLOR above)
            typ = nz.get("type")
            if typ in NOISE_TYPE_ENGINE or typ is None:
                P["noise_type"] = NOISE_TYPE_ENGINE.get(typ, 0)
                P["noise_hp"], P["noise_lp"] = noise_color_pots(nz.get("color"))
                if typ is None:                        # the unwritten type is 2.2 dB under White at the same volume (probe_noise_color.py)
                    noise_db = -2.2
                    P["noise_level"] = pot_level(level_of(3, nz["level"]) * 10 ** (-2.2 / 20))
            else:
                note.append(f"noise type {typ} (sparse clicks in Serum) approximated by pink noise through a high-pass")

    # ---- filter -------------------------------------------------------------
    # Everything goes through Tablor's one filter. In Serum a source can bypass it, so a
    # patch whose body bypasses must not be strangled by a filter it never went through.
    routed = {int(k): v for k, v in ph["routed"].items()}
    sources = [(o["id"], levels.get(o["id"], 0.0)) for o in keep]
    if smp and not smp[0].get("noise"):
        sources.append((smp[0]["id"], levels.get(smp[0]["id"], 0.0)))
    if sub["enabled"]:
        sources.append((4, sub["level"]))
    if nz["enabled"]:
        sources.append((3, nz["level"]))
    none = {int(k): v for k, v in ph.get("route_none", {}).items()}
    out = {int(k): v for k, v in ph.get("route_out", {}).items()} if CARRY_BUSES else {}
    bus_of = {int(k): v for k, v in ph.get("bus", {}).items()}                  # (the preset has been through JSON: keys are strings)
    fdest_of = {int(k): v for k, v in ph.get("filter_dest", {}).items()}
    post_of = lambda src, on=True: 0 if not on else 2 if none.get(src) else 3 if out.get(src) else int(not routed.get(src))
    through = sum(v ** 2 for i, v in sources if routed.get(i))
    around = sum(v ** 2 for i, v in sources if not routed.get(i) and not none.get(i))

    f = ph["filter"]
    # Each source goes where Serum sends it: through the filter or direct to the amp (the engine's per-source Route).
    bypassed = f["enabled"] and through == 0 and around > 0
    for fl, label in ((f, "filter"), (ph["filter2"], "filter 2")):
        if fl["enabled"] and 1.0 <= fl.get("wet", 100.0) < 99.0:
            appr.append(f"{label} Wet {fl['wet']:.0f} %: Serum mixes the unfiltered signal back in, the engine's voice filter is fully wet")
    if bypassed:
        appr.append("every source bypasses the filter in Serum, so Tablor's is left open")
    for oid, slot in slot_of.items():
        P[f"wt{slot}_post"] = post_of(oid)
    P["sub_post"] = post_of(4, sub["enabled"])
    P["noise_post"] = post_of(3, nz["enabled"])
    if smp:
        so, spath, sinfo = smp
        P["smp_set"] = f"{MOVE_SAMPLE_ROOT}/{subdir}/{spath.name}"
        slevel = level_of(3, so["level"]) if so.get("noise") else eff_level(so)
        P["smp_level"] = round(_clamp(pot_level(slevel) * CAL["smp_scale"], 0, 127))
        P["smp_fixed"] = int(bool(so.get("noise")))
        P["smp_tune"] = round(_clamp(sinfo["octave"] * 12 + sinfo["pitch"], -48, 48))
        P["smp_fine"] = round(_clamp(sinfo["fine"], -100, 100))
        P["smp_post"] = post_of(so["id"])
        note.append(f"sample oscillator carried: {sinfo['file']} ({'multisample' if sinfo['multi'] else 'sample'})")
    # ---- FX buses: each source's send to Bus 1 / Bus 2 (Serum's level cubed is the gain, probe_bus.py) ----
    send_keys = {**{oid: f"wt{slot}" for oid, slot in slot_of.items()}, 4: "sub", 3: "noise"}
    if smp:
        send_keys[smp[0]["id"]] = "smp"
    for k in send_keys.values():
        P[f"{k}_bus1"] = P[f"{k}_bus2"] = 0
    if CARRY_BUSES:
        for src, key in send_keys.items():
            if (src == 4 and not sub["enabled"]) or (src == 3 and not nz["enabled"]):
                continue
            for b in (0, 1):
                lvl = bus_of.get(src, [0.0, 0.0])[b]
                P[f"{key}_bus{b + 1}"] = int(round(127.0 * (_clamp(lvl, 0.0, 100.0) / 100.0) ** 3))
    serial = False                                  # Filter 1's output goes into Filter 2
    if CARRY_BUSES:
        for slot, fk, fl_on in ((5, "flt1", ph["filter2"]["enabled"]), (6, "flt2", ph["filter"]["enabled"])):       # (in series: into the other filter, which has to be on)
            d = fdest_of.get(slot, 0)
            if d == 3 and fl_on and slot == 5:
                P["flt1_out"] = 3
                serial = True
            elif d == 3 and fl_on:
                crit.append("Voice Filter 2 routed into Filter 1 in series (not carried)")
            elif d in (1, 2):
                P[f"{fk}_out"] = d
            for b in (0, 1):
                P[f"{fk}_bus{b + 1}"] = int(round(127.0 * (_clamp(bus_of.get(slot, [0.0, 0.0])[b], 0.0, 100.0) / 100.0) ** 3))
    def voice_filter_pots(fl, key_id, prefix, label):
        """The engine pots for one Serum voice filter (cutoff law, model, resonance, drive, key tracking)."""
        idx, exact = FILTER_MAP.get(fl["model"], (None, False))
        fkey = filter_model_key(fl["model"], fl.get("var"))
        if fkey in FILTER_MODELS:                                # measured against Serum: the engine filter type that matches, however it is named
            fm = FILTER_MODELS[fkey]
            idx, exact = int(fm["type"]), False
            if fm.get("poor"):
                appr.append(f"{label} {fl['model']}: the nearest engine filter is still {fm['rms_db']:.1f} dB off")
        if idx is None and prefix == "flt2":
            crit.append(f"{label} model {fl['model']} unsupported; left open")       # half a source through a low-pass that is not that filter is worse than none
            return
        if idx is None:
            crit.append(f"{label} model {fl['model']} unsupported; LP24 used")
            idx = 1
        elif not exact and fkey not in FILTER_MODELS:
            appr.append(f"{label} {fl['model']} approximated by {['LP12','LP24','HP12','HP24','BP12','BP24'][idx]}")
        P[f"{prefix}_type"] = idx
        cut = _clamp(fl["cutoff_norm"] + fold[("VoiceFilter", "kParamFreq", key_id)], 0, 1)
        # A stored key track of 1.0 is full tracking. Serum's filter pivots near the bottom of
        # the keyboard and Tablor's at note 60, so the base cutoff has to move to match.
        key = _clamp(abs(fl["keytrack"]), 0, 1) * CAL["key_scale"]
        pivot = (60 - CAL["key_pivot"]) * key
        P[f"{prefix}_freq"] = round(_clamp(cut * CUTOFF_NOTES * CAL["cutoff_scale"] + CAL["cutoff_offset"] + pivot - 8 + filter_cutoff_delta(fkey, cut), 0, 127))
        P[f"{prefix}_res"] = filter_res_pot(fl["model"], _clamp(fl["reso_pct"] + 100.0 * fold[("VoiceFilter", "kParamReso", key_id)], 0, 100))
        P[f"{prefix}_key"] = round(_clamp(key + keyfold[key_id], 0, 1) * 127)
        if fl["keytrack"] < 0:
            note.append(f"negative {label} key tracking not representable")
        drive_pct = _clamp(fl["drive_pct"] + 100.0 * fold[("VoiceFilter", "kParamDrive", key_id)], 0, 100)      # macro, velocity and note routes at their nominal value
        P[f"{prefix}_drive"] = round(_clamp(interp(FILTER_DRIVE_POT_USED, drive_pct), 0, 127))
        P[f"{prefix}_gain"] = round(_clamp(interp(FILTER_DRIVE_MAKEUP_DB, drive_pct) / 24.0 * 127.0, 0, 127))

    if f["enabled"] and not bypassed:
        voice_filter_pots(f, 0, "flt", "filter")
    # The second filter: each source has a balance between the two (the engine's per-source Filter Balance); Filter 2 has its own type, cutoff, resonance,
    # drive and key tracking, and follows the filter envelope by its own amount.
    f2 = ph["filter2"]
    reaches2 = {}
    if f2["enabled"] and not bypassed:
        bal = {int(k): v for k, v in ph["balance"].items()}       # (the preset has been through JSON: keys are strings; an int lookup found none and sent every source to Filter 1)
        pot_bal = lambda src: round(_clamp((bal.get(src, -1.0) + 1.0) * 63.5, 0, 127)) if routed.get(src) else 0
        for oid, slot in slot_of.items():
            P[f"wt{slot}_bal"] = pot_bal(oid)
        if sub["enabled"]:
            P["sub_bal"] = pot_bal(4)
        if nz["enabled"]:
            P["noise_bal"] = pot_bal(3)
        if smp:
            P["smp_bal"] = pot_bal(smp[0]["id"])
        reaches2 = {k: P[k] for k in ("wt1_bal", "wt2_bal", "wt3_bal", "sub_bal", "noise_bal", "smp_bal") if P[k] > 0}
        if reaches2 or serial:
            voice_filter_pots(f2, 1, "flt2", "filter 2")
            note.append("second voice filter carried (" + ", ".join(f"{k[:-4]} {v / 63.5 - 1:+.1f}" for k, v in reaches2.items()) + (", " if reaches2 else "") + ("in series after filter 1" if serial else "") + ")")
        elif ph["filter2_enabled"]:
            note.append("second voice filter is on but no source is sent to it")

    # ---- envelopes ----------------------------------------------------------
    P["vca_a"], P["vca_d"], P["vca_s"], P["vca_r"] = pot_env(ph["envs"][0])
    flt_routes = [r for r in routes if r[1] == ("VoiceFilter", "kParamFreq", 0)]
    if flt_routes and not bypassed:
        tot = Counter()
        for e, _, a in flt_routes:
            tot[e] += a
        feg = max(tot, key=lambda e: abs(tot[e]))
        P["flt_a"], P["flt_d"], P["flt_s"], P["flt_r"] = pot_env(ph["envs"][feg], CAL.get("flt_decay_mult", 1.0))
        P["flt_env"] = round(_clamp(64 + 63 * tot[feg] * CUTOFF_NOTES / FILTER_WIDTH * CAL["flt_env_scale"], 0, 127))
        if len(tot) > 1:
            appr.append("only the strongest of several envelope->cutoff routes kept")
    else:
        P["flt_env"] = 64                     # no filter envelope depth
    f2_routes = [r for r in routes if r[1] == ("VoiceFilter", "kParamFreq", 1)]
    f2_me = None
    if (reaches2 or serial) and f2_routes:
        tot2 = Counter()
        for e, _, a in f2_routes:
            tot2[e] += a
        e2 = max(tot2, key=lambda e: abs(tot2[e]))
        P["flt2_env"] = round(_clamp(64 + 63 * tot2[e2] * CUTOFF_NOTES / FILTER_WIDTH * CAL["flt_env_scale"], 0, 127))
        if not flt_routes:
            P["flt_a"], P["flt_d"], P["flt_s"], P["flt_r"] = pot_env(ph["envs"][e2], CAL.get("flt_decay_mult", 1.0))     # the one filter envelope is Filter 2's
            feg = e2
        elif e2 != feg:
            # Filter 2 has its own envelope: a spare modulation envelope pointed at "Filter 2 Freq" (same units as flt2_env, its own ADSR); if it loses its place below,
            # Filter 2 follows the filter envelope as before
            f2_me = (e2, tot2[e2] * CUTOFF_NOTES / FILTER_WIDTH * CAL["flt_env_scale"])
    synced = {o["id"] for o in keep if any(w["mode"] == "kSync" for w in o["warp"])}
    mod_routes = []
    for e, key, a in routes:
        # Serum addresses oscillators by id, and they may sit in any Tablor slot.
        slot, dest = slot_of.get(key[2]), None
        if key[0] == "WTOsc" and key[1] == "kParamTablePos" and slot:
            dest = f"WT{slot} Pos"
        elif key[0] == "WTOsc" and key[1] == "kParamWarp" and slot and key[2] in synced:
            dest = f"WT{slot} Sync"
        elif key[0] == "Oscillator" and key[1] == "kParamVolume":
            dest = f"WT{slot} Level" if slot else {3: "Noise Level", 4: "Sub Level"}.get(key[2])
        if dest:
            mod_routes.append((abs(a), e, dest, a))
        elif key not in (("VoiceFilter", "kParamFreq", 0), ("VoiceFilter", "kParamFreq", 1)):
            other.append((f"env{e+1}", key, a))

    # An envelope that is an oscillator's only level comes first: losing a filter
    # sweep changes a sound, losing this silences part of it.
    def level_pot(dest):
        pot = {"Sub Level": "sub_level", "Noise Level": "noise_level"}.get(dest)
        if not pot and dest.endswith(" Level"):
            pot = f"wt{dest[2]}_level"
        return pot

    def needed(route):
        pot = level_pot(route[2])
        return route[3] > 0 and pot is not None and P[pot] == 0

    if f2_me:
        mod_routes.append((abs(f2_me[1]), f2_me[0], "Filter 2 Freq", f2_me[1]))
    mod_routes.sort(key=lambda r: (needed(r), r[0]), reverse=True)
    if f2_me and any(r[2] == "Filter 2 Freq" for r in mod_routes[:2]):
        P["flt2_env"] = 64                                    # its own envelope carries the sweep
    elif f2_me:
        P["flt2_env"] = round(_clamp(64 + 63 * f2_me[1], 0, 127))
        appr.append("Filter 2 follows the envelope Filter 1 uses, not its own (both spare envelopes are taken)")
    for n, (_, e, dest, a) in enumerate(mod_routes[:2], 1):
        P[f"me{n}_a"], P[f"me{n}_d"], P[f"me{n}_s"], P[f"me{n}_r"] = pot_env(ph["envs"][e])
        if dest == "Noise Level":
            a *= 10 ** (noise_db / 40)           # the route moves the knob and the gain is its square (calibration/probe_noise_route.py): the level offset is half in dB
        P[f"me{n}_dst"], P[f"me{n}_amt"] = ME_DST[dest], round(_clamp(64 + 63 * a, 0, 127))
    if len(mod_routes) > 2:
        appr.append(f"{len(mod_routes) - 2} extra envelope route(s) dropped (Tablor has 2)")
        for _, _, dest, a in mod_routes[2:]:
            if a > 0 and level_pot(dest) and P[level_pot(dest)] == 0:
                P[level_pot(dest)] = round(127 * _clamp(a * MOD_LEVEL_HOLD, 0, 1))

    other = map_lfos(ph, P, other, slot_of, appr, note)          # the LFO routes the engine's LFOs can carry
    audible = [o for o in other if abs(o[2]) >= 0.25 and o[1][0] in ("VoiceFilter", "WTOsc", "Oscillator")]
    if audible:
        appr.append(f"{len(audible)} velocity/LFO route(s) >=25% not carried (an LFO beyond the engine's four, or a destination it lacks)")

    # ---- velocity -> amplitude ---------------------------------------------------
    # Serum's loudness does not depend on the velocity unless a route puts it there (a plain saw is flat from velocity 20 to 127), and a velocity -> Voice Amp route is linear in
    # the velocity (20 log10(vel / 127) at 100 %). The engine's "Serum" velocity law is that, anchored at velocity 100; VCA Velocity is the route's depth.
    for m in ph["mods"]:
        if m["dest_type"] == "Global" and m["dest_param"] == "kParamVoiceAmp" and m["source"] == 16 and m["amount_pct"] > 0:
            P["vca_vel"] = max(P["vca_vel"], round(_clamp(m["amount_pct"] / 100.0, 0.0, 1.0) * 127))
    # ---- master -------------------------------------------------------------
    if CAL["master_db0"] is not None:
        m = ph.get("master")
        want = (CAL["master_db0"] + CAL["master_slope"] * math.log10(max(m, 1e-4))
                if m is not None else CAL["master_default_db"])
        P["volume"] = pot_volume(want)
        short = want - 20 * math.log10((P["volume"] / 127) ** 2 * MASTER_MAX + 1e-9)
        if short > 3:
            appr.append(f"about {short:.0f} dB quieter than Serum (Tablor volume is at maximum)")

    # ---- voice --------------------------------------------------------------
    if ph["porta_s"] is not None and ph["porta_s"] > 0.002:
        P["glide_mode"] = 2
        P["glide"] = round(_clamp(math.log(ph["porta_s"] / 0.001) / math.log(2000) * 127, 0, 127))
    # ---- filter fit -----------------------------------------------------------
    note += apply_filter_fit(ph["name"], P)
    note += apply_width_fit(ph["name"], P)
    # ---- FX block -------------------------------------------------------------
    fx_p, fx_notes, fx_info = fxmap.map_fx(ph["name"], ph.get("fx_nodes", []), ctx={"mods": ph["mods"], "macros": ph["macros"]})
    t_out = fx_p.pop("comp_thresh_out_db", None)
    fx_p_extra = {k: fx_p.pop(k) for k in ("comp_thresh2_out_db", "comp_thresh3_out_db") if k in fx_p}
    P.update(fx_p)
    if t_out is not None:
        # the threshold was measured at Serum's output; the engine's compressor sits before the master gain, so take that gain (at the volume the
        # closed-form law gives, before any loudness fit) off it
        g_db = 20 * math.log10((P["volume"] / 127) ** 2 * MASTER_MAX + 1e-9)
        # The thresholds were measured at the probe preset's default master; Serum's master comes after its effects, so a preset with another master has
        # the compressor working at a level that many dB lower or higher at the output (the output is proportional to the master: calibration/stage_audit.py)
        m_db = master_vs_default_db(ph.get("master"))
        P["comp_thresh"] = int(round(_clamp((t_out + m_db - g_db + 90.0) / 120.0, 0.0, 1.0) * 127))
        for k, key in (("comp_thresh2_out_db", "comp_thresh2"), ("comp_thresh3_out_db", "comp_thresh3")):
            if k in fx_p_extra:
                P[key] = int(round(_clamp((fx_p_extra[k] + m_db - g_db + 90.0) / 120.0, 0.0, 1.0) * 127))
    note += fx_notes
    dyn = fxmap.dynamic_filter(ph.get("fx_nodes", []), {"mods": ph["mods"], "macros": ph["macros"]})
    if dyn:
        note += apply_filter2(P, dyn, ph, feg if (flt_routes and not bypassed) else None)
    note += apply_chain_fit(ph["name"], P)
    # The FX switches are part of the preset: On for the stages this sound uses, Off for the rest, and the
    # whole block On. A preset load only sets the keys its state holds, so writing all five resets them
    # whenever another preset is chosen. A stage that is Off here has no mix or no bands, so the sound is
    # the same either way (params_sig leaves them out for that reason).
    P["fx_on"] = 1
    P["eq_on"] = int(any(P[f"eq{n}_type"] for n in range(1, 5)) or any(P.get(f"eq{t}{n}_type", 0) for t in "ms" for n in range(1, 5)))
    P["wide_on"] = int(P["fx_wide"] > 0)
    P["dly_on"] = int(P["dly_mix"] > 0 or P.get("dly2_mix", 0) > 0)
    P["rev_on"] = int(P["rev_mix"] > 0)
    if CARRY_BUSES:
        note += map_bus_racks(ph, P, feg if (flt_routes and not bypassed) else None)
    # The performance knobs (Decay macro, Drive, the two shelves) are neutral in every preset, written out
    # so that loading a preset puts them back.
    P.update({"decay": 64, "drive": 0, "lo_shelf": 64, "hi_shelf": 64})
    note += apply_tune_fit(ph["name"], P)
    note += apply_stereo_fit(ph["name"], P)
    if ph.get("bus_fx") and not CARRY_BUSES:
        note.append(f"{ph['bus_fx']} FX in the two bus racks not carried")
    tier = "C" if crit else "B" if appr else "A"
    return {"name": ph["name"], "tier": tier, "critical": crit, "approx": appr, "notes": note,
            "missing_tables": sorted(set(missing)), "fx": ph["fx"], "fx_info": fx_info, "params": P, "paths": paths}


BUS_KEYS = [k for k in fxmap.fx_defaults() if k != "fx_space"]


def map_bus_racks(ph: dict, P: dict, voice_env=None) -> list[str]:
    """The FX bus racks that some source sends to, as the engine's bus pots (state keys "b1." / "b2."): each rack mapped as the main one is (the importer measures its reverb,
    delay and width under the name '<preset>#busN'), its compressor thresholds taken to the engine's level the same way. A send into an empty rack is a plain copy of the source."""
    notes = []
    sends = [k for k in P if k.endswith(("_bus1", "_bus2")) and P[k] > 0]
    for b in (1, 2):
        nodes = ph.get("bus_nodes", [[], []])[b - 1]
        live = [k for k in sends if k.endswith(f"_bus{b}")]
        if live and any("FXReverb" in n and (n["FXReverb"].get("plainParams") or {}).get("kParamWet", 0.0) >= 99.9
                        and (n["FXReverb"].get("plainParams") or {}).get("kParamLevelOut", 1.0) <= 0.0005 for n in nodes):
            for k in live:                              # a reverb that is all wet with its Level Out at 0 makes no sound, and neither does the bus behind it (Bass - Gravity's)
                P[k] = 0
            notes.append(f"FX bus {b} is muted by an all-wet reverb with Level Out 0; its sends are dropped")
            continue
        if not live:
            if nodes:
                notes.append(f"FX bus {b} has {len(nodes)} FX but no source sends to it")
            continue
        if not nodes:
            notes.append(f"FX bus {b}: sent {', '.join(k.split('_')[0] for k in live)}, rack empty (a parallel copy)")
            continue
        fx_p, fx_notes, _ = fxmap.map_fx(f"{ph['name']}#bus{b}", nodes, ctx={"mods": ph["mods"], "macros": ph["macros"]})
        t_out = fx_p.pop("comp_thresh_out_db", None)
        extra = {k: fx_p.pop(k) for k in ("comp_thresh2_out_db", "comp_thresh3_out_db") if k in fx_p}
        if t_out is not None:
            g_db = 20 * math.log10((P["volume"] / 127) ** 2 * MASTER_MAX + 1e-9)
            m_db = master_vs_default_db(ph.get("master"))
            fx_p["comp_thresh"] = int(round(_clamp((t_out + m_db - g_db + 90.0) / 120.0, 0.0, 1.0) * 127))
            for k, key in (("comp_thresh2_out_db", "comp_thresh2"), ("comp_thresh3_out_db", "comp_thresh3")):
                if k in extra:
                    fx_p[key] = int(round(_clamp((extra[k] + m_db - g_db + 90.0) / 120.0, 0.0, 1.0) * 127))
        # A Filter FX that the filter envelope moves is the engine's Filter stage in a rack, as in the main one (fxmap.filter_bands leaves it to that stage and
        # no static EQ band stands in for it): left out here, the EQs behind it, set to lift what it takes away, boost the whole distorted signal (Lead - Other
        # Side's bus 1: LP 24 -> +18 dB shelf at 15 kHz, +29 dB too much above 10 kHz).
        dyn = fxmap.dynamic_filter(nodes, {"mods": ph["mods"], "macros": ph["macros"]})
        if dyn:
            scratch: dict = {}
            fnotes = apply_filter2(scratch, dyn, ph, voice_env if voice_env is not None else (-1 if P.get("fxf_on") else None))
            fx_p.update({k: v for k, v in scratch.items() if k.startswith("fxf_")})
            if voice_env is None and not P.get("fxf_on"):
                P.update({k: v for k, v in scratch.items() if k in ("flt_a", "flt_d", "flt_s", "flt_r")})      # no voice filter uses the envelope: the stage takes the Filter FX's
            notes += [f"FX bus {b}: {n}" for n in fnotes]
        fx_p["eq_on"] = int(any(fx_p.get(f"eq{n}_type", 0) for n in range(1, 5)) or any(fx_p.get(f"eq{t}{n}_type", 0) for t in "ms" for n in range(1, 5)))
        fx_p["wide_on"] = int(fx_p.get("fx_wide", 0) > 0)
        fx_p["dly_on"] = int(fx_p.get("dly_mix", 0) > 0 or fx_p.get("dly2_mix", 0) > 0)
        fx_p["rev_on"] = int(fx_p.get("rev_mix", 0) > 0)
        for k in BUS_KEYS + ["eq_on", "wide_on", "dly_on", "rev_on", "mwidth"]:
            if k in fx_p:
                P[f"b{b}.{k}"] = fx_p[k]
        notes.append(f"FX bus {b} carried: {', '.join(k.split('_')[0] for k in live)} sent")
        notes += [f"FX bus {b}: {n}" for n in fx_notes if not n.startswith("Utils carried")]
        if fx_p.get("mwidth", 64) != 64:
            notes.append(f"FX bus {b}: Utils carried as the rack's own width {fx_p['mwidth'] / 64 * 100:.0f} %")
        if fx_p.get("monohz", 0):
            notes.append(f"FX bus {b}: Utils mono-below in a bus rack not carried (the engine's mono filter is for the whole mix)")
    return notes


def apply_filter2(P: dict, dyn: dict, ph: dict, feg1) -> list[str]:
    """The envelope-moved Filter FX as the engine's Filter FX stage (after the distortion; its cutoff follows the filter envelope), cutoff law and models as for the voice filter."""
    model = "MgL24" if dyn["model"] == "default" else dyn["model"]
    fkey = filter_model_key(model, None)
    idx, _ = FILTER_MAP.get(model, (None, False))
    if fkey in FILTER_MODELS:
        idx = int(FILTER_MODELS[fkey]["type"])
    if idx is None:
        return [f"Filter FX model {model} not carried"]
    cut = _clamp(dyn["norm"], 0, 1)
    P["fxf_on"] = 1
    P["fxf_type"] = idx
    P["fxf_freq"] = round(_clamp(cut * CUTOFF_NOTES * CAL["cutoff_scale"] + CAL["cutoff_offset"] - 8 + filter_cutoff_delta(fkey, cut), 0, 127))
    P["fxf_res"] = filter_res_pot(model, dyn["reso"])
    e = max(dyn["envs"], key=lambda k: abs(dyn["envs"][k]))
    P["fxf_env"] = round(_clamp(64 + 63 * dyn["envs"][e] * CUTOFF_NOTES / FILTER_WIDTH * CAL["flt_env_scale"], 0, 127))
    out = [f"Filter FX carried as the effects block's filter: {model}, envelope {e + 1} x{dyn['envs'][e]:.2f}"]
    if feg1 is None:
        P["flt_a"], P["flt_d"], P["flt_s"], P["flt_r"] = pot_env(ph["envs"][e], CAL.get("flt_decay_mult", 1.0))
    elif feg1 != e:
        out.append("the Filter FX follows the voice filter's envelope, not the one it uses in Serum")
    if len(dyn["envs"]) > 1:
        out.append("only the strongest of several envelopes on the Filter FX kept")
    if dyn["ignored"]:
        out.append("Filter FX moved by a source the engine does not have (velocity/aftertouch), left out")
    return out


def trim_for_headroom(m: dict, move_root: Path) -> None:
    """Matching Serum's level can push a chord past full scale. Tablor's output clipper
    rounds that off, which is the point, but its shoulder runs out about +4 dB over. Play
    the preset through the real engine and turn it down only if a full-velocity chord
    gets that far. Skipped, with a note in the report, if calibration/tablor_cli.exe has
    not been built."""
    cli = Path(__file__).resolve().parent / "calibration/tablor_cli.exe"
    if not cli.exists():
        m["notes"].append("not checked for clipping: calibration/tablor_cli.exe is not built")
        return
    chords = [[48], [60]] if m["params"]["voice_mode"] == 1 else [[48, 55, 60, 63], [60, 67, 72, 75]]
    start = m["params"]["volume"]
    with tempfile.TemporaryDirectory() as td:
        bf, rf = Path(td, "b.txt"), Path(td, "o.raw")
        for _ in range(16):
            bf.write_text(blob(m), encoding="utf-8", newline="\n")
            clipped = False
            for notes in chords:
                r = subprocess.run([str(cli), str(move_root), str(bf), str(rf), "1.5", "1.2", "127",
                                    ",".join(map(str, notes)), "1"], capture_output=True, timeout=120)
                if r.returncode:
                    m["notes"].append(f"clipping check failed: {r.stderr.decode(errors='replace')[:80]}")
                    return
                raw = rf.read_bytes()
                x = struct.unpack(f"<{len(raw) // 4}f", raw)
                clipped |= max(map(abs, x), default=0.0) >= 0.999
            if not clipped or m["params"]["volume"] < round(20 / POT_SCALE):
                break
            m["params"]["volume"] = max(0, m["params"]["volume"] - max(1, round(5 / POT_SCALE)))
    if m["params"]["volume"] != start:
        drop = 40 * math.log10(m["params"]["volume"] / start)
        m["notes"].append(f"volume trimmed {drop:.1f} dB so a chord at full velocity stays on the clipper's shoulder")


def blob(m: dict) -> str:
    parts = [f"{k}={v}" for k, v in m["params"].items()]
    parts += [f"{k}={v}" for k, v in m["paths"].items()]
    return "TBLR2;" + ";".join(parts) + ";"


def unsupported_reasons(m: dict) -> list:
    """What stops a converted preset being a whole translation: an audible gap (tier C), or an effect in its chain that TinyHouse has no
    stage for."""
    why = list(m["critical"])
    why += [x for x in m["notes"] if "not carried" in x]
    return why


# ---------------------------------------------------------------------------
def default_serum_tables():
    """Serum 2's own Tables folder, where the factory wavetables a preset names live (a pack holds only its own). Without it they fall back to Init."""
    try:
        import serum_paths
        return serum_paths.SERUM_TABLES if serum_paths.SERUM_TABLES.is_dir() else None
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("src", type=Path, help=".SerumPack zip or folder of .SerumPreset files")
    ap.add_argument("-o", "--out", type=Path, default=Path("out"))
    ap.add_argument("--serum-tables", type=Path, help="Serum 2 'Tables' folder, to resolve factory wavetables")
    ap.add_argument("--wt-subdir", default="Deep House", help="folder under Wavetables/ on the Move")
    ap.add_argument("--bank", default="", help="bank (a folder under the module's presets) to put the presets in; default: loose files, the 'User' bank")
    ap.add_argument("--supported-only", action="store_true",
                    help="keep only presets TinyHouse can carry whole: no critical gap and every effect in the chain carried")
    a = ap.parse_args()

    out: Path = a.out
    vital_dir = out / "vital"
    wt_out = out / "move/data/UserData/UserLibrary/Wavetables"
    pre_out = out / "move/data" / MOVE_PRESET_DIR / a.bank
    for d in (vital_dir, wt_out, pre_out):
        d.mkdir(parents=True, exist_ok=True)

    presets, tables = extract(a.src, out)
    if not presets:
        sys.exit("No .SerumPreset files found")
    for t in tables.values():                                  # embedded wavetables
        dest = wt_out / a.wt_subdir / t.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(t, dest)
    print(f"{len(presets)} presets, {len(tables)} packaged wavetables")

    # Stage 2
    used: set[str] = set()
    for p in presets:
        header, tree = decode_preset_file(p)
        v = to_vital(physical(tree, header))
        # The file name is unique and is what the calibration files (FX fingerprints, fits) are keyed
        # by. Headers can repeat: "Pad - Nu Aspect 2.SerumPreset" is headed "Pad - Nu Aspect", and
        # naming by header swapped that pair, so each took the other's fingerprint and fit.
        name = ascii_name(p.stem)
        n = 2
        while name.lower() in used:
            name, n = f"{ascii_name(p.stem)} {n}", n + 1
        used.add(name.lower())
        v["preset_name"] = name
        v["serum2move"]["name"] = name
        (vital_dir / f"{name}.vital").write_text(json.dumps(v, indent=1), encoding="utf-8")

    # Stage 3: reads the .vital files back from disk
    results, skipped = [], []
    for vf in sorted(vital_dir.glob("*.vital")):
        v = json.loads(vf.read_text(encoding="utf-8"))
        m = map_to_tablor(v, tables, a.serum_tables or default_serum_tables(), wt_out, a.wt_subdir)
        trim_for_headroom(m, out / "move")
        m["notes"] += apply_level_fit(m["name"], m["params"])
        why = unsupported_reasons(m)
        if a.supported_only and why:
            skipped.append((m["name"], why))
            continue
        name = ascii_name(m["name"])[:40]
        (pre_out / f"{name}.json").write_text(
            json.dumps({"name": name, "module": "tinyhouse", "version": 1, "state": blob(m)}) + "\n",
            encoding="utf-8")
        results.append(m)

    # Report
    if a.supported_only:
        (out / "skipped.md").write_text("\n".join(["# Presets not imported (not fully supported)", ""] + [f"- {n}: {'; '.join(w)}" for n, w in skipped]), encoding="utf-8")
        print(f"--supported-only: kept {len(results)}, skipped {len(skipped)} (see {out / 'skipped.md'})")
    tiers = Counter(r["tier"] for r in results)
    gaps = Counter(re.sub(r"[\d.+-]+", "#", x).split(" (")[0] for r in results for x in r["critical"] + r["approx"])
    miss = Counter(t for r in results for t in r["missing_tables"])
    fx = Counter(f for r in results for f in set(r["fx"]))
    carried = {"EQ": sum(1 for r in results if r["params"]["eq1_type"] or r["params"]["eq2_type"]),
               "Delay": sum(1 for r in results if r["params"]["dly_mix"]),
               "Reverb": sum(1 for r in results if r["params"]["rev_mix"]),
               "Width": sum(1 for r in results if r["params"]["fx_wide"])}
    not_carried = Counter(k.strip() for r in results for x in r["notes"] if x.startswith("not carried: ") for k in x[13:].split(","))
    eq_err = [r["fx_info"]["eq_rms_db"] for r in results if "eq_rms_db" in r["fx_info"]]
    (out / "report.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    lines = [f"# Conversion report ({len(results)} presets)", "",
             f"Engine fidelity: A (faithful) {tiers['A']}, B (close) {tiers['B']}, C (compromised) {tiers['C']}", "",
             f"Presets with an unresolved wavetable (fall back to Init): {sum(1 for r in results if r['missing_tables'])}",
             f"Unique wavetables referenced but not found: {len(miss)} (pass --serum-tables to resolve)", "",
             "## Most common gaps", *[f"- {n}x {g}" for g, n in gaps.most_common(15)], "",
             "## FX: what the preset has, and what TinyHouse now does with it",
             "Carried by the engine's own FX block (presets where it is on): " + ", ".join(f"{k} {n}" for k, n in carried.items()),
             "Not carried (presets with each enabled): " + ", ".join(f"{k} {n}" for k, n in not_carried.most_common()),
             f"EQ bands reduced to the engine's four: {len(eq_err)} presets, worst rms error {max(eq_err, default=0):.1f} dB, median {sorted(eq_err)[len(eq_err) // 2] if eq_err else 0:.1f} dB",
             "All FX in the packs, for reference: " + ", ".join(f"{f} {n}" for f, n in fx.most_common()), "",
             "## Per preset", "| tier | preset | critical | approx | notes |", "|---|---|---|---|---|"]
    for r in sorted(results, key=lambda r: (r["tier"], r["name"])):
        lines.append(f"| {r['tier']} | {r['name']} | {'; '.join(r['critical'])} | {'; '.join(r['approx'])} | {'; '.join(r['notes'])} |")
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:22]))
    print(f"\nCopy {out / 'move/data/UserData'} to /data/UserData on the Move. Report: {out / 'report.md'}")


if __name__ == "__main__":
    main()
