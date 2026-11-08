"""Serum 2 FX -> the TinyHouse FX block (src/dsp/fx.h).

What is mapped, and how each part is known:

  EQ      every rack-0 EQ band is rebuilt from Serum's stored values with the laws measured in
          calibration/fx_probe.py (types, Q from Reso, the unwritten defaults), summed into one
          target curve, and reduced to the engine's four bands if there are more than four.
  Delay   times, mode, feedback and the feedback filter follow measured laws; the mix comes from
          measuring the preset's own delay in Serum (fx_fingerprints.json).
  Reverb  Serum's reverb is not modelled. Its decay, brightness and width are measured on the
          preset's own reverb in Serum, and the engine's are searched until the same
          measurement on the real Tablor engine agrees (closed loop, tablor_cli.exe).
  Width   chorus, Hyper, flanger and phaser together are one width control, matched on the
          side energy they add to a noise burst, the same way.

Not mapped, and said so in the report: distortion, compressor, filters, bit/frequency shift,
stereo utilities, convolution that is not a reverb, FX in the two bus racks, and any
modulation of an FX parameter.
"""
from __future__ import annotations

import copy
import json
import math
import re
import struct
import subprocess
import tempfile
from pathlib import Path

import numpy as np

import fxmetrics as M

ROOT = Path(__file__).resolve().parent
CLI = ROOT / "calibration/tablor_cli.exe"
FINGERPRINTS = ROOT / "calibration/fx_fingerprints.json"
TABLOR_CACHE = ROOT / "calibration/fx_tablor_cache.json"
SR = 44100

EQ_OFF, EQ_BELL, EQ_LOSHELF, EQ_HISHELF, EQ_HIPASS, EQ_LOPASS = range(6)
KIND_ID = {"bell": EQ_BELL, "loshelf": EQ_LOSHELF, "hishelf": EQ_HISHELF, "hp": EQ_HIPASS, "lp": EQ_LOPASS}

# Serum's Reso (0..100) -> Q, measured (fx_probe.py eqfit). Identical to potEqQ in fx.h.
RESO = [0, 10, 25, 40, 50, 60, 75, 90, 100]
Q_BELL = [0.10, 0.10, 0.16, 0.58, 1.25, 2.4, 5.5, 10.0, 14.0]
Q_SHELF = [0.40, 0.40, 0.44, 0.51, 0.64, 0.79, 1.13, 1.67, 2.10]

# What an EQ band is when its key is not written (fx_probe.py eqfit, defaults section)
EQ_DEFAULT = {1: {"type": 0, "freq": 200.0, "reso": 60.0}, 2: {"type": 0, "freq": 2100.0, "reso": 60.0}}
BAND_KIND = {(1, 0): "loshelf", (2, 0): "hishelf", (1, 1): "bell", (2, 1): "bell", (1, 2): "hp", (2, 2): "lp"}

# Delay (fx_probe.py delay, delaydef): time in ms is 5030 x the stored value, the unwritten Time is
# 0.1, feedback 40 %, and the feedback filter a very wide band-pass.
DELAY_MS_PER_UNIT = 5030.0          # no longer read: the stored time is a position in a list of note lengths (delay_ms below)
# Serum's delay time is tempo-synced and the stored TimeL/TimeR is a position on a stepped knob, not milliseconds. Measured in Serum
# (calibration/delay_sync_probe.json: the first echo of a burst for TimeL = TimeR from 0 to 0.25) at its default 120 BPM: edges between steps at
# about 0.032, 0.063, 0.105 and 0.185 and the lengths 1/16, 1/8, 1/4, 1/2 and one bar. A preset that stores no time plays 1/4. OffsetL / OffsetR
# multiply their side's time (1.5 is a dotted note: a 1/8 plays 0.375 s). At another tempo the real thing follows it; this stays at 120 BPM.
DELAY_STEPS = [(0.004, 62.5), (0.032, 125.0), (0.063, 250.0), (0.105, 500.0), (0.185, 1000.0), (9.9, 2000.0)]
DELAY_DEFAULT_POS = 0.083
DELAY_BW_SCALE = 1.6               # the filter's edges are at Freq / 2^(BW x scale / 2) and Freq x 2^(BW x scale / 2): fitted against Serum (calibration probe_delay_filter2.py: third-octave response of eight Freq / BW pairs; a 1-pole high-pass and low-pass, unity gain at the centre; 2.0 was the older, coarser fit)


def delay_ms(pp: dict, side: str = "L") -> float:
    v = float(pp.get(f"kParamTime{side}", DELAY_DEFAULT_POS))
    base = next(ms for edge, ms in DELAY_STEPS if v <= edge)
    return base * float(pp.get(f"kParamOffset{side}", 1.0))

DELAY_DEFAULT = {"time": 0.1, "fb": 40.0, "freq": 1000.0, "bw": 6.75, "mode": 0}

EQ_SLOTS = 4
FGRID = np.geomspace(40.0, 18000.0, 72)


def kind_of(node: dict) -> str | None:
    return next((k[2:] for k in node if k.startswith("FX")), None)


def plain(node: dict) -> dict:
    p = node.get(f"FX{kind_of(node)}", {}).get("plainParams")
    return p if isinstance(p, dict) else {}


def enabled(node: dict) -> bool:
    return plain(node).get("kParamEnable", 1.0) != 0.0


WET_DEFAULT = {"Reverb": 25.0, "Delay": 25.0, "Conv": 25.0}
DRY_KINDS = ("Distortion", "Comp", "Bode", "Filter", "Utils", "Split", "SplitMS")      # effects whose Wet 0 is bypass (not Chorus / Flanger / Phaser: those still add)


def fold_macro_wets(nodes: list[dict], mods: list[dict], macros: list[float]) -> list[dict]:
    """The rack with every macro that drives an effect's Wet applied at its saved position (Serum adds amount x macro, in
    percentage points of Wet; a Distortion / Comp / Bode / Filter / Utils / Split that stores no Wet is fully wet, which a -100 % route at macro 0 leaves at 100: ARP - Aardvark,
    FX - Church, PD - Butterbrot). Envelope and LFO routes to a Wet are dynamic and left alone."""
    out = copy.deepcopy(nodes)
    for m in mods or []:
        if not (m.get("dest_type", "").startswith("FX") and m.get("dest_param") == "kParamWet" and 25 <= m["source"] <= 32 and macros):
            continue
        i = m.get("dest_id")
        if not isinstance(i, int) or not 0 <= i < len(out) or kind_of(out[i]) != m["dest_type"][2:]:
            continue
        mv = macro_value(m, macros)
        node = out[i]
        key = f"FX{kind_of(node)}"
        if not isinstance(node[key].get("plainParams"), dict):
            node[key]["plainParams"] = {}
        pp = node[key]["plainParams"]
        d = (2.0 * mv - 1.0) if m.get("bipolar") else mv
        pp["kParamWet"] = min(max(pp.get("kParamWet", WET_DEFAULT.get(kind_of(node), 100.0 if kind_of(node) in DRY_KINDS else 0.0)) + m["amount_pct"] * d, 0.0), 100.0)
    return out


# Macro routes into an EQ band's knobs, measured on Serum (calibration/probe_fx_macro.py eq: noise through a bell / low-pass with a route from Macro 1 at 100 %): the gain
# knob spans -24..+24 dB, so a route adds 0.48 dB per percent (25 % from 0 dB gave +11.9, 50 % from -12 dB +11.8, 100 % clipped at +24); the frequency knob is a log scale from
# 21.5332 Hz (the value Serum stores for the knob's left end) to 20 kHz, so a route adds its amount to the knob position (+10 % = +0.99 octave, +25 % from 3 kHz = 16.9 kHz). A
# macro only sits at its saved position (a bipolar route is the macro minus one half, probe_macro_bipolar.py), so the route is folded into the stored value. Before this a
# macro-driven EQ was carried at its stored knobs (Camp Fire 1: a low-pass at 21 Hz that the HIGH CUT macro opens to 20 kHz stayed shut, 28 dB too quiet).
# A Distortion's Drive is a percentage and a route adds its amount in percentage points (probe_fx_macro.py dist: drive 10/25/50 + 20/40/-10 % x macro 100 % gave the static
# drive of the sum on the 3rd harmonic of a sine, to 0.1).
EQ_GAIN_RANGE_DB = 48.0
EQ_FREQ_MIN, EQ_FREQ_MAX = 21.5332, 20000.0
FOLDED_KNOBS = {"FXEQ": ("kParamGain1", "kParamGain2", "kParamFreq1", "kParamFreq2"), "FXDistortion": ("kParamDrive",)}


