# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Coverage report: every parameter Vital has, which the converter carries, and how many of your local presets use each.

    python calibration/vital_coverage.py [preset folder] [-o docs/VITAL_COVERAGE.md]

The parameter list is the plugin's own: Vital's default state (what the plugin saves with nothing loaded) holds every parameter name with its
default value.  A parameter counts as "used" by a preset when its value differs from that default.  "Carried" is
decided by looking for the parameter's name in the converter's own source (string literals and f-string patterns, with indices folded to N), then
adjusted by the hand-written notes below for what is only approximated or only partly carried.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))


def norm(name: str) -> str:
    return re.sub(r"(?<![A-Za-z])\d+(?![A-Za-z])", "N", name)


def converter_patterns() -> set[str]:
    src = (ROOT / "converter" / "vital_import.py").read_text(encoding="utf-8")
    pats: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            pats.add(norm(node.value))
        elif isinstance(node, ast.JoinedStr):
            text = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
            pats.add(norm(text.replace("{}", "1")))          # an index placeholder, folded like a digit
    return pats


# (regex on the normalised name, status, what is and is not done).  First match wins; status is "carried", "approx", "partial" or "missing".
RULES: list[tuple[str, str, str]] = [
    (r"osc_N_spectral_morph_(type|amount)", "partial", "spectral morph types 2-9 and 11 (formant scale, stretch, inharmonic, smear, random, low pass, high pass, phase disperse, skew) are measured and baked into the wavetable at the saved amount (macros and velocity folded in); vocode, Shepard and live amount modulation are not carried"),
    (r"osc_N_distortion_(type|amount|phase)", "partial", "phase distortion types 1-6 baked into the wavetable at the saved amount; ring modulation by another oscillator (types 10 and 11) runs live through the engine's RM warp with Warp Chain (Vital's depth, measured: calibration/vital_rm_probe.py); FM by another oscillator (types 7 and 8) runs through the PD warp at Vital's law, 6 cycles x amount squared (calibration/vital_fm_probe.py), and a live LFO / envelope route to the amount of an FM or RM oscillator moves it; FM by the sample (9), ring mod by the sample (12) and live amount modulation of types 1-6 are not carried"),
    (r"osc_N_stack_style", "carried", "stack styles 0-10 measured (unison lines against the plugin) and played by the engine's Vital unison law"),
    (r"osc_N_(unison_detune|detune_(range|power)|unison_blend|stereo_spread|unison_voices)", "carried", "detune width and voice spacing, blend level, stereo spread and the single-voice level measured through the plugin (calibration/vital_stack_probe.py) and run by the engine's Vital unison law"),
    (r"osc_N_(smooth_interpolation|random_phase|phase|view_2d|dimension.*)", "approx", "phase and frame interpolation are the engine's own"),
    (r"sample_(on|level|loop|keytrack|bounce|transpose|tune|pan|destination)", "carried", "sample oscillator carried as a one-region sample set (root note 60, loop, ping-pong, key tracking, transpose, tune, pan, routing); level measured"),
    (r"sample_.*", "missing", "random start phase and transpose quantize are not carried"),
    (r"filter_N_mix", "carried", "a linear dry / wet crossfade, measured (calibration/vital_fmix_probe.py) and run by the engine's hidden Filter Mix (macro / velocity routes are folded in at their saved values)"),
    (r"filter_N_keytrack", "partial", "positive key tracking measured and carried; negative is not"),
    (r"filter_fx_(keytrack|drive)", "missing", "not carried"),
    (r"filter_N_(formant_.*)", "approx", "formant filter is mapped to the nearest engine filter type"),
    (r"(filter_N|filter_fx)_(model|style|blend)", "approx", "filter models are mapped to the nearest engine types; the notes list the ones with no match"),
    (r"filter_N_blend_transpose", "partial", "carried for the comb filter (Vital comb law in the engine); other models ignore it"),
    (r"filter_N_filter_input", "carried", "serial routing measured and wired (engine routing slot 'Filter')"),
    (r"env_N_(delay|hold)", "missing", "delay and hold stages are not carried"),
    (r"env_N_(attack|decay|sustain|release)(_power)?", "partial", "carried for the amp and filter envelopes and the two spare ones; decay and release times are fitted to the engine's curve from the stage's power, the amp envelope is squared into the gain as Vital does (measured: calibration/vital_env_probe.py), macro and velocity routes to the times and sustain are added at their saved values"),
    (r"lfo_N_(delay_time|fade_time|phase|smooth_.*)", "carried", "LFO Delay, Fade In, Phase and Smooth follow synth_lfo.cpp and are checked against the plugin (calibration/vital_lfo_delay_probe.py)"),
    (r"lfo_N_stereo", "missing", "the stereo phase spread between the left and right LFO is not carried"),
    (r"lfo_N_(tempo|sync|frequency|keytrack_.*)", "partial", "rate carried; tempo sync at 120 BPM; keyboard-following LFOs at a fixed rate"),
    (r"random_N_.*", "missing", "random modulators (sample-and-hold, perlin) are not carried"),
    (r"portamento_slope", "missing", "the glide slope (curved glide) is not carried; the glide is a straight ramp"),
    (r"portamento_.*", "carried", "time, scale and force measured through the plugin (calibration/vital_porta_probe.py) and run by the engine's Vital glide law"),
    (r"compressor_(attack|release)", "carried", "carried into the engine's Vital compressor law (fx.h processCompVital)"),
    (r"voice_(tune|transpose)", "carried", "added to every oscillator's and the sample's tune, as Vital adds it to the note (key tracking and LFO key following do not see it)"),
    (r"voice_(override|priority|amplitude)|velocity_track|sample_tune|stereo_(mode|routing)", "partial", "some voice-level settings are carried (polyphony, legato, pitch bend range), the rest are not"),
    (r"(chorus|flanger|phaser|delay|reverb|distortion|compressor|eq)_.*", "carried", "measured laws for each effect, see TRANSLATION_RULES.md and the Vital notes"),
]