def fold_macro_knobs(nodes: list[dict], mods: list[dict], macros: list[float]) -> list[dict]:
    """The rack with the macros that drive an EQ band's gain or frequency, or a Distortion's drive, applied at their saved position."""
    out = copy.deepcopy(nodes)
    for m in mods or []:
        if m.get("dest_param") not in FOLDED_KNOBS.get(m.get("dest_type"), ()) or not (25 <= m["source"] <= 32 and macros):
            continue
        i = m.get("dest_id")
        kind = m["dest_type"][2:]
        if not isinstance(i, int) or not 0 <= i < len(out) or kind_of(out[i]) != kind:
            continue
        d = macro_value(m, macros) - (0.5 if m.get("bipolar") else 0.0)
        a = m["amount_pct"] / 100.0
        if not isinstance(out[i][f"FX{kind}"].get("plainParams"), dict):
            out[i][f"FX{kind}"]["plainParams"] = {}
        pp = out[i][f"FX{kind}"]["plainParams"]
        param = m["dest_param"]
        band = int(param[-1]) if kind == "EQ" else 0
        if kind == "Distortion":
            pp[param] = min(max(float(pp.get(param, DIST_DEFAULT_DRIVE)) + m["amount_pct"] * d, 0.0), 100.0)
        elif param.startswith("kParamGain"):
            pp[param] = min(max(float(pp.get(param, 0.0)) + a * d * EQ_GAIN_RANGE_DB, -EQ_GAIN_RANGE_DB / 2), EQ_GAIN_RANGE_DB / 2)
        else:
            span = math.log(EQ_FREQ_MAX / EQ_FREQ_MIN)
            x = math.log(max(float(pp.get(param, EQ_DEFAULT[band]["freq"])), EQ_FREQ_MIN) / EQ_FREQ_MIN) / span
            pp[param] = EQ_FREQ_MIN * math.exp(min(max(x + a * d, 0.0), 1.0) * span)
    return out


def norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


# ------------------------------------------------------------------------------ EQ
def q_from_reso(kind: str, reso: float) -> float:
    tab = Q_SHELF if kind in ("loshelf", "hishelf") else Q_BELL
    return float(math.exp(np.interp(reso, RESO, np.log(tab))))


def reso_from_q(kind: str, q: float) -> float:
    tab = Q_SHELF if kind in ("loshelf", "hishelf") else Q_BELL
    return float(np.interp(math.log(max(q, 1e-3)), np.log(tab), RESO))


def macro_value(m: dict, macros: list) -> float:
    """The value 0..1 a macro gives a route: the macro's position, through the route's own curve when it has one."""
    x = macros[m["source"] - 25] / 100.0
    lut = m.get("curve")
    return float(np.interp(x, np.linspace(0.0, 1.0, len(lut)), lut)) if lut else x


def biquad_db(kind: str, fc: float, gain_db: float, q: float, f) -> np.ndarray:
    """RBJ cookbook magnitude at f, at 44.1 kHz: the filters fx.h runs."""
    fc = min(max(fc, 10.0), SR * 0.45)
    w0 = 2 * np.pi * fc / SR
    A = 10 ** (gain_db / 40)
    cw, sw = np.cos(w0), np.sin(w0)
    al = sw / (2 * max(q, 0.05))
    if kind == "bell":
        b = [1 + al * A, -2 * cw, 1 - al * A]; a = [1 + al / A, -2 * cw, 1 - al / A]
    elif kind == "loshelf":
        r = 2 * np.sqrt(A) * al
        b = [A * ((A + 1) - (A - 1) * cw + r), 2 * A * ((A - 1) - (A + 1) * cw), A * ((A + 1) - (A - 1) * cw - r)]
        a = [(A + 1) + (A - 1) * cw + r, -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - r]
    elif kind == "hishelf":
        r = 2 * np.sqrt(A) * al
        b = [A * ((A + 1) + (A - 1) * cw + r), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - r)]
        a = [(A + 1) - (A - 1) * cw + r, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - r]
    elif kind == "hp":
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]; a = [1 + al, -2 * cw, 1 - al]
    else:
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]; a = [1 + al, -2 * cw, 1 - al]
    z = np.exp(-1j * 2 * np.pi * np.asarray(f) / SR)
    h = (b[0] + b[1] * z + b[2] * z ** 2) / (a[0] + a[1] * z + a[2] * z ** 2)
    return 20 * np.log10(np.maximum(np.abs(h), 1e-9))


def branch_nodes(nodes: list[dict]) -> set[int]:
    """Indices of the nodes that sit inside a Split / SplitMS branch rather than on the master
    signal. After a split node come ModuleCount1 modules for the first branch and ModuleCount2
    for the second (each 1 when unwritten); everything after those is back on the master. An
    EQ inside a branch shapes only that half of the signal, so it is not a master EQ."""
    inside, skip = set(), 0
    for i, n in enumerate(nodes):
        if skip > 0:
            inside.add(i)
            skip -= 1
            continue
        if kind_of(n) in ("Split", "SplitMS") and enabled(n):
            pp = plain(n)
            skip = int(round(pp.get("kParamModuleCount1", 1))) + int(round(pp.get("kParamModuleCount2", 1)))
    return inside


def eq_bands(nodes: list[dict]) -> list[dict]:
    """Every audible band of every master-signal rack-0 EQ, from Serum's stored values."""
    out = []
    inside = branch_nodes(nodes)
    for i, n in enumerate(nodes):
        if kind_of(n) != "EQ" or not enabled(n) or i in inside:
            continue
        pp = plain(n)
        for b in (1, 2):
            d = EQ_DEFAULT[b]
            ty = int(round(pp.get(f"kParamType{b}", d["type"])))
            kind = BAND_KIND.get((b, ty))
            if kind is None:
                continue
            fc = float(pp.get(f"kParamFreq{b}", d["freq"]))
            gain = float(pp.get(f"kParamGain{b}", 0.0))
            q = q_from_reso(kind, float(pp.get(f"kParamReso{b}", d["reso"])))
            if kind == "hp":
                if fc < 25:
                    continue
                gain = 0.0
            elif kind == "lp":
                if fc > 19000:
                    continue
                gain = 0.0
            elif abs(gain) < 0.25:
                continue
            out.append({"kind": kind, "fc": fc, "gain": gain, "q": q})
    return out


# Serum's FX Filter, taken as a static low-pass or high-pass in the EQ stage. Its cutoff is the same scale as the
# voice filter's (8.18 Hz * 2697^x). Only plain LP/HP models are carried (the ladders, whose 18/24 dB slope is
# approximated by the EQ's 12 dB biquad at the same cutoff); band-pass, comb, formant and DJ-mixer models are
# not. A filter whose cutoff, wet or resonance is moved by anything but a macro (envelope, LFO, velocity) is
# dynamic and is not carried: macros sit at their saved value, so they are folded into the cutoff.
FX_FILTER_LP = {"MgL24", "L24", "MgL18", "L18", "DirtyMg", "LadderAcid", "ZDF_A", "MgL12", "L12", "MgL6", "L6"}
FX_FILTER_HP = {"H24", "H18", "H12", "H6"}
FX_FILTER_RATIO = {"MgL24": 0.47, "L24": 0.47, "MgL18": 0.53, "L18": 0.53, "DirtyMg": 0.53, "LadderAcid": 0.53, "ZDF_A": 0.53,
                   "MgL12": 0.67, "L12": 0.67, "MgL6": 1.0 / 0.64, "L6": 1.0 / 0.64,
                   "H24": 0.9, "H18": 0.9, "H12": 0.9, "H6": 0.9}      # -3 dB point / stated cutoff, measured
FX_FILTER_POLES = {"MgL24": 4, "L24": 4, "MgL18": 3, "L18": 3, "DirtyMg": 3, "LadderAcid": 3, "ZDF_A": 3, "MgL12": 2, "L12": 2, "MgL6": 1, "L6": 1,
                    "H24": 4, "H18": 3, "H12": 2, "H6": 1}   # DirtyMg, LadderAcid and ZDF_A unmeasured (taken as three); the H types: probe_fxfilter_types.py --hp
FX_FILTER_MIN_WET = 50.0                 # below this the filtered signal is too small a part of the output to stand in for


def serum_filter_hz(norm: float) -> float:
    return 8.18 * 2697.0 ** min(max(norm, 0.0), 1.0)


# The default-type Filter FX with resonance, measured on Serum (calibration/probe_fxfilter_drive.py: noise through one Filter FX, half-octave points,
# cutoff 0.7 = 2062 Hz at its -3 dB point). Resonance lowers the passband (about -2.8 dB at the default Reso 10, -7.3 at 35, -14 at 100) and puts a peak
# at the cutoff (+2 dB above the passband at 10, +10 at 35, +13 at 50), while the stop band stays on the 6 dB/octave slope. Drive is a plain gain
# (+3.2 dB at 10, +7.7 at 25, +14 at 50, +23.5 at 100) that does not depend on the level, whatever the type.
FX_FILTER_DRIVE_DB = [(0, 0.0), (10, 3.2), (25, 7.7), (50, 14.0), (75, 18.8), (100, 23.5)]
FX_FILTER_RESO_FILE = Path(__file__).resolve().parent / "calibration/fxfilter_drive_probe2.json"
FX_FILTER_RESO_MAXDB = 14.0              # the half-octave probe cannot resolve the self-oscillating peak above Reso 70; a peak that high is not asked of one band
_reso_table: dict = {}


def fx_filter_reso(reso: float, f) -> tuple[np.ndarray, float]:
    """The default Filter FX's response change from its resonance, in dB at frequencies f relative to the same filter at Reso 0 (the cutoff
    is the one given by `fc3` in the caller: f must be in units of it), and its passband gain. Interpolated between the probed resonances."""
    if not _reso_table:
        d = json.loads(FX_FILTER_RESO_FILE.read_text(encoding="utf-8")) if FX_FILTER_RESO_FILE.exists() else {}
        grid = np.array(d.get("grid", []), dtype=float)
        rows = {int(k.split("|")[2][1:]): np.array(v) for k, v in d.items() if k.startswith("default|f0.7|r") and k.endswith("|d0")}
        if len(grid) and 0 in rows:
            base = rows.pop(0)
            rows = {r: np.minimum(v - base, FX_FILTER_RESO_MAXDB) for r, v in rows.items()}
            _reso_table.update(octaves=np.log2(grid / 2062.0), resos=np.array([0] + sorted(rows)), delta=np.array([np.zeros(len(grid))] + [rows[r] for r in sorted(rows)]))
    if not _reso_table:
        return np.zeros(len(f)), 0.0
    t = _reso_table
    lo = np.array([np.interp(reso, t["resos"], t["delta"][:, k]) for k in range(t["delta"].shape[1])])
    out = np.interp(np.log2(np.asarray(f, dtype=float)), t["octaves"], lo, left=lo[0], right=0.0)
    return out, float(lo[0])


def fit_filter_curve(target: np.ndarray, fc: float, n: int = 3, stop: float = 1.0) -> list[dict]:
    """A few EQ bands (greedy: the best single band for what is still missing, three times at most) following a Filter FX's curve."""
    bands, resid = [], target.copy()
    for _ in range(n):
        best = None
        for kind, centre, spread in (("hishelf", fc, 1.5), ("loshelf", fc, 1.5), ("bell", 0.85 * fc, 1.0)):
            b, err = fit_one(resid, kind, centre, spread)
            if best is None or err < best[0]:
                best = (err, b)
        bands.append(best[1])
        resid = resid - biquad_db(best[1]["kind"], best[1]["fc"], best[1]["gain"], best[1]["q"], FGRID)
        if best[0] < stop:
            break
    return refine_bands(bands, target)


def refine_bands(bands: list[dict], target: np.ndarray) -> list[dict]:
    """Coordinate descent on every band's frequency, gain and Q together: the greedy fit places each band for what the one before left, and two
    shelves and a peak that must sum to a resonant low-pass need adjusting against each other."""
    bands = [dict(b) for b in bands]
    qmax = {"bell": 14.0}
    err_of = lambda bs: float(np.sqrt(np.mean((curve(bs) - target) ** 2)))
    best = err_of(bands)
    steps = [0.5, 3.0, 0.4]                                     # octaves of frequency, dB of gain, octaves of Q
    while steps[0] > 0.01:
        improved = False
        for b in bands:
            for k, key in enumerate(("fc", "gain", "q")):
                for sign in (1.0, -1.0):
                    old = b[key]
                    if key == "fc":
                        b[key] = min(max(old * 2 ** (sign * steps[0]), 20.0), 20000.0)
                    elif key == "gain":
                        b[key] = min(max(old + sign * steps[1], -24.0), 24.0)
                    else:
                        b[key] = min(max(old * 2 ** (sign * steps[2]), 0.3), qmax.get(b["kind"], 4.0))
                    e = err_of(bands)
                    if e < best - 1e-4:
                        best, improved = e, True
                        break
                    b[key] = old
        if not improved:
            steps = [x / 2 for x in steps]
    return bands


FILTER_FX_ENABLED = True       # calibration/ablation scripts turn this off to hear the preset without its Filter effect


# Envelope sources are 2..5; 0 and 1 are not moved in a render (a velocity sweep changes nothing audible), so they are set aside.
ENV_SOURCES = range(2, 6)


def dynamic_filter(nodes: list[dict], ctx: dict | None) -> dict | None:
    """A master-rack Filter FX that an envelope moves (and nothing else but macros): the engine runs it as its second voice filter.
    Returns {"index", "model", "norm", "reso", "envs": {env index: amount}, "ignored": [sources]} or None. The cutoff is the stored
    value plus the macros at their saved position; the envelope amounts are fractions of the cutoff knob."""
    mods = [m for m in (ctx or {}).get("mods", []) if m.get("dest_type") == "FXFilter"]
    macros = (ctx or {}).get("macros", [])
    inside = branch_nodes(nodes)
    fx = [i for i, n in enumerate(nodes) if kind_of(n) == "Filter" and enabled(n) and i not in inside]
    if not fx or not FILTER_FX_ENABLED:
        return None
    i = fx[0]
    pp = plain(nodes[i])
    envs, ignored, norm = {}, [], float(pp.get("kParamFreq", 0.5))
    reso = float(pp.get("kParamReso", 10.0))
    for m in mods:
        if m.get("dest_param") != "kParamFreq":
            if 6 <= m["source"] <= 15:
                return None
            continue
        a = m["amount_pct"] / 100.0
        if 25 <= m["source"] <= 32 and macros:
            mv = macro_value(m, macros)
            norm += a * ((2.0 * mv - 1.0) if m.get("bipolar") else mv)
        elif m["source"] in ENV_SOURCES:
            envs[m["source"] - 2] = envs.get(m["source"] - 2, 0.0) + a * (0.5 if m.get("bipolar") else 1.0)
        elif 6 <= m["source"] <= 15:
            return None                                 # an LFO moves it: not carried
        else:
            ignored.append(m["source"])
    if not envs or float(pp.get("kParamWet", 100.0)) < FX_FILTER_MIN_WET:
        return None
    return {"index": i, "model": str(pp.get("kParamType", "default")), "norm": norm, "reso": reso, "envs": envs, "ignored": ignored}