def classify(name: str, pats: set[str]) -> tuple[str, str]:
    n = norm(name)
    for rx, status, why in RULES:
        if re.fullmatch(rx, n):
            return status, why
    return ("carried", "read by the converter") if n in pats else ("missing", "not read by the converter")


def default_state() -> dict:
    from vital_render import VitalHost, split_state
    host = VitalHost()
    inner, off, ln = split_state(host.template)
    chunk = inner[off:off + ln]
    return json.loads(chunk[: chunk.rfind(b"}") + 1])["settings"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folder", nargs="?", default=os.path.expanduser("~/OneDrive/Documents/Vital"))
    ap.add_argument("-o", "--out", default=str(ROOT / "docs" / "VITAL_COVERAGE.md"))
    a = ap.parse_args()

    dflt = default_state()
    files = sorted(Path(a.folder).rglob("*.vital"))
    presets = []
    for f in files:
        try:
            presets.append(json.loads(f.read_text(encoding="utf-8"))["settings"])
        except Exception:
            pass
    pats = converter_patterns()

    used: dict[str, int] = Counter()
    for s in presets:
        for k, v in s.items():
            if k in ("modulations", "lfos", "wavetables", "sample"):
                continue
            d = dflt.get(k)
            if isinstance(v, (int, float)) and isinstance(d, (int, float)) and abs(v - d) > 1e-6:
                used[norm(k)] += 1                              # presets that changed any member of the family
    fam_members: dict[str, list[str]] = defaultdict(list)
    for k in dflt:
        if k in ("modulations", "lfos", "wavetables", "sample"):
            continue
        fam_members[norm(k)].append(k)
    # presets using a family: count each preset once per family
    fam_used: dict[str, int] = Counter()
    for s in presets:
        seen = set()
        for k, v in s.items():
            d = dflt.get(k)
            if isinstance(v, (int, float)) and isinstance(d, (int, float)) and abs(v - d) > 1e-6:
                seen.add(norm(k))
        for n in seen:
            fam_used[n] += 1

    rows = []
    for fam, members in fam_members.items():
        status, why = classify(members[0], pats)
        rows.append((fam, len(members), fam_used.get(fam, 0), status, why))

    # modulation: each active route is replayed through the converter's own destination table (dest_spec) and its source rules
    sys.path.insert(0, str(ROOT / "converter"))
    import vital_import as VI
    src_c, dst_c, dst_ok = Counter(), Counter(), Counter()
    dropped: Counter = Counter()
    mod_total = 0
    for s in presets:
        for i, m in enumerate(s.get("modulations", [])):
            if not (m.get("source") and m.get("destination")) or s.get(f"modulation_{i + 1}_bypass") or abs(s.get(f"modulation_{i + 1}_amount", 0)) < 1e-6:
                continue
            src, dest = m["source"], m["destination"]
            mod_total += 1
            src_c[norm(src)] += 1
            dst_c[norm(dest)] += 1
            if dest.startswith("modulation_"):
                ok = src.startswith("macro_control_") or src == "velocity"          # meta-modulation: static sources are folded in
            elif re.fullmatch(r"env_[1-6]_(attack|decay|release|sustain)", dest) and (src.startswith("macro_control_") or src == "velocity"):
                ok = True                                       # folded into the envelope's stored value at the saved macro / velocity 100
            else:
                spec = VI.dest_spec(dest, s, {}, 1)
                if spec is None:
                    ok = False
                elif src.startswith(("lfo_", "env_")):
                    ok = spec["lr"] is not None
                elif src.startswith("macro_control_") or src == "velocity":
                    ok = spec["key"] is not None
                else:
                    ok = False
            if ok:
                dst_ok[norm(dest)] += 1
            else:
                dropped[(norm(src), norm(dest))] += 1
    wt_mod = Counter()
    for s in presets:
        for w in s.get("wavetables", []):
            for g in w.get("groups", []):
                for c in g.get("components", []):
                    wt_mod[c.get("type", "?")] += 1

    order = {"missing": 0, "partial": 1, "approx": 2, "carried": 3}
    rows.sort(key=lambda r: (order[r[3]], -r[2], r[0]))
    n = len(presets)
    out = [f"# Vital import coverage", "",
           f"Generated by `calibration/vital_coverage.py` from the plugin's own default state ({sum(len(v) for v in fam_members.values())} parameters in {len(rows)} families) "
           f"and {n} local presets.  *Used* = presets of {n} that change at least one member of the family from its default.", "",
           "Status: **missing** nothing is done; **partial** carried for some values or settings; **approx** carried by a nearest-match or a guess; **carried** read and measured.", ""]
    for st in ("missing", "partial", "approx", "carried"):
        group = [r for r in rows if r[3] == st]
        out += [f"## {st.capitalize()} ({len(group)} families)", "", "| Family | Params | Used | Note |", "|---|---:|---:|---|"]
        out += [f"| `{r[0]}` | {r[1]} | {r[2]} | {r[4]} |" for r in group]
        out.append("")
    carried_routes = mod_total - sum(dropped.values())
    out += ["## Modulation", "",
            f"{mod_total} active routes in the {n} presets; {carried_routes} are carried ({100 * carried_routes // max(mod_total, 1)} %). "
            "Each route is replayed through the converter's own destination table and source rules (LFOs and envelopes with a live destination, macros and velocity folded in as static offsets; "
            "mod wheel, aftertouch, pitch wheel, key, lift, stereo and random sources are not carried).", "",
            "| Source | Routes |", "|---|---:|"]
    out += [f"| `{k}` | {v} |" for k, v in src_c.most_common()]
    out += ["", "### Routes not carried, by source and destination", "", "| Source | Destination | Routes |", "|---|---|---:|"]
    out += [f"| `{a_}` | `{b_}` | {v} |" for (a_, b_), v in dropped.most_common(45)]
    out += ["", "### Destinations", "", "| Destination | Routes | Carried |", "|---|---:|---:|"]
    out += [f"| `{k}` | {v} | {dst_ok.get(k, 0)} |" for k, v in dst_c.most_common(60)]
    out += ["", "## Wavetable modifiers", "", "| Modifier | Uses | Status |", "|---|---:|---|"]
    out += [f"| `{k}` | {v} | {'carried' if k in ('Wave Source', 'Audio File Source', 'Line Source', 'Wave Folder', 'Frequency Filter', 'Wave Warp', 'Phase Shift') else 'not carried (the converter notes it and plays the unmodified source)'} |" for k, v in wt_mod.most_common()]
    Path(a.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {a.out}: {len(rows)} families, {n} presets")
    for st in order:
        print(st, sum(1 for r in rows if r[3] == st))


if __name__ == "__main__":
    main()