def filter_bands(nodes: list[dict], ctx: dict | None) -> tuple[list[dict], list[str]]:
    """The static low-pass / high-pass Filter FX nodes of the master rack as EQ bands, and why any were left out."""
    out, why = [], []
    if not FILTER_FX_ENABLED:
        return out, why
    inside = branch_nodes(nodes)
    mods = (ctx or {}).get("mods", [])
    macros = (ctx or {}).get("macros", [])
    fmods = [m for m in mods if m.get("dest_type") == "FXFilter"]
    dynamic = any(not (25 <= m["source"] <= 32) for m in fmods)
    spec = dynamic_filter(nodes, ctx)
    for i, n in enumerate(nodes):
        if kind_of(n) != "Filter" or not enabled(n):
            continue
        pp = plain(n)
        if i in inside:
            why.append("Filter in a Split branch")
            continue
        if spec is not None and spec["index"] == i:
            continue                                    # moved by an envelope: the engine's second filter carries it (dynamic_filter)
        # An unwritten type is Serum's default, measured with test patches (calibration/probe_filterfx.py): a 6 dB/octave
        # low-pass with its -3 dB point at the stated cutoff.
        ty = str(pp.get("kParamType", "default"))
        kind = "lp" if (ty in FX_FILTER_LP or ty == "default") else "hp" if ty in FX_FILTER_HP else None
        if kind is None:
            why.append(f"Filter model {ty}")
            continue
        wet = float(pp.get("kParamWet", 100.0))
        if wet < FX_FILTER_MIN_WET:
            continue                                    # off, or too little of the signal to matter: not worth a note
        if dynamic:
            why.append("Filter moved by an envelope/LFO/velocity")
            continue
        norm = float(pp.get("kParamFreq", 0.5))
        for m in fmods:
            if m.get("dest_param") == "kParamFreq" and macros:
                mv = macro_value(m, macros)
                norm += m["amount_pct"] / 100.0 * ((2.0 * mv - 1.0) if m.get("bipolar") else mv)
        # The stated cutoff is not where the filter is 3 dB down. Measured on Serum (probe_filterfx.py), the -3 dB point as a
        # fraction of the stated cutoff is 0.47 for the 24 dB ladders, 0.53 for 18 dB, 0.67 for 12 dB; the engine's biquad has
        # its -3 dB point at its own frequency, so that is where it goes. The 6 dB/octave default is matched by a critically
        # damped biquad (Q 0.5, -3 dB at 0.64 of its frequency) placed so its -3 dB point is at the stated cutoff.
        ratio = FX_FILTER_RATIO.get(ty, 1.0 / 0.64 if ty == "default" else 0.9)
        fc = serum_filter_hz(norm) * ratio
        reso = float(pp.get("kParamReso", 0.0))                # an unwritten Reso plays as 0 (no passband loss, no peak): measured, probe_fxfilter_types.py
        q = (0.5 if ty == "default" else 0.7071) + 4.0 * min(max(reso, 0.0), 100.0) / 100.0 * (0.0 if ty == "default" else 1.0)
        if (kind == "lp" and fc > 19000) or (kind == "hp" and fc < 25):
            continue                                    # out of the audible band: nothing to carry
        if kind == "lp" and (ty in ("default", "MgL6", "L6")):
            # A 6 dB/octave low-pass (the default Filter FX, MG Low 6): the engine's EQ has only 12 dB/octave biquads, and one of those falls
            # twice as fast past the corner (Keys - Zerb's high end was 12 dB too low at 12 kHz). A high shelf of up to -24 dB, fitted to the
            # one-pole curve, follows it through the audible range. The stated cutoff is the -3 dB point.
            fc3 = serum_filter_hz(norm)
            if ty == "default":
                # with its resonance (the passband and the peak at the cutoff) and, mixed under a Wet below 100, with the dry signal; the Drive is a gain
                # that only counts against that dry signal (an all-over gain is the preset's loudness, fitted afterwards)
                h = 1.0 / (1.0 + 1j * FGRID / fc3)
                delta, _ = fx_filter_reso(reso, FGRID / fc3)
                w = min(max(wet, 0.0), 100.0) / 100.0
                drive = float(np.interp(float(pp.get("kParamDrive", 0.0)), [d for d, _ in FX_FILTER_DRIVE_DB], [g for _, g in FX_FILTER_DRIVE_DB]))
                lin = 10 ** ((drive if w < 0.99 else 0.0) / 20) * 10 ** (delta / 20)
                target = 20.0 * np.log10(np.maximum(np.abs((1.0 - w) + w * h * lin), 1e-6))
                out += fit_filter_curve(target, fc3)
                continue
            target = -10.0 * np.log10(1.0 + (FGRID / fc3) ** 2)
            band, _ = fit_one(target, "hishelf", fc3, spread=1.5)
            out.append(band)
            continue
        if kind in ("lp", "hp") and ty in FX_FILTER_POLES:
            # A ladder type with no resonance is that many identical one-pole low-passes in a row at the stated cutoff (measured on Serum,
            # probe_fxfilter_types.py: 800 Hz of a stated 630 Hz is -16.7 dB for four, -12.5 for three, -8.3 for two, to within 0.5 dB). Two of them
            # are a Q 0.5 biquad; an odd one left over is fitted by a shelf, since the engine's EQ has no one-pole.
            n = FX_FILTER_POLES[ty]
            fp = serum_filter_hz(norm)
            out += [{"kind": kind, "fc": fp, "gain": 0.0, "q": 0.5} for _ in range(n // 2)]
            if n % 2:
                one = (FGRID / fp) ** 2 if kind == "lp" else (fp / FGRID) ** 2
                band, _ = fit_one(-10.0 * np.log10(1.0 + one), "hishelf" if kind == "lp" else "loshelf", fp, spread=1.5)
                out.append(band)
            continue
        out.append({"kind": kind, "fc": fc, "gain": 0.0, "q": q})
    return out, why


# The Distortion effect has a filter of its own, before or after the distorting (PrePost 2 is before, 1 is after; 0 and unwritten is off: noise through
# Overdrive at Drive 100, calibration/probe_dist_prepost.py, and Bass - AVAION, whose bass keeps the harmonics the overdrive makes above its 144 Hz filter), a 12 dB/octave state-variable filter
# at Freq with BW as its resonance and LPHP morphing from low-pass (0, and unwritten) to high-pass (100). Measured with noise through Drive 0
# (calibration/probe_dist_filter.py): at Freq 0.5 the -3 dB point is the stated cutoff (serum_filter_hz), the peak over the passband is 1.3 dB at BW 1, 4.3 at
# BW 2 (the unwritten BW is about 2) and 6.9 at BW 3, which is a biquad with Q = 0.5 + 0.55 BW. Only the two ends of LPHP are carried, and only a filter AFTER
# the distortion: the engine's EQ comes after it, and a filter before it (PrePost 2) decides what the distortion makes harmonics of, which an EQ cannot do.
DIST_FILTER_Q_BASE, DIST_FILTER_Q_PER_BW, DIST_FILTER_BW_DEFAULT = 0.5, 0.55, 2.0


def dist_filter_bands(nodes: list[dict]) -> tuple[list[dict], list[str]]:
    out, why = [], []
    inside = branch_nodes(nodes)
    for i, n in enumerate(nodes):
        if kind_of(n) != "Distortion" or not enabled(n) or i in inside:
            continue
        pp = plain(n)
        pre_post = int(round(pp.get("kParamPrePost", 0.0)))
        if pre_post == 2 and pp.get("kParamWet", 100.0) >= 1.0:
            why.append("Distortion filter before the distortion")
        if pre_post != 1 or pp.get("kParamWet", 100.0) < 1.0:
            continue
        t = float(pp.get("kParamLPHP", 0.0)) / 100.0
        if 0.2 < t < 0.8:
            why.append(f"Distortion filter morph {t * 100:.0f} % (between low-pass and high-pass)")
            continue
        kind = "lp" if t <= 0.2 else "hp"
        fc = serum_filter_hz(float(pp.get("kParamFreq", 0.5)))
        q = DIST_FILTER_Q_BASE + DIST_FILTER_Q_PER_BW * float(pp.get("kParamBW", DIST_FILTER_BW_DEFAULT))
        if (kind == "lp" and fc > 19000) or (kind == "hp" and fc < 25):
            continue
        w = min(max(float(pp.get("kParamWet", 100.0)), 0.0), 100.0) / 100.0
        if w < 0.99:
            # only the distorted part goes through the filter: the dry part stays, so the cut is shallower
            f0 = fc
            x = FGRID / f0
            h = (1.0 / (1.0 - x ** 2 + 1j * x / q)) if kind == "lp" else (-(x ** 2) / (1.0 - x ** 2 + 1j * x / q))
            target = 20.0 * np.log10(np.maximum(np.abs((1.0 - w) + w * h), 1e-6))
            out += fit_filter_curve(target, fc, n=2)
        else:
            out.append({"kind": kind, "fc": fc, "gain": 0.0, "q": q})
    return out, why


# Serum's Distortion onto the engine's stage, from measurement (calibration/probe_dist.py plays a sine through every mode and drive;
# calibrate_dist.py finds the engine drive whose 2nd and 3rd harmonics match). The modes differ in kind as well as in scale: the
# clippers (SoftClip, HardClip, Overdrive) are clean until the drive passes a threshold, the default and the asymmetric/diode modes
# distort from the start. An unwritten mode is the default mode; an unwritten drive is 25 (its harmonics match the drive-25 curve).
# Downsample is a sample-rate reducer, a different effect, and is not carried.
DIST_SCALES_FILE = Path(__file__).resolve().parent / "calibration/dist_scales.json"
DIST_DEFAULT_DRIVE = 25.0
# The calibrated law (the engine's dist_cal; calibration/probe_dist_transfer.py, probe_dist_wet.py, calibrate_dist2.py). Serum's curve is measured at its
# output with the probe preset's default master; the engine stage sits before its own master gain, which at that default master is 10^(master_default_db/20),
# so a measured pre-gain G becomes G x that and an output gain K becomes K / that. Wet is a smoothstep s with the dry part (1 - s^2); LevelOut (unwritten 0.5)
# is a gain on the sum of (LevelOut / 0.5)^2.
DIST_CURVES_FILE = Path(__file__).resolve().parent / "calibration/dist_curves.json"
DIST_PROBE_GAIN = 10 ** (-2.4186 / 20)
DIST_LEVELOUT_DEFAULT = 0.5


def smoothstep(w: float) -> float:
    w = min(max(w, 0.0), 1.0)
    return w * w * (3.0 - 2.0 * w)


def dist_params(nodes: list[dict]) -> tuple[dict, list[str]]:
    """The first Distortion on the master signal as the engine's distortion stage: mode, drive and wet mix."""
    scales = json.loads(DIST_SCALES_FILE.read_text(encoding="utf-8"))
    inside = branch_nodes(nodes)
    # The engine has one distortion stage: the master Distortion that does the most, wet part times drive (an unwritten drive is 25; one at 0 is only its own filter). The first
    # in the rack used to be taken, whatever it did (Bass - SWEDISHMF has a 19 % TapeSat ahead of a 100 % wet drive-72 one; Bass - The Selected a drive-0 filter ahead of a
    # soft clip at 75); a first node that is off ended the search.
    cands = []
    for i, n in enumerate(nodes):
        if kind_of(n) != "Distortion" or not enabled(n) or i in inside:
            continue
        pp = plain(n)
        wet = float(pp.get("kParamWet", 100.0))
        if wet < 1.0 or str(pp.get("kParamMode", "default")) == "kDownsample":
            continue
        cands.append((wet / 100.0 * float(pp.get("kParamDrive", DIST_DEFAULT_DRIVE)) / 100.0, -i, n))
    if not cands:
        down = [n for i, n in enumerate(nodes) if kind_of(n) == "Distortion" and enabled(n) and i not in inside and str(plain(n).get("kParamMode")) == "kDownsample" and plain(n).get("kParamWet", 100.0) >= 1.0]
        return {}, (["Distortion mode Downsample"] if down else [])
    for _, _, n in [max(cands, key=lambda c: (c[0], c[1]))]:
        pp = plain(n)
        mode = str(pp.get("kParamMode", "default"))
        wet = float(pp.get("kParamWet", 100.0))
        drive = float(pp.get("kParamDrive", DIST_DEFAULT_DRIVE))
        curves = json.loads(DIST_CURVES_FILE.read_text(encoding="utf-8"))
        cv = curves.get(mode) or curves["default"]
        if len(cv["drives"]) >= 2:
            lg = float(np.interp(drive, cv["drives"], np.log(cv["G"])))
            lk = float(np.interp(drive, cv["drives"], np.log(cv["K"])))
            g_db = 20.0 * math.log10(math.exp(lg) * DIST_PROBE_GAIN)
            k_db = 20.0 * math.log10(math.exp(lk) / DIST_PROBE_GAIN)
            lo = float(pp.get("kParamLevelOut", DIST_LEVELOUT_DEFAULT))
            lv_db = 40.0 * math.log10(max(lo, 1e-3) / DIST_LEVELOUT_DEFAULT)
            P = {"dist_cal": 1, "dist_mode": int(cv["engine_mode"]),
                 "dist_drive": int(round(min(max((g_db + 16.0) * 127.0 / 70.0, 0), 127))),
                 "dist_out": int(round(min(max((k_db + 36.0) * 127.0 / 48.0, 0), 127))),
                 "dist_level": int(round(min(max(64.0 + lv_db * 64.0 / 24.0, 0), 127))),
                 "dist_mix": int(round(min(max(127.0 * smoothstep(wet / 100.0), 0), 127)))}
            return P, [f"Distortion carried: Serum {mode} drive {drive:.0f} %, wet {wet:.0f} %, level out {lo:.2f} -> engine {('soft', 'asym', 'hard', 'sine')[P['dist_mode']]} "
                       f"pre-gain {g_db:+.1f} dB, output {k_db:+.1f} dB, level {lv_db:+.1f} dB"]
        sc = scales.get(mode) or scales["default"]
        pot = float(np.interp(drive, sc["drives"], sc["pots"]))
        P = {"dist_drive": int(round(pot)), "dist_mode": int(sc["engine_mode"]), "dist_mix": int(round(min(max(127.0 * wet / 100.0, 0), 127)))}
        return P, [f"Distortion carried: Serum {mode} drive {drive:.0f} %, wet {wet:.0f} % -> engine {('soft', 'asym', 'hard')[sc['engine_mode']]} drive {P['dist_drive']}"]
    return {}, []


# Serum's compressor, measured with a saw through it (calibration/probe_comp2.py, probe_comp3.py): the static curve of every setting fitted
# with the engine's own law (12 dB soft knee, on the peak level), the attack and release from the gain-reduction trajectory. Omitted
# parameters are Serum's defaults: threshold 0.5, ratio 4, attack 90, release 90, makeup 1 (unity, a linear factor), wet 100.
COMP_THRESH_DB = [(0.0, -1.0), (0.3, -3.0), (0.4, -7.0), (0.5, -12.0), (0.6, -17.0), (0.7, -24.0), (0.8, -34.0), (0.85, -41.5), (0.9, -52.0), (0.95, -70.0), (1.0, -90.0)]
COMP_THRESH_LIMIT_DB = [(0.0, -8.0), (0.4, -14.5), (0.5, -19.0), (0.6, -24.5), (0.7, -32.0), (0.8, -43.0), (0.9, -58.0), (1.0, -80.0)]   # at an infinite ratio
COMP_ATTACK_K, COMP_RELEASE_K = 0.75, 0.45                # fitted on the gain-reduction trajectory (calibration/fit_comp_time.py)
COMP_ATTACK_LAG_MS = 15.0                                     # the engine's 20 ms detector adds its own lag                    # Serum's Attack / Release setting (ms) -> the engine's time constant


def comp_params(nodes: list[dict]) -> tuple[dict, list[str]]:
    """The first single-band Comp on the master signal as the engine's compressor stage."""
    inside = branch_nodes(nodes)
    for i, n in enumerate(nodes):
        if kind_of(n) != "Comp" or not enabled(n) or i in inside:
            continue
        pp = plain(n)
        multi = float(pp.get("kParamMultiband", 0.0)) >= 0.5
        wet = float(pp.get("kParamWet", 100.0))
        if wet < 1.0:
            return {}, []
        thresh = float(pp.get("kParamThresh", 0.5))
        ratio = float(pp.get("kParamRatio", 4.0))
        att = float(pp.get("kParamAttack", 90.0))
        rel = float(pp.get("kParamRelease", 90.0))
        mk = float(pp.get("kParamMakeup", 1.0))            # a linear factor (1 is unity), measured: 20 log10(makeup) dB
        limiter = ratio >= 100.0                             # an infinite ratio is a limiter: lower plateau, narrow knee
        table = COMP_THRESH_LIMIT_DB if limiter else COMP_THRESH_DB
        t_db = float(np.interp(thresh, [x for x, _ in table], [y for _, y in table]))
        r_eng = 100.0 if limiter else max(ratio, 1.0)
        knee_db = 6.0 if limiter else min(max(0.9 * abs(t_db), 6.0), 40.0)       # the knee widens with the threshold's depth
        att_ms, rel_ms = max(att * COMP_ATTACK_K - COMP_ATTACK_LAG_MS, 2.0), max(rel * COMP_RELEASE_K, 10.0)
        if multi:
            # Three bands (crossovers 120 Hz and 2.4 kHz). Measured with a sine in the mid band (probe_mbcomp*.py): ThreshUD is the band's downward
            # threshold, about -21 - 0.24 (143 - UD) dB (the default is 100); Ratio Below is the slope of the upward compression under a floor
            # 11 dB lower (0 = none); Gain0..2 are the bands' gains in dB. Omitted: UD 100, gains 0, Below 0, ratio 4.
            ud = [float(pp.get(f"kParamThreshUD{b}", 100.0)) for b in range(3)]
            t_band = [-21.0 - 0.24 * (143.0 - u) for u in ud]
            gains = [float(pp.get(f"kParamGain{b}", 0.0)) for b in range(3)]
            below = min(max(float(pp.get("kParamRatioBelow", 0.0)), 0.0), 1.0)
            knee_db = 2.0                                      # the band curves have a hard knee (fitted)
            gp = lambda g: int(round(min(max(64.0 + (g / 24.0 * 63.0 if g >= 0 else g / 24.0 * 64.0), 0.0), 127.0)))
            P = {"comp_on": 1, "comp_multi": 1,
                 "comp_thresh_out_db": t_band[0], "comp_thresh2_out_db": t_band[1], "comp_thresh3_out_db": t_band[2],
                 "comp_gain1": gp(gains[0]), "comp_gain2": gp(gains[1]), "comp_gain3": gp(gains[2]), "comp_below": int(round(below * 127)),
                 "comp_ratio": int(round(min(max(math.log(max(ratio, 1.0)) / math.log(100.0), 0.0), 1.0) * 127)),
                 "comp_attack": int(round(min(max(math.log10(max(att * COMP_ATTACK_K - COMP_ATTACK_LAG_MS, 2.0) / 0.1) / 3.0, 0.0), 1.0) * 127)),
                 "comp_release": int(round(min(max(math.log(max(rel * COMP_RELEASE_K, 10.0) / 10.0) / math.log(300.0), 0.0), 1.0) * 127)),
                 "comp_makeup": int(round(min(max(20.0 * math.log10(max(mk, 1.0)) / 24.0, 0.0), 1.0) * 127)),
                 "comp_mix": int(round(min(max(wet / 100.0, 0.0), 1.0) * 127)),
                 "comp_knee": int(round(min(max(knee_db / 48.0, 0.0), 1.0) * 127))}
            return P, [f"Comp carried (multiband): thresholds UD {ud[0]:.0f}/{ud[1]:.0f}/{ud[2]:.0f}, gains {gains[0]:.1f}/{gains[1]:.1f}/{gains[2]:.1f} dB, below {below:.2f}"]
        P = {"comp_on": 1,
             "comp_thresh_out_db": t_db,                  # a level at Serum's output: the converter turns it into the stage's input level (pre master gain)
             "comp_ratio": int(round(min(max(math.log(r_eng) / math.log(100.0), 0.0), 1.0) * 127)),
             "comp_attack": int(round(min(max(math.log10(att_ms / 0.1) / 3.0, 0.0), 1.0) * 127)),
             "comp_release": int(round(min(max(math.log(rel_ms / 10.0) / math.log(300.0), 0.0), 1.0) * 127)),
             "comp_makeup": int(round(min(max(20.0 * math.log10(max(mk, 1.0)) / 24.0, 0.0), 1.0) * 127)),
             "comp_mix": int(round(min(max(wet / 100.0, 0.0), 1.0) * 127)),
             "comp_knee": int(round(min(max(knee_db / 48.0, 0.0), 1.0) * 127))}
        return P, [f"Comp carried: threshold {thresh:.2f}, ratio {ratio:g}, attack {att:g}, release {rel:g}, makeup x{mk:g}"]
    return {}, []


def utils_stereo(nodes: list[dict], ctx: dict | None) -> tuple[float, float | None, list[str]]:
    """Serum's Utils effect on the master signal as (width factor, mono-below Hz or None, why not). Width is the
    product of every Utils width (100 % = 1); Low Freq Mono is the highest crossover set. Anything moved by an
    envelope, LFO or velocity is dynamic and left out; a macro only moves a value to the saved position, and
    Utils widths here are stored values."""
    width, mono, why = 1.0, None, []
    inside = branch_nodes(nodes)
    mods = [m for m in (ctx or {}).get("mods", []) if m.get("dest_type") == "FXUtils"]
    dynamic = any(not (25 <= m["source"] <= 32) for m in mods)
    for i, n in enumerate(nodes):
        if kind_of(n) != "Utils" or not enabled(n):
            continue
        if i in inside:
            why.append("Utils in a Split branch")
            continue
        if dynamic:
            why.append("Utils moved by an envelope/LFO/velocity")
            continue
        pp = plain(n)
        width *= float(pp.get("kParamWidth", 100.0)) / 100.0
        if float(pp.get("kParamLFMono", 0.0)) >= 0.5:
            hz = float(pp.get("kParamLFXover", 150.0))
            mono = hz if mono is None else max(mono, hz)
    return width, mono, why


def curve(bands: list[dict]) -> np.ndarray:
    y = np.zeros_like(FGRID)
    for b in bands:
        y += biquad_db(b["kind"], b["fc"], b["gain"], b["q"], FGRID)
    return y


def fit_one(target: np.ndarray, kind: str, centre: float, spread: float = 1.2) -> tuple[dict, float]:
    """The single band of this kind that is closest (rms dB) to a target curve."""
    best = (1e9, None)
    for fc in centre * 2 ** np.linspace(-spread, spread, 25):
        for q in np.geomspace(0.1 if kind in ("bell", "hp", "lp") else 0.4, 14.0 if kind == "bell" else 4.0, 14):
            if kind in ("hp", "lp"):
                trial = biquad_db(kind, fc, 0.0, q, FGRID)
                gain = 0.0
            else:
                shape = biquad_db(kind, fc, 6.0, q, FGRID) / 6.0
                gain = float(np.clip((shape @ target) / max(shape @ shape, 1e-9), -24, 24))
                trial = biquad_db(kind, fc, gain, q, FGRID)
            err = float(np.sqrt(np.mean((trial - target) ** 2)))
            if err < best[0]:
                best = (err, {"kind": kind, "fc": float(fc), "gain": gain, "q": float(q)})
    return best[1], best[0]


def reduce_bands(bands: list[dict], slots: int = EQ_SLOTS) -> tuple[list[dict], float]:
    """At most `slots` bands whose summed response is as close to the original as it gets.
    Cuts are kept first, since nothing else can stand in for one; shaped bands are merged
    pairwise, cheapest merge first. Returns the bands and the rms error (dB) over the grid."""
    target = curve(bands)
    cuts = sorted([b for b in bands if b["kind"] in ("hp", "lp")], key=lambda b: -b["fc"] if b["kind"] == "hp" else b["fc"])
    keep_cuts = []
    for k, most in (("hp", 2), ("lp", 2)):                                # two cuts in a row are a steeper one (the ladder Filter FX)
        keep_cuts += [b for b in cuts if b["kind"] == k][:most]
    shaped = [b for b in bands if b["kind"] not in ("hp", "lp")]
    room = slots - len(keep_cuts)
    while len(shaped) > room and len(shaped) > 1:
        best = (1e9, None, None)
        for i in range(len(shaped)):
            for j in range(i + 1, len(shaped)):
                pair = curve([shaped[i], shaped[j]])
                kind = shaped[i]["kind"] if shaped[i]["kind"] == shaped[j]["kind"] else \
                    next((k for k in (shaped[i]["kind"], shaped[j]["kind"]) if k != "bell"), "bell")
                centre = math.sqrt(shaped[i]["fc"] * shaped[j]["fc"])
                merged, err = fit_one(pair, kind, centre)
                # cost: how far the merged band is from the pair it replaces
                if err < best[0]:
                    best = (err, (i, j), merged)
        _, (i, j), merged = best
        shaped = [b for n, b in enumerate(shaped) if n not in (i, j)] + [merged]
    if len(shaped) > room:                           # only when room is 0
        shaped = shaped[:max(room, 0)]
    out = keep_cuts + shaped
    # Below -40 dB nothing is audible, and two cascaded low-passes reach -80: floor both curves there
    err = float(np.sqrt(np.mean((np.maximum(curve(out), -40) - np.maximum(target, -40)) ** 2))) if (bands or out) else 0.0
    return out, err


def pot_freq(f: float) -> int:
    return int(round(min(max(127 * math.log(max(f, 20.0) / 20.0) / math.log(1000.0), 0), 127)))


def pot_gain(db: float) -> int:
    return int(round(min(max(64 + db / 24.0 * 63.0, 0), 127)))


def map_eq(nodes: list[dict], ctx: dict | None = None) -> tuple[dict, list[str], dict]:
    P = {}
    for n in range(1, EQ_SLOTS + 1):
        P.update({f"eq{n}_type": EQ_OFF, f"eq{n}_freq": 64, f"eq{n}_gain": 64, f"eq{n}_q": 64})
    bands = eq_bands(nodes)
    fbands, fwhy = filter_bands(nodes, ctx)
    dbands, dwhy = dist_filter_bands(nodes)
    fbands, fwhy = fbands + dbands, fwhy + dwhy
    bands = bands + fbands
    notes, info = [], {"eq_bands_in": len(bands), "filter_fx_bands": len(fbands)}
    if fbands:
        notes.append("Filter FX carried as " + ", ".join(f"{b['kind']} {b['fc']:.0f} Hz" for b in fbands) + " in the EQ")
    if fwhy:
        notes.append("Filter FX not carried: " + "; ".join(sorted(set(fwhy))))
    inside = branch_nodes(nodes)
    branch_eqs = sum(1 for i in inside if kind_of(nodes[i]) == "EQ" and enabled(nodes[i]))
    if branch_eqs:
        notes.append(f"EQ: {branch_eqs} in a Split branch, which only shapes half the signal, not carried")
    if not bands:
        return P, notes, info
    out, err = reduce_bands(bands)
    info.update(eq_bands_out=len(out), eq_rms_db=round(err, 2))
    for n, b in enumerate(out, 1):
        P[f"eq{n}_type"] = KIND_ID[b["kind"]]
        P[f"eq{n}_freq"] = pot_freq(b["fc"])
        P[f"eq{n}_gain"] = pot_gain(b["gain"])
        P[f"eq{n}_q"] = int(round(min(max(reso_from_q(b["kind"], b["q"]) * 1.27, 0), 127)))
    if len(bands) > len(out):
        notes.append(f"EQ: {len(bands)} bands fitted to {len(out)} (rms {err:.1f} dB)")
    return P, notes, info


# ------------------------------------------------------------------------------ delay
def map_delay(nodes: list[dict], fp: dict | None) -> tuple[dict, list[str], dict]:
    P = {"dly_mix": 0, "dly_time": 94, "dly_timer": 94, "dly_fb": 50, "dly_lo": 0, "dly_hi": 127, "dly_mode": 0, "dly_sync": 0}
    ds = [n for n in nodes if kind_of(n) == "Delay" and enabled(n)]
    if not ds:
        return P, [], {}
    # the node the fingerprint's mix is mostly about: the one with the most wet
    node = max(ds, key=lambda n: plain(n).get("kParamWet", 0.0))
    pp = plain(node)
    d = DELAY_DEFAULT
    ms_l = delay_ms(pp, "L")
    ms_r = delay_ms(pp, "R")
    fb = pp.get("kParamFeedback", d["fb"]) / 100.0
    f, bw = pp.get("kParamFreq", d["freq"]), pp.get("kParamBW", d["bw"])
    lo, hi = f / 2 ** (bw * DELAY_BW_SCALE / 2), f * 2 ** (bw * DELAY_BW_SCALE / 2)
    pot_ms = lambda ms: int(round(min(max(127 * math.log(max(ms, 1.0)) / math.log(1500.0), 0), 127)))
    P.update(dly_time=pot_ms(ms_l), dly_timer=pot_ms(ms_r), dly_fb=int(round(min(fb, 0.97) * 127)),
             dly_lo=pot_freq(lo), dly_hi=pot_freq(min(hi, 20000.0)), dly_mode=int(round(pp.get("kParamMode", d["mode"]))))
    # The mix is the stored Wet (macros folded in) through the delay's own wet law, which the engine shares (a smoothstep, measured on Serum). It was
    # read off a burst through Serum at an assumed echo time before, and a time that missed the echo (tempo-synced, ping-pong) gave a ratio of 1.0.
    ratio = M.smooth(min(max(pp.get("kParamWet", 25.0), 0.0), 100.0) / 100.0)
    notes_w = []
    w = M.inv_smoothstep(ratio)
    P["dly_mix"] = int(round(w * 127))
    P["dly_sync"] = 1                       # Serum's Delay times are note steps (DELAY_STEPS, at 120 BPM here): the engine's follow the host tempo
    notes = list(notes_w)
    if len(ds) > 1:
        notes.append(f"Delay: {len(ds)} delays, the one with the most wet is carried")
    if ms_l > 1500 or ms_r > 1500:
        notes.append("Delay: a time over 1.5 s is clamped")
    return P, notes, {"delay_ms": [round(ms_l), round(ms_r)], "delay_wet": round(w, 3)}


# ------------------------------------------------------------------------------ chorus, flanger, phaser
# The engine's three modulated stages (fx.h, ModFx). Serum stores a value only when it differs from its default, and the defaults are not
# known for every control: the ones used here that no probe has measured are marked, and the report says when a preset leans on them.
MOD_DEFAULT = {"Chorus": {"Rate": 0.5, "Delay": 5.0, "Delay2": 5.2, "Depth": 80.0, "Feedback": 9.5, "Filt": 1000.0, "FiltMode": 0.0},
               "Flanger": {"Rate": 0.5, "Depth": 50.0, "Feedback": 0.0, "Width": 0.0},
               "Phaser": {"Rate": 0.5, "Depth": 50.0, "Depth2": 0.0, "Freq": 300.0, "Feedback": 0.0, "Width": 0.0, "NumPoles": 4.0}}
MOD_MEASURED = {"Chorus": ("Delay", "Delay2", "Filt"), "Flanger": (), "Phaser": ()}      # defaults taken from a probe (5.0 and about 5.2 ms; Filt about 1 kHz, a 12 dB/oct low-pass)


def _rate_pot(hz: float) -> int:             # engine: 20 Hz * x^3
    return int(round(min(max((max(hz, 0.0) / 20.0) ** (1.0 / 3.0), 0.0), 1.0) * 127))


def _hz_pot(hz: float) -> int:               # engine: 20 Hz .. 20 kHz, logarithmic
    return int(round(min(max(math.log(max(hz, 20.0) / 20.0) / math.log(1000.0), 0.0), 1.0) * 127))


def modfx_params(nodes: list[dict]) -> tuple[dict, list[str]]:
    """The master-rack Chorus, Flanger and Phaser as the engine's three stages (the loudest of each kind), already folded for macros."""
    P, notes = {}, []
    inside = branch_nodes(nodes)
    for kind, pre in (("Chorus", "chor"), ("Flanger", "flan"), ("Phaser", "phas")):
        cands = [(plain(n).get("kParamWet", 100.0), i) for i, n in enumerate(nodes) if kind_of(n) == kind and enabled(n) and i not in inside]
        cands = [c for c in cands if c[0] > 0.0]
        if not cands:
            continue
        wet, i = max(cands)
        pp = plain(nodes[i])
        d = MOD_DEFAULT[kind]
        g = lambda k: float(pp.get(f"kParam{k}", d.get(k, 0.0)))
        used_default = [k for k in d if f"kParam{k}" not in pp and k not in MOD_MEASURED[kind] and k in ("Rate", "Depth", "Freq", "NumPoles")]
        P[f"{pre}_on"] = 1
        P[f"{pre}_mix"] = int(round(min(wet, 100.0) / 100.0 * 127))
        P[f"{pre}_rate"] = _rate_pot(g("Rate"))
        P[f"{pre}_depth"] = int(round(min(g("Depth"), 100.0) / 100.0 * 127))
        if kind == "Chorus":
            P["chor_d1"] = int(round(min(g("Delay"), 30.0) / 30.0 * 127))
            P["chor_d2"] = int(round(min(g("Delay2"), 30.0) / 30.0 * 127))
            P["chor_fb"] = int(round(min(g("Feedback") / 100.0 / 0.8, 1.0) * 127))
            P["chor_filt"] = _hz_pot(g("Filt"))
            P["chor_fmode"] = int(round(g("FiltMode")) >= 1)
        elif kind == "Flanger":
            P["flan_fb"] = int(round(min(g("Feedback") / 100.0 / 0.8, 1.0) * 127))
            P["flan_phase"] = int(round(min(g("Width"), 100.0) / 100.0 * 127))
        else:
            d2 = g("Depth2")
            P["phas_depth2"] = int(round(min(d2 if d2 <= 1.0 else d2 / 100.0, 1.0) * 127))
            P["phas_freq"] = _hz_pot(g("Freq"))
            P["phas_fb"] = int(round(min(g("Feedback") / 100.0 / 0.8, 1.0) * 127))
            P["phas_phase"] = int(round(min(g("Width"), 100.0) / 100.0 * 127))
            P["phas_poles"] = int(min(max(round(g("NumPoles")), 2), 12))
        notes.append(f"{kind} carried: wet {wet:.0f} %, rate {g('Rate'):.2f} Hz" + (f" (Serum default assumed for {', '.join(used_default)})" if used_default else ""))
        if len(cands) > 1:
            notes.append(f"{kind}: {len(cands)} in the rack, the one with the most wet is carried")
    return P, notes


# ------------------------------------------------------------------------------ closed loop
def noise_patch(extra: dict) -> str:
    """A blob that plays white noise straight to the FX block: one oscillator off, noise on,
    instant attack, full sustain, filter open."""
    base = {"wt1_level": 0, "noise_level": 23, "noise_type": 0, "vca_a": 0, "vca_s": 127, "vca_d": 64, "vca_r": 30,
            "volume": 100, "flt_freq": 127}
    base.update(extra)
    return "TBLR2;" + ";".join(f"{k}={v}" for k, v in base.items()) + ";"


def render_cli(blob: str, dur: float, hold: float, cli: Path = CLI):
    with tempfile.TemporaryDirectory() as td:
        bf, rf = Path(td, "b.txt"), Path(td, "o.raw")
        bf.write_text(blob, encoding="utf-8", newline="\n")
        r = subprocess.run([str(cli), str(ROOT / "out/move"), str(bf), str(rf), str(dur), str(hold), "100", "48", "1"],
                           capture_output=True, timeout=120)
        if r.returncode:
            raise RuntimeError(r.stderr.decode(errors="replace")[:120])
        raw = rf.read_bytes()
    x = np.array(struct.unpack(f"<{len(raw) // 4}f", raw), dtype=np.float64)
    return np.stack([x[0::2], x[1::2]])


def finite(x: float, ceiling: float = 60.0) -> float:
    """A measurement that failed (nan) reads as nothing; one that never decays reads as the ceiling."""
    return 0.0 if x != x else min(x, ceiling)


def bisect(f, lo: int, hi: int, target: float, rising: bool = True) -> int:
    """Integer in lo..hi whose f is closest to target, f monotonic (rising or falling)."""
    best = (abs(f(lo) - target), lo)
    a, b = lo, hi
    while b - a > 1:
        m = (a + b) // 2
        v = f(m)
        if abs(v - target) < best[0]:
            best = (abs(v - target), m)
        if (v < target) == rising:
            a = m
        else:
            b = m
    for x in (a, b):
        v = f(x)
        if abs(v - target) < best[0]:
            best = (abs(v - target), x)
    return best[1]


def map_reverb(fp: dict | None, cache: dict, cli: Path = CLI) -> tuple[dict, list[str], dict]:
    P = {"rev_mix": 0, "rev_decay": 70, "rev_damp": 110, "rev_lo": 0, "rev_width": 127}
    if not fp or "reverb" not in fp or fp["reverb"]["wet_ratio"] < 0.02:
        return P, [], {}
    r = fp["reverb"]
    colour = math.isfinite(r["centroid"]) and math.isfinite(r["side_mid"])         # a tail too quiet to read a colour from (fxmetrics.tail_window)
    if not colour:
        r = r | {"centroid": 0.0, "side_mid": 0.0}
    key = json.dumps([round(r["rt60"], 2), round(r["centroid"]), round(r["side_mid"], 1), round(r["wet_ratio"], 3), round(r["level100_db"], 1)])
    if key in cache:
        return P | cache[key]["params"], cache[key]["notes"], cache[key]["info"]
    notes = []

    def tail(decay, damp, width=127):
        b = noise_patch({"rev_mix": 127, "rev_decay": decay, "rev_damp": damp, "rev_width": width})
        return M.reverb_tail(render_cli(b, 5.0, 0.12, cli))

    rt_t = r["rt60"] if math.isfinite(r["rt60"]) and r["rt60"] > 0 else 30.0
    cen_t = min(max(r["centroid"], 250.0), 9000.0)
    if not colour:
        notes.append("Reverb: the tail is too quiet to read its colour from, left at the default")
    elif r["centroid"] < 250:
        notes.append(f"Reverb: the measured tail is nearly all bass (centroid {r['centroid']:.0f} Hz), matched as dark as it goes")
    decay, damp = 70, 110
    for _ in range(2):
        decay = bisect(lambda d: finite(tail(d, damp)["rt60"]), 0, 127, min(rt_t, 30.0))
        if colour:
            damp = bisect(lambda p: finite(tail(decay, p)["centroid"], 1e5), 20, 127, cen_t)
    got = tail(decay, damp)
    base_sm = got["side_mid"]
    width = 127
    if colour and r["side_mid"] < base_sm - 3:
        width = int(round(min(max(127 * 10 ** ((r["side_mid"] - base_sm) / 20), 0), 127)))
    # the level: the engine's wet-100 steady level at these settings against Serum's
    ref = render_cli(noise_patch({}), 3.0, 2.9, cli)
    ref_db = M.window_db(ref, 1.5, 3.0)
    st = render_cli(noise_patch({"rev_mix": 127, "rev_decay": decay, "rev_damp": damp, "rev_width": width}), 3.0, 2.9, cli)
    lev_e = M.window_db(st, 1.5, 3.0) - ref_db
    gap = r["level100_db"] - lev_e
    if abs(gap) > 12:                          # a level that far off is a measurement to doubt, not to chase
        notes.append(f"Reverb: Serum's level is {gap:+.0f} dB from the engine's, limited to 12 dB")
        gap = max(min(gap, 12.0), -12.0)
    g = 10 ** (gap / 20)
    wg = min(r["wet_ratio"] * g, 1.0)
    if r["wet_ratio"] * g > 1.0:
        notes.append(f"Reverb: Serum's is {20 * math.log10(r['wet_ratio'] * g):.1f} dB louder than the engine's maximum, so the tail is that much quieter")
    w = M.inv_smoothstep(wg)
    P.update(rev_mix=int(round(w * 127)), rev_decay=decay, rev_damp=damp, rev_width=width)
    info = {"reverb_rt60": [round(r["rt60"], 2), round(got["rt60"], 2)], "reverb_centroid": [round(r["centroid"]), round(got["centroid"]) if math.isfinite(got["centroid"]) else 0],
            "reverb_level_db": [round(r["level100_db"], 1), round(lev_e, 1)], "reverb_wet": round(w, 3)}
    cache[key] = {"params": {k: P[k] for k in ("rev_mix", "rev_decay", "rev_damp", "rev_width")}, "notes": notes, "info": info}
    return P, notes, info


def map_wide(fp: dict | None, cache: dict, cli: Path = CLI) -> tuple[dict, list[str], dict]:
    P = {"fx_wide": 0, "fx_wrate": 40, "fx_wdepth": 64}
    if not fp or "wide" not in fp or fp["wide"]["side_added_db"] < -30:
        return P, [], {}
    t = fp["wide"]["side_added_db"]
    key = f"wide:{round(t, 1)}"
    if key in cache:
        return P | cache[key]["params"], cache[key]["notes"], cache[key]["info"]
    dry = render_cli(noise_patch({}), 2.0, 1.9, cli)

    def added(v):
        return M.side_added_db(render_cli(noise_patch({"fx_wide": v}), 2.0, 1.9, cli), dry, 0.3, 1.8)

    top = added(127)
    notes = []
    if t > top:
        notes.append(f"Width: Serum's adds {t:.0f} dB side, the engine's maximum is {top:.0f}")
        v = 127
    else:
        v = bisect(added, 1, 127, t)
    P["fx_wide"] = v
    info = {"wide_side_added_db": [round(t, 1), round(added(v), 1)]}
    cache[key] = {"params": {"fx_wide": v}, "notes": notes, "info": info}
    return P, notes, info


# ------------------------------------------------------------------------------ top level
_fingerprints: dict | None = None
_cache: dict | None = None


def fingerprint_for(name: str) -> dict | None:
    global _fingerprints
    if _fingerprints is None:
        _fingerprints = {norm_name(k): v for k, v in json.loads(FINGERPRINTS.read_text(encoding="utf-8")).items()} if FINGERPRINTS.exists() else {}
    return _fingerprints.get(norm_name(name))


def hyperd_params(nodes: list[dict]) -> tuple[dict, list[str]]:
    """A HyperD in front of a hard-driven Distortion is the engine's Hyper stage (it runs ahead of the distortion, which then clips the moving voices together: the steady
    square-like tone the plain chain gives, 12 dB too bright and flat in level, becomes Serum's breathing one). Serum's own values carry across: voices, Detune, Rate and Wet
    in percent become the 0..127 pots. Only when the HyperD has real voices, detune and wet and the distortion behind it is driven hard (a HyperD that only widens the sound
    stays the Width stage's job); a value the file leaves at its default is not guessed at."""
    for i, n in enumerate(nodes):
        if kind_of(n) != "HyperD" or not enabled(n):
            continue
        pp = plain(n)
        uni, wet, det, rate = (pp.get(k) for k in ("kParamUnison", "kParamWet", "kParamDetune", "kParamRate"))
        if None in (uni, wet, det, rate) or uni < 1.5 or wet < 10.0 or det <= 0.0:
            continue
        hard = [m for m in nodes[i + 1:] if kind_of(m) == "Distortion" and enabled(m) and str(plain(m).get("kParamMode")) != "kDownsample"
                and plain(m).get("kParamWet", 100.0) >= 50.0 and plain(m).get("kParamDrive", DIST_DEFAULT_DRIVE) >= 50.0]
        if not hard:
            continue
        P = {"hyd_uni": int(min(round(uni), 8)), "hyd_det": int(round(min(det, 100.0) * 1.27)), "hyd_rate": int(round(min(rate, 100.0) * 1.27)), "hyd_wet": int(round(min(wet, 100.0) * 1.27))}
        return P, [f"HyperD ({uni:.0f} voices, detune {det:.0f} %, rate {rate:.0f} %, wet {wet:.0f} %) ahead of a hard distortion carried as the Hyper stage"]
    return {}, []


def fx_defaults() -> dict:
    P = {"fx_space": 100}
    P.update(map_eq([])[0])
    P.update({"dist_drive": 0, "dist_mode": 0, "dist_mix": 127, "dist_cal": 0, "dist_out": 64, "dist_level": 64, "fx_wide": 0, "fx_wrate": 40, "fx_wdepth": 64, "dly_mix": 0, "dly_sync": 0, "dly_time": 94, "dly_timer": 94, "dly_fb": 50,
              "dly_lo": 0, "dly_hi": 127, "dly_mode": 0, "rev_mix": 0, "rev_decay": 70, "rev_damp": 110, "rev_lo": 0, "rev_width": 127,
              "comp_on": 1, "comp_thresh": 127, "comp_ratio": 0, "comp_attack": 40, "comp_release": 60, "comp_makeup": 0, "comp_mix": 0, "comp_knee": 32,
              "comp_multi": 0, "comp_thresh2": 127, "comp_thresh3": 127, "comp_gain1": 64, "comp_gain2": 64, "comp_gain3": 64, "comp_below": 0,
              "chor_on": 0, "chor_mix": 0, "chor_rate": 37, "chor_d1": 21, "chor_d2": 22, "chor_depth": 64, "chor_fb": 0, "chor_filt": 127, "chor_fmode": 0,
              "flan_on": 0, "flan_mix": 0, "flan_rate": 37, "flan_depth": 64, "flan_fb": 0, "flan_phase": 0,
              "phas_on": 0, "phas_mix": 0, "phas_rate": 37, "phas_depth": 64, "phas_depth2": 0, "phas_freq": 50, "phas_fb": 0, "phas_phase": 0, "phas_poles": 4,
              "fxf_on": 0, "fxf_type": 0, "fxf_freq": 127, "fxf_res": 0, "fxf_env": 64,
              "hyd_uni": 1, "hyd_det": 0, "hyd_rate": 40, "hyd_wet": 0})
    return P


def map_fx(name: str, nodes: list[dict], cli: Path = CLI, ctx: dict | None = None) -> tuple[dict, list[str], dict]:
    """Everything the FX block needs for one preset: pot values, notes for the report, and the
    measurements behind them."""
    global _cache
    if _cache is None:
        _cache = json.loads(TABLOR_CACHE.read_text(encoding="utf-8")) if TABLOR_CACHE.exists() else {}
    nodes = fold_macro_wets(nodes, (ctx or {}).get("mods", []), (ctx or {}).get("macros", []))
    nodes = fold_macro_knobs(nodes, (ctx or {}).get("mods", []), (ctx or {}).get("macros", []))
    fp = fingerprint_for(name)
    # A fingerprint is keyed by preset name, so it must not outlive its effects: a rack with no
    # reverb (or width effect) gets none, whatever was measured for the name.
    inside = branch_nodes(nodes)
    kinds = {kind_of(x) for i, x in enumerate(nodes) if i not in inside}      # an effect in a Split branch works on one band, not the master signal
    stray = sorted({kind_of(nodes[i]) for i in inside if kind_of(nodes[i]) in ("Reverb", "Conv", "HyperD", "Chorus", "Flanger", "Phaser") and enabled(nodes[i])} - kinds)
    # (No rule on the stored Wet here: each effect kind is measured on its own, so one that does nothing measures as doing nothing, and Wet 0 does
    # not mean off for a flanger: Keys - Loop's, at Wet 0, still adds -15 dB of side.)
    if fp:
        fp = {k: v for k, v in fp.items()
              if k != "reverb" or kinds & {"Reverb", "Conv"}
              if k != "wide" or kinds & {"HyperD"}}
    P, notes, info = fx_defaults(), [], {}
    if stray:
        notes.append(f"{', '.join(stray)} in a Split branch (one frequency band only) not carried")
    p, n, i = map_eq(nodes, ctx)
    P.update(p); notes += n; info.update(i)
    p, n, i = map_delay(nodes, fp)
    P.update(p); notes += n; info.update(i)
    if fp is None and any(kind_of(x) in ("Reverb", "Conv", "Delay", "HyperD") for x in nodes):
        notes.append("Reverb, delay and width not carried: no Serum fingerprint for this preset (calibration/fx_fingerprint.py)")
    elif not cli.exists():
        if fp and ("reverb" in fp or "wide" in fp):
            notes.append("Reverb and width not carried: calibration/tablor_cli.exe is not built")
    else:
        p, n, i = map_reverb(fp, _cache, cli)
        P.update(p); notes += n; info.update(i)
        p, n, i = map_wide(fp, _cache, cli)
        P.update(p); notes += n; info.update(i)
        TABLOR_CACHE.write_text(json.dumps(_cache, indent=1), encoding="utf-8")
    mp, mn = modfx_params(nodes)
    P.update(mp); notes += mn
    dp, dn = dist_params(nodes)
    P.update(dp); notes += [x for x in dn if x.startswith("Distortion carried")]
    if any(not x.startswith("Distortion carried") for x in dn):
        notes.append("Distortion not carried: " + "; ".join(x for x in dn if not x.startswith("Distortion carried")))
    hy, hn = hyperd_params(nodes)
    P.update(hy); notes += hn
    cp, cn = comp_params(nodes)
    P.update(cp); notes += [x for x in cn if x.startswith("Comp carried")]
    if any(not x.startswith("Comp carried") for x in cn):
        notes.append("Comp not carried: " + "; ".join(x for x in cn if not x.startswith("Comp carried")))
    width, mono, uwhy = utils_stereo(nodes, ctx)
    P["mwidth"] = int(round(min(max(64.0 * width, 0.0), 127.0)))
    P["monohz"] = 0 if mono is None else int(round(min(max(1.0 + 126.0 * math.log(max(mono, 40.0) / 40.0) / math.log(12.5), 1.0), 127.0)))
    if width != 1.0 or mono is not None:
        notes.append(f"Utils carried as master width {width * 100:.0f} %" + (f" and mono below {mono:.0f} Hz" if mono is not None else ""))
    if uwhy:
        notes.append("Utils not carried: " + "; ".join(sorted(set(uwhy))))
    carried_utils = (width != 1.0 or mono is not None) and not uwhy
    # A Distortion / Comp / Bode / Filter / Utils / Split node with a stored Wet under 1 % and no envelope / LFO / velocity route to its Wet passes the signal untouched
    # (macro routes are already folded into the Wet above), so it has nothing to carry. Modulation effects are not covered: a flanger at Wet 0 still adds side.
    wet_mod = {m.get("dest_type") for m in (ctx or {}).get("mods", []) if m.get("dest_param") == "kParamWet" and not 25 <= m.get("source", 0) <= 32}
    dry = lambda x: kind_of(x) in DRY_KINDS and float(plain(x).get("kParamWet", 100.0)) < 1.0 and "FX" + kind_of(x) not in wet_mod
    ignored = sorted({kind_of(x) for x in nodes if kind_of(x) in ("Distortion", "Comp", "Bode", "Split", "SplitMS", "Utils") and enabled(x) and not dry(x)
                      and not (kind_of(x) == "Utils" and carried_utils) and not (kind_of(x) == "Distortion" and dp) and not (kind_of(x) == "Comp" and cp)})
    if any(kind_of(x) == "Filter" and enabled(x) and not dry(x) for x in nodes) and not info.get("filter_fx_bands") and dynamic_filter(nodes, ctx) is None:
        ignored = sorted(set(ignored) | {"Filter"})
    if ignored:
        notes.append("not carried: " + ", ".join(ignored))
    return P, notes, info
