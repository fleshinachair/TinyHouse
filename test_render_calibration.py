#!/usr/bin/env python3
"""Measure Tablor against Serum 2 and fit the mapper's scaling laws.

    python test_render_calibration.py            measure, fit, choose, validate
    python test_render_calibration.py --apply    also write the chosen table into deephouse2move.py
    python test_render_calibration.py --stage choose     one stage: fit, choose, validate or fx

The plugin goes silent after 1200 s of rendered audio per plugin instance (measured:
render 599 of 2 s each). So each stage runs in its own process, Serum's renders of whole
presets are cached under calibration/refs/ rather than repeated, and a silent render stops
the run instead of being scored. Delete that folder if the presets or Serum change.

Every constant is fitted on its own isolated probe: a minimal Serum patch with one
thing changed, rendered by Serum 2 through DawDreamer and by the Tablor engine through
the real converter (deephouse2move.physical -> to_vital -> map_to_tablor). Fitting on
whole presets instead would just learn those presets.

Filter variants are then chosen on an acceptance set, the canonical patches plus held-out
presets. The validation presets decide nothing: they are scored once, as shipped, clip
guard included, against the saved scores of earlier versions. Preset FX are stripped from
the Serum reference because Tablor is a dry engine and its reverb and delay live in other
Schwung slots.

Needs: dawdreamer, serum2-preset-loader, numpy, plus calibration/tablor_cli.exe.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import deephouse2move as d2m                                     # noqa: E402

import dawdreamer as daw                                         # noqa: E402
from serum2_preset_loader import convert_preset_tree             # noqa: E402
from serum2_preset_loader.codec import decode_preset_file        # noqa: E402

SR = 44100
from serum_paths import SERUM_VST, SERUM_TABLES            # noqa: E402  (the SERUM_ROOT / SERUM_VST environment variables override the defaults)
PRESET_DIR = ROOT / "out/_extracted/Presets/Packs/loopsy.store/Loopsy - DEEP HOUSE"
MOVE_ROOT = ROOT / "out/move"
WT_OUT = MOVE_ROOT / "data/UserData/UserLibrary/Wavetables"
CLI = ROOT / "calibration/tablor_cli.exe"
OUT = ROOT / "calibration"

CANONICAL = {"donk bass": "Bass - A Classic", "poly chord stab": "Keys - Catching Flies"}
FM_CONTROL = "Pluck - all things break"
# Every preset scored must use only tables Serum can open, or its "reference" is Serum
# playing a missing wavetable. See usable(); pack tables are not in Serum's folder.
HELD_OUT = ["Bass - Gravity", "Bass - Rivo", "Keys - Vintage Culture", "Pad - MIND",
            "Pluck - Scorpios", "Lead - Ari", "Pad - Reiny"]
VALIDATION = ["Bass - AVAION", "Bass - Black Out", "Bass - BUNT", "Keys -  Zerb", "Keys - Can't Find You",
              "Keys - Hans Deeper", "Lead - Can't Find You", "Lead - Consoul Training", "Pad - Aquatiq",
              "Pad - BICEP", "Pluck - HotLap", "Pluck - NOTSOBAD"]

LOG_PATH = OUT / "calibration.log"


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(s + "\n")


# ------------------------------------------------------------------ renderers
class Serum:
    def __init__(self):
        self.engine = daw.RenderEngine(SR, 128)
        self.synth = self.engine.make_plugin_processor("serum", SERUM_VST)
        self.audio_seconds = 0.0

    def render(self, tree, notes, dur, hold, vel=100):
        fd, path = tempfile.mkstemp(suffix=".bin")
        with os.fdopen(fd, "wb") as f:
            f.write(convert_preset_tree(tree))
        self.synth.load_state(path)
        os.unlink(path)
        self.synth.clear_midi()
        for n in notes:
            self.synth.add_midi_note(n, vel, 0.0, hold)
        self.engine.load_graph([(self.synth, [])])
        self.engine.render(dur)
        self.audio_seconds += dur
        a = np.array(self.engine.get_audio(), dtype=np.float64)
        if np.abs(a).max() < 1e-7:
            raise RuntimeError(f"Serum rendered silence after {self.audio_seconds:.0f} s of audio: "
                               "the plugin's render limit, or a patch that makes no sound. Not scoring against it.")
        return a


_seed = 1


def tablor_render(tree, notes, dur, hold, vel=100, header=None):
    """Through the REAL converter, so a fitted constant is a constant that ships."""
    global _seed
    header = header or {"presetName": "probe", "tags": []}
    m = d2m.map_to_tablor(d2m.to_vital(d2m.physical(tree, header)), {}, SERUM_TABLES, WT_OUT, "Deep House")
    return run_cli(d2m.blob(m), notes, dur, hold, vel), m


def run_cli(blob, notes, dur, hold, vel=100):
    global _seed
    _seed += 1
    with tempfile.TemporaryDirectory() as td:
        bf, rf = Path(td, "b.txt"), Path(td, "o.raw")
        bf.write_text(blob, encoding="utf-8", newline="\n")
        r = subprocess.run([str(CLI), str(MOVE_ROOT), str(bf), str(rf), str(dur), str(hold), str(vel),
                            ",".join(map(str, notes)), str(_seed)], capture_output=True, text=True, timeout=120)
        if r.returncode:
            raise RuntimeError(f"tablor_cli {r.returncode}: {r.stderr}")
        x = np.fromfile(rf, dtype=np.float32).astype(np.float64)
    return np.stack([x[0::2], x[1::2]])


# ------------------------------------------------------------------ metrics
def db(x):
    return 20 * np.log10(np.maximum(x, 1e-9))


def rms_db(a):
    return float(db(np.sqrt(np.mean(a ** 2))))


def peak_db(a):
    return float(db(np.abs(a).max()))


def spectrum(a, nfft=8192):
    mono = a.mean(axis=0) if a.ndim == 2 else a
    if len(mono) < nfft:
        nfft = max(256, 2 ** int(math.log2(max(len(mono), 2))))
    win = np.hanning(nfft)
    p = np.zeros(nfft // 2 + 1)
    n = 0
    for s in range(0, len(mono) - nfft + 1, nfft // 2):
        p += np.abs(np.fft.rfft(mono[s:s + nfft] * win)) ** 2
        n += 1
    return np.fft.rfftfreq(nfft, 1 / SR), p / max(n, 1)


def centroid(a):
    f, p = spectrum(a)
    return float((f * p).sum() / (p.sum() + 1e-30))


def rolloff(a, frac=0.85):
    f, p = spectrum(a)
    c = np.cumsum(p)
    return float(f[np.searchsorted(c, frac * c[-1])])


_BANDS = 20.0 * 2 ** (np.arange(0, 30) / 3)


def band_db(a):
    """Third-octave band levels, normalised to total energy, so a level error is
    measured by the level metric and not here too."""
    f, p = spectrum(a)
    p = p / (p.sum() + 1e-30)
    out = []
    for lo in _BANDS:
        s = p[(f >= lo) & (f < lo * 2 ** (1 / 3))]
        out.append(10 * np.log10(s.sum() + 1e-9) if s.size else np.nan)
    return np.array(out)


def spec_dist(a, b):
    """RMS difference between band curves, in dB, over bands at least one side uses."""
    x, y = band_db(a), band_db(b)
    keep = np.isfinite(x) & np.isfinite(y) & ((x > -60) | (y > -60))
    return float(np.sqrt(np.mean((x[keep] - y[keep]) ** 2))) if keep.any() else 0.0


def envelope_db(a, hop=220):
    """Level per 5 ms frame."""
    m = (a ** 2).mean(axis=0)
    n = len(m) // hop
    return db(np.sqrt(m[:n * hop].reshape(n, hop).mean(axis=1)))


def decay_time(a, drop=20.0, after=0.0):
    """Seconds from the loudest frame after `after` until the level stays `drop` dB below it."""
    e = envelope_db(a)
    first = int(after / (220 / SR))
    pk = first + int(np.argmax(e[first:]))
    below = np.where(e[pk:] > e[pk] - drop)[0]
    return float((below[-1] + 1) * 220 / SR) if below.size else 0.0


def attack_time(a, drop=3.0):
    e = envelope_db(a)
    return float(np.argmax(e >= e.max() - drop) * 220 / SR)


def correlation(a):
    l, r = a[0] - a[0].mean(), a[1] - a[1].mean()
    d = np.sqrt((l ** 2).sum() * (r ** 2).sum())
    return float((l * r).sum() / d) if d > 0 else 1.0


def side_mid_db(a):
    m, s = (a[0] + a[1]) / 2, (a[0] - a[1]) / 2
    return max(float(10 * np.log10(((s ** 2).sum() + 1e-12) / ((m ** 2).sum() + 1e-12))), -40.0)


def fundamental(a):
    f, p = spectrum(a, 32768)
    k = (f > 15) & (f < 2000)
    pk = p[k].max()
    return float(f[k][np.argmax(p[k] >= pk * 0.1)])


def spread_hz(a, centre):
    f, p = spectrum(a, 65536)
    k = np.abs(f - centre) < centre * 0.25
    m = (f[k] * p[k]).sum() / p[k].sum()
    return float(np.sqrt((((f[k] - m) ** 2) * p[k]).sum() / p[k].sum()))


def harmonics(a, f0, win=(11025, 33075)):
    """(frequency, level) of every harmonic of f0, from a long Hann FFT."""
    x = a.mean(axis=0)[win[0]:win[1]]
    n = len(x)
    sp = np.abs(np.fft.rfft(x * np.hanning(n))) / n * 4
    bin_hz = SR / n
    k = np.arange(1, int(16000 / f0))
    idx = np.round(k * f0 / bin_hz).astype(int)
    lv = np.array([sp[max(i - 2, 0):i + 3].max() for i in idx])
    return k * f0, np.maximum(lv, 1e-7)


def transfer(filtered, open_, f0):
    """What the filter did to each harmonic, in dB. Unlike a centroid this does not care
    that a low saw's energy sits in its fundamental."""
    f, y = harmonics(filtered, f0)
    _, o = harmonics(open_, f0)
    return f, np.maximum(20 * np.log10(y / o), -40.0)     # Tablor is 16-bit: below this is its noise floor


def corners(f, g, drops=(6.0, 12.0, 24.0)):
    """Frequency where the filter has fallen each drop below its passband."""
    pb = float(np.median(g[:2]))
    out = []
    for d in drops:
        below = np.where(g < pb - d)[0]
        if not below.size or below[0] == 0:
            out.append(float("nan"))
            continue
        i = below[0]
        x = (pb - d - g[i - 1]) / (g[i] - g[i - 1])
        out.append(float(f[i - 1] * (f[i] / f[i - 1]) ** x))
    return np.array(out)


def mean_of(render, n, fn):
    """Free-running phases make single renders noisy: 1.2 dB between identical Serum runs."""
    return float(np.mean([fn(render()) for _ in range(n)]))


# ------------------------------------------------------------------ probe patches
def load_preset(name):
    return decode_preset_file(PRESET_DIR / f"{name}.SerumPreset")


def strip_fx(tree):
    t = copy.deepcopy(tree)
    for k in ("FXRack0", "FXRack1", "FXRack2"):
        if k in t:
            t[k]["FX"] = []
    for i in range(64):
        s = t.get(f"ModSlot{i}", {})
        if str(s.get("destModuleTypeString", "")).startswith("FX"):
            t[f"ModSlot{i}"] = {"plainParams": "default"}
    return t


def init_tree(base, table="S2 Tables/Default Shapes.wav", frames=9):
    """One wavetable oscillator straight to the output, everything else off.
    Frame 0 of Default Shapes is a saw; frame 0 of Basic Shapes is a sine."""
    t = copy.deepcopy(base)
    for k in list(t):
        if k.startswith("ModSlot"):
            t[k] = {"plainParams": "default"}
        elif k.startswith("Macro"):
            t[k]["plainParams"] = "default"
        elif k.startswith("Env") or k.startswith("RoutingSlot"):
            t[k]["plainParams"] = "default"
    for k in ("FXRack0", "FXRack1", "FXRack2"):
        t[k]["FX"] = []
    t["Global0"]["plainParams"] = {"kParamMonoToggle": 1.0}
    t["VoiceFilter0"]["plainParams"] = {"kParamEnable": 0.0}
    t["VoiceFilter1"]["plainParams"] = "default"
    for i in (1, 2, 3, 4):
        t[f"Oscillator{i}"]["plainParams"] = {"kParamEnable": 0.0}
    o = t["Oscillator0"]
    o["plainParams"] = {"kParamVolume": 0.75}
    o["WTOsc0"]["plainParams"] = {}
    o["WTOsc0"]["relativePathToWT"] = table
    o["WTOsc0"]["numFrames"] = frames * 2048
    o["WTOsc0"]["numChannels"] = 1
    o["WTOsc0"]["sampleRate"] = 44100
    o["WTOsc0"]["flex"] = {}
    return t


def with_filter(t, cutoff, reso=0.0, model="MgL24"):
    t = copy.deepcopy(t)
    t["VoiceFilter0"]["plainParams"] = {"kParamEnable": 1.0, "kParamFreq": cutoff, "kParamReso": reso, "kParamType": model}
    return t


def set_env(t, idx=0, **kw):
    t = copy.deepcopy(t)
    p = {f"kParam{k.capitalize()}": v for k, v in kw.items()}
    t[f"Env{idx}"]["plainParams"] = p
    return t


def set_osc(t, **kw):
    t = copy.deepcopy(t)
    t["Oscillator0"]["plainParams"].update(kw)
    return t


# ------------------------------------------------------------------ fitters
class Fit:
    def __init__(self, serum, base, avg):
        self.s, self.base, self.avg = serum, base, avg
        self.report = {}

    def ref(self, tree, notes, dur, hold, fn):
        return mean_of(lambda: self.s.render(tree, notes, dur, hold), self.avg, fn)

    def grid(self, key, values, loss):
        best, best_l = None, float("inf")
        for v in values:
            d2m.CAL[key] = float(v)
            l = loss()
            if l < best_l:
                best, best_l = float(v), l
        d2m.CAL[key] = best
        return best, best_l

    def record(self, name, **kw):
        self.report[name] = kw
        log(f"  {name}: " + ", ".join(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in kw.items()))

    # --- level law: how Serum's oscillator level maps to amplitude
    def level(self):
        saw = self.base
        vs = [0.2, 0.4, 0.6, 0.75, 1.0]
        ys = [self.ref(set_osc(saw, kParamVolume=v), [48], 1.0, 0.9, lambda a: rms_db(a[:, 11025:33075])) for v in vs]
        slope = float(np.polyfit(20 * np.log10(vs), ys, 1)[0])
        tablor = [rms_db(tablor_render(set_osc(saw, kParamVolume=v), [48], 1.0, 0.9)[0][:, 11025:33075]) for v in vs]
        before = float(np.polyfit(20 * np.log10(vs), tablor, 1)[0])
        d2m.CAL["level_exp"] = max(slope / 2.0, 0.05)
        self.record("level", serum_gain_law=f"amp = level^{slope:.3f}", tablor_before=f"amp = level^{before:.3f}", level_exp=d2m.CAL["level_exp"], serum_dB=[round(y, 1) for y in ys])

    # --- master volume, and the overall gain between the two engines
    def master(self):
        saw = self.base
        vs = [0.1, 0.2, 0.28, 0.4, 0.6, 0.8, 1.0]
        def t(v):
            x = set_osc(saw)
            x["Global0"]["plainParams"]["kParamMasterVolume"] = v
            return x
        ys = [self.ref(t(v), [48], 1.0, 0.9, lambda a: rms_db(a[:, 11025:33075])) for v in vs]
        slope, icpt = np.polyfit(np.log10(vs), ys, 1)
        resid = float(np.max(np.abs(np.polyval([slope, icpt], np.log10(vs)) - ys)))
        default = self.ref(saw, [48], 1.0, 0.9, lambda a: rms_db(a[:, 11025:33075]))
        # what Tablor gives at known master gain
        d2m.CAL["master_db0"] = None
        tb, m = tablor_render(saw, [48], 1.0, 0.9)
        tb_rms = rms_db(tb[:, 11025:33075])
        vol = m["params"]["volume"]
        engine_db = 20 * math.log10((vol / 127) ** 2 * 0.8)
        unity = tb_rms - engine_db                     # Tablor's level at master gain 1.0
        d2m.CAL["master_db0"] = float(icpt - unity)
        d2m.CAL["master_slope"] = float(slope)
        d2m.CAL["master_default_db"] = float(default - unity)
        self.record("master", serum_dB=[round(y, 1) for y in ys], slope=float(slope), intercept=float(icpt), fit_worst_dB=resid,
                    serum_default_dB=default, tablor_unity_dB=unity)

    def unison_level(self):
        saw = self.base
        pts = [4, 8, 16]
        refs = {v: self.ref(set_osc(saw, kParamUnison=float(v), kParamDetune=0.25), [48], 1.0, 0.9, lambda a: rms_db(a[:, 11025:33075])) for v in [1] + pts}
        def loss():
            base = mean_of(lambda: tablor_render(set_osc(saw, kParamUnison=1.0, kParamDetune=0.25), [48], 1.0, 0.9)[0], 2, lambda a: rms_db(a[:, 11025:33075]))
            e = 0.0
            for v in pts:
                y = mean_of(lambda: tablor_render(set_osc(saw, kParamUnison=float(v), kParamDetune=0.25), [48], 1.0, 0.9)[0], 2, lambda a: rms_db(a[:, 11025:33075]))
                e += abs((y - base) - (refs[v] - refs[1]))
            return e / len(pts)
        d2m.CAL["unison_gain_exp"] = 0.0
        before = loss()
        best, l = self.grid("unison_gain_exp", np.arange(-0.2, 0.21, 0.04), loss)
        self.record("unison_level", serum_rel_dB={v: round(refs[v] - refs[1], 1) for v in pts}, before_dB=before, after_dB=l, unison_gain_exp=best)

    # --- filter drive: 42 of the 70 presets use it. Fitted after the cutoff, since what it
    # adds is judged through the filter.
    def drive(self):
        pts = [10.0, 20.0, 35.0, 50.0, 70.0, 100.0]
        def patch(d):
            x = with_filter(self.base, 0.5)
            x["VoiceFilter0"]["plainParams"]["kParamDrive"] = d
            return x
        win = lambda a: a[:, 11025:33075]
        refs = {d: self.s.render(patch(d), [36], 1.0, 0.9) for d in pts}
        lv = {d: rms_db(win(a)) for d, a in refs.items()}
        def errs():
            return {d: (rms_db(win(a)) - lv[d], spec_dist(win(a), win(refs[d])))
                    for d, a in ((d, tablor_render(patch(d), [36], 1.0, 0.9)[0]) for d in pts)}
        loss = lambda: float(np.mean([abs(l) + 0.5 * b for l, b in errs().values()]))
        d2m.CAL["drive_scale"], d2m.CAL["drive_exp"] = 0.0, 1.0
        before = errs()
        best, best_l = (0.0, 1.0), loss()
        for sc in (0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0):
            for ex in (0.5, 0.7, 1.0, 1.3, 1.6, 2.0):
                d2m.CAL["drive_scale"], d2m.CAL["drive_exp"] = sc, ex
                l = loss()
                if l < best_l:
                    best, best_l = (sc, ex), l
        d2m.CAL["drive_scale"], d2m.CAL["drive_exp"] = best
        after = errs()
        self.record("drive", serum_rms_dB={d: round(v, 1) for d, v in lv.items()},
                    level_err_dB_without={d: round(v[0], 1) for d, v in before.items()},
                    level_err_dB_with={d: round(v[0], 1) for d, v in after.items()},
                    band_err_dB_without={d: round(v[1], 1) for d, v in before.items()},
                    band_err_dB_with={d: round(v[1], 1) for d, v in after.items()},
                    drive_scale=best[0], drive_exp=best[1])

    # --- cutoff law, through a real MG Low 24, by where the response actually falls
    def cutoff(self):
        note, f0 = 24, 32.703
        saw = self.base
        pts = [0.45, 0.55, 0.65, 0.75]
        s_open = self.s.render(saw, [note], 1.0, 0.9)
        t_open = tablor_render(saw, [note], 1.0, 0.9)[0]
        ref = {c: corners(*transfer(self.s.render(with_filter(saw, c), [note], 1.0, 0.9), s_open, f0)) for c in pts}
        def errs():
            return {c: 12 * np.log2(corners(*transfer(tablor_render(with_filter(saw, c), [note], 1.0, 0.9)[0], t_open, f0)) / ref[c]) for c in pts}
        loss = lambda: float(np.nanmean([np.nanmean(np.abs(v)) for v in errs().values()]))
        show = lambda e: {c: [round(float(x), 1) for x in v] for c, v in e.items()}
        before = errs()
        for _ in range(2):
            self.grid("cutoff_offset", np.arange(-24, 6.1, 1.0), loss)
            self.grid("cutoff_scale", np.arange(0.85, 1.16, 0.01), loss)
        self.record("cutoff", serum_corners_6_12_24dB_Hz={c: [round(float(x)) for x in v] for c, v in ref.items()},
                    err_st_before=show(before), err_st_after=show(errs()), loss_st_before=float(np.nanmean([np.nanmean(np.abs(v)) for v in before.values()])),
                    loss_st_after=loss(), cutoff_offset=d2m.CAL["cutoff_offset"], cutoff_scale=d2m.CAL["cutoff_scale"])

    def resonance(self):
        note, f0 = 24, 32.703
        saw = self.base
        pts = [20.0, 40.0, 60.0, 80.0, 95.0]
        s_open = self.s.render(saw, [note], 1.0, 0.9)
        t_open = tablor_render(saw, [note], 1.0, 0.9)[0]
        peak = lambda f, g: float(g.max() - np.median(g[f < 100]))
        ref = {}
        for r in pts:
            f, g = transfer(self.s.render(with_filter(saw, 0.55, r), [note], 1.0, 0.9), s_open, f0)
            ref[r] = peak(f, g)
        def errs():
            out = {}
            for r in pts:
                f, g = transfer(tablor_render(with_filter(saw, 0.55, r), [note], 1.0, 0.9)[0], t_open, f0)
                out[r] = peak(f, g) - ref[r]
            return out
        loss = lambda: float(np.mean(np.abs(list(errs().values()))))
        before = errs()
        for _ in range(2):
            self.grid("res_exp", [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0, 1.25], loss)
            self.grid("res_scale", [0.7, 0.8, 0.85, 0.9, 0.95, 1.0], loss)
        self.record("resonance", serum_peak_dB={r: round(v, 1) for r, v in ref.items()},
                    peak_err_dB_before={r: round(v, 1) for r, v in before.items()},
                    peak_err_dB_after={r: round(v, 1) for r, v in errs().items()},
                    res_exp=d2m.CAL["res_exp"], res_scale=d2m.CAL["res_scale"])

    def keytrack(self):
        """A stored key track of 1.0 is full tracking. What has to be found is where it pivots:
        corners where harmonics are dense enough to locate them, loudness where they are not."""
        saw = self.base
        def patch(cutoff=0.55, model="MgL24"):
            x = with_filter(saw, cutoff, 0.0, model)
            x["VoiceFilter0"]["plainParams"]["kParamKeyTrack"] = 1.0
            return x
        win = lambda a: rms_db(a[:, 11025:33075])
        low = ((36, 65.406), (48, 130.81), (60, 261.63))
        opens = {n: self.s.render(saw, [n], 1.0, 0.9) for n, _ in low}
        topens = {n: tablor_render(saw, [n], 1.0, 0.9)[0] for n, _ in low}
        ref_c = {n: corners(*transfer(self.s.render(patch(), [n], 1.0, 0.9), opens[n], f0)) for n, f0 in low}
        ref_l = {n: mean_of(lambda: self.s.render(patch(0.285, "ZDF_A"), [n], 1.0, 0.9), 2, win) for n in (60, 72, 84)}
        def parts():
            e = [np.nanmean(np.abs(12 * np.log2(corners(*transfer(tablor_render(patch(), [n], 1.0, 0.9)[0], topens[n], f0)) / ref_c[n]))) for n, f0 in low]
            return float(np.mean(e)), float(np.mean([abs(win(tablor_render(patch(0.285, "ZDF_A"), [n], 1.0, 0.9)[0]) - ref_l[n]) for n in ref_l]))
        d2m.CAL["key_scale"] = 1.0
        best, best_v, best_parts = None, 1e9, None
        for pv in range(12, 61, 3):
            d2m.CAL["key_pivot"] = float(pv)
            c, l = parts()
            if c + l / 3 < best_v:
                best, best_v, best_parts = float(pv), c + l / 3, (c, l)
        d2m.CAL["key_pivot"] = best
        self.record("key_tracking", key_pivot=best, corner_err_st=best_parts[0], level_err_dB=best_parts[1])

    def envelope_times(self):
        saw = self.base
        def curve_table(stage, knob, values, seconds, render, measure):
            """For each curve value, the factor that makes Tablor's stage last as long as
            Serum's. Each table entry is fitted by its own bisection."""
            d2m.CAL[knob] = []
            scale = {"decay": "decay_scale", "release": "release_scale", "attack": "attack_scale"}[stage]
            table, worst = [], 0.0
            for c in values:
                tree = render(seconds, c)
                target = self.ref(tree, *measure[0], measure[1])
                lo, hi = 0.02, 12.0
                for _ in range(11):
                    mid = math.sqrt(lo * hi)
                    d2m.CAL[scale] = mid
                    y = measure[1](tablor_render(tree, *measure[0])[0])
                    if y < target:
                        lo = mid
                    else:
                        hi = mid
                table.append([c, round(math.sqrt(lo * hi), 4)])
                worst = max(worst, abs(math.log2(max(measure[1](tablor_render(tree, *measure[0])[0]), 1e-3) / max(target, 1e-3))))
            return table, worst

        # the default curve first: what a preset that stores none gets
        def fit_default(stage, tree, measure):
            target = self.ref(tree, *measure[0], measure[1])
            scale = stage + "_scale"
            lo, hi = 0.02, 12.0
            for _ in range(11):
                mid = math.sqrt(lo * hi)
                d2m.CAL[scale] = mid
                if measure[1](tablor_render(tree, *measure[0])[0]) < target:
                    lo = mid
                else:
                    hi = mid
            d2m.CAL[scale] = round(math.sqrt(lo * hi), 4)
            return d2m.CAL[scale]

        dec = (([48], 4.5, 4.4), decay_time)
        rel_fn = lambda a: decay_time(a[:, 22050:], 20.0)
        rel = (([48], 4.0, 0.5), rel_fn)
        atk = (([48], 2.5, 2.4), attack_time)

        def dec_tree(sec, c):
            x = set_env(saw, 0, attack=0.001, decay=sec, sustain=0.0)
            if c is not None:
                x["Env0"]["plainParams"]["kParamCurve2"] = float(c)
            return x
        def rel_tree(sec, c):
            x = set_env(saw, 0, attack=0.001, decay=0.1, sustain=1.0, release=sec)
            if c is not None:
                x["Env0"]["plainParams"]["kParamCurve3"] = float(c)
            return x
        def atk_tree(sec, c):
            x = set_env(saw, 0, attack=sec, decay=1.0, sustain=1.0)
            if c is not None:
                x["Env0"]["plainParams"]["kParamCurve1"] = float(c)
            return x

        ds = fit_default("decay", dec_tree(1.0, None), dec)
        rs = fit_default("release", rel_tree(1.0, None), rel)
        ats = fit_default("attack", atk_tree(0.3, None), atk)
        self.record("env_default_curve", decay_scale=ds, release_scale=rs, attack_scale=ats)

        keep = (d2m.CAL["decay_scale"], d2m.CAL["release_scale"], d2m.CAL["attack_scale"])
        vals = [30.0, 40.0, 50.0, 60.0, 67.0, 75.0, 85.0, 96.0]
        dt, dw = curve_table("decay", "decay_curve", vals, 1.0, dec_tree, dec)
        rt, rw = curve_table("release", "release_curve", vals, 1.0, rel_tree, rel)
        at, aw = curve_table("attack", "attack_curve", [30.0, 40.0, 50.0, 62.0, 75.0], 0.3, atk_tree, atk)
        d2m.CAL["decay_scale"], d2m.CAL["release_scale"], d2m.CAL["attack_scale"] = keep
        d2m.CAL["decay_curve"], d2m.CAL["release_curve"], d2m.CAL["attack_curve"] = dt, rt, at
        self.record("env_curves", decay=dt, release=rt, attack=at, worst_log2_err=max(dw, rw, aw))

        # does one factor per curve hold at other lengths? if not the table is a fiction
        held = {}
        for sec in (0.2, 3.0):
            for c in (50.0, 67.0, 85.0):
                tree = dec_tree(sec, c)
                held[(sec, c)] = round(math.log2(max(decay_time(tablor_render(tree, *dec[0])[0]), 1e-3) / max(self.ref(tree, *dec[0], dec[1]), 1e-3)), 2)
        self.record("decay_other_lengths_log2", result={str(k): v for k, v in held.items()})

        # sustain level
        pts = [0.25, 0.5, 0.75]
        cases = [(set_env(saw, 0, attack=0.001, decay=0.05, sustain=s), s) for s in pts]
        rel_level = lambda a: rms_db(a[:, 22050:33075]) - float(envelope_db(a).max())
        ref = {s: self.ref(t, [48], 1.0, 0.9, rel_level) for t, s in cases}
        def sloss():
            return float(np.mean([abs(rel_level(tablor_render(t, [48], 1.0, 0.9)[0]) - ref[s]) for t, s in cases]))
        before = sloss()
        best, l = self.grid("sustain_exp", np.arange(0.8, 3.21, 0.1), sloss)
        self.record("sustain", serum_rel_dB={s: round(v, 1) for s, v in ref.items()}, dB_err_before=before, dB_err_after=l, sustain_exp=best)

    def filter_envelope(self):
        saw = with_filter(self.base, 0.30)
        def patch(a):
            t = set_env(copy.deepcopy(saw), 1, attack=0.001, decay=0.4, sustain=0.0)
            t["ModSlot0"] = {"destModuleID": 0, "destModuleParamID": 3, "destModuleParamName": "kParamFreq",
                             "destModuleTypeString": "VoiceFilter", "plainParams": {"kParamAmount": a}, "source": [2, 0]}
            return t
        pts = [25.0, 50.0, 80.0]
        wins = [(441, 3528), (3528, 8820), (8820, 17640)]
        ref = {a: self.s.render(patch(a), [24], 1.2, 1.1) for a in pts}
        def loss():
            e = []
            for a in pts:
                x = tablor_render(patch(a), [24], 1.2, 1.1)[0]
                e += [spec_dist(x[:, w[0]:w[1]], ref[a][:, w[0]:w[1]]) for w in wins]
            return float(np.mean(e))
        before = loss()
        best, l = self.grid("flt_env_scale", np.arange(0.5, 1.51, 0.05), loss)
        self.record("filter_env", band_rmse_dB_before=before, band_rmse_dB_after=l, flt_env_scale=best)

    def detune(self):
        sine = init_tree(self.base_src, "Analog/Basic Shapes.wav", 7)
        fn = lambda a: spread_hz(a, 440.0)
        # With no DetuneWid stored, which is 130 of the 149 oscillators in this pack.
        pts = [0.03, 0.1, 0.25, 0.6]
        patch = lambda d: set_osc(sine, kParamUnison=7.0, kParamDetune=d)
        ref = {d: self.ref(patch(d), [69], 2.0, 1.9, fn) for d in pts}
        def loss():
            e = 0.0
            for d in pts:
                y = mean_of(lambda: tablor_render(patch(d), [69], 2.0, 1.9)[0], 2, fn)
                e += abs(math.log2(max(y, 0.1) / max(ref[d], 0.1)))
            return e / len(pts)
        before = loss()
        best, best_l = None, 1e9
        for sc in (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0):
            for ex in (0.8, 1.0, 1.2, 1.35, 1.5):
                d2m.CAL["detune_scale"], d2m.CAL["detune_exp"] = sc, ex
                l = loss()
                if l < best_l:
                    best, best_l = (sc, ex), l
        d2m.CAL["detune_scale"], d2m.CAL["detune_exp"] = best
        self.record("detune", serum_spread_Hz={d: round(v, 2) for d, v in ref.items()}, log2_err_before=before, log2_err_after=loss(),
                    detune_scale=d2m.CAL["detune_scale"], detune_exp=d2m.CAL["detune_exp"])
        # DetuneWid is a spread multiplier against that default
        wids = [0.0, 25.0, 50.0, 75.0, 100.0]
        d0 = ref[0.25]
        table = [[w, round(self.ref(set_osc(sine, kParamUnison=7.0, kParamDetune=0.25, kParamDetuneWid=w), [69], 2.0, 1.9, fn) / d0, 3)] for w in wids]
        d2m.CAL["detune_wid"] = table
        self.record("detune_wid", spread_vs_default=table)

    def width(self):
        saw = self.base
        pts = [25.0, 50.0, 75.0, 100.0]
        fn = lambda a: side_mid_db(a[:, 11025:55125])
        cn = lambda a: correlation(a[:, 11025:55125])
        def patch(w):
            return set_osc(saw, kParamUnison=16.0, kParamDetune=0.25, kParamUnisonStereo=w)
        ref = {w: (self.ref(patch(w), [48], 1.5, 1.4, fn), self.ref(patch(w), [48], 1.5, 1.4, cn)) for w in pts}
        absent = self.ref(set_osc(saw, kParamUnison=16.0, kParamDetune=0.25), [48], 1.5, 1.4, fn)
        # which stored value a preset with no Stereo param behaves like
        d2m.CAL["stereo_default"] = float(np.interp(absent, [ref[w][0] for w in pts], pts))
        def loss():
            e = 0.0
            for w in pts:
                y = mean_of(lambda: tablor_render(patch(w), [48], 1.5, 1.4)[0], 2, fn)
                e += abs(y - ref[w][0])
            return e / len(pts)
        before = loss()
        best, l = self.grid("width_scale", [0.25, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5], loss)
        t_full = mean_of(lambda: tablor_render(patch(100.0), [48], 1.5, 1.4)[0], 3, fn)
        t_corr = mean_of(lambda: tablor_render(patch(100.0), [48], 1.5, 1.4)[0], 3, cn)
        self.record("stereo_width_16", serum_side_mid_dB={w: round(v[0], 1) for w, v in ref.items()}, serum_correlation={w: round(v[1], 3) for w, v in ref.items()},
                    serum_absent_dB=absent, stereo_default=d2m.CAL["stereo_default"], dB_err_before=before, dB_err_after=l, width_scale=best,
                    tablor_at_100_side_mid_dB=t_full, tablor_at_100_corr=t_corr)

    def sub(self):
        t = copy.deepcopy(self.base)
        t["Oscillator0"]["plainParams"] = {"kParamEnable": 0.0}
        t["Oscillator4"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.75}
        y = self.ref(t, [48], 1.0, 0.9, lambda a: rms_db(a[:, 11025:33075]))
        f_s = fundamental(self.s.render(t, [48], 1.0, 0.9)[:, 11025:33075])
        def loss():
            return abs(rms_db(tablor_render(t, [48], 1.0, 0.9)[0][:, 11025:33075]) - y)
        before = loss()
        best, l = self.grid("sub_scale", np.arange(0.3, 1.61, 0.05), loss)
        f_t = fundamental(tablor_render(t, [48], 1.0, 0.9)[0][:, 11025:33075])
        self.record("sub", serum_dB=y, dB_err_before=before, dB_err_after=l, sub_scale=best, serum_fundamental_Hz=f_s, tablor_fundamental_Hz=f_t)

    def octave_check(self):
        """Serum's oscillators reach further than Tablor's +/-24 semitone tune."""
        sine = init_tree(self.base_src, "Analog/Basic Shapes.wav", 7)
        out = {}
        for oct_ in (0.0, -1.0, -2.0, -3.0, -4.0):
            t = set_osc(sine, kParamOctave=oct_)
            out[oct_] = (fundamental(self.s.render(t, [60], 1.0, 0.9)[:, 11025:33075]),
                         fundamental(tablor_render(t, [60], 1.0, 0.9)[0][:, 11025:33075]))
        self.record("octave_reach", note=60, serum_vs_tablor_Hz={k: (round(a, 1), round(b, 1)) for k, (a, b) in out.items()})


# ------------------------------------------------------------------ A/B scoring
def reference(serum, name, tree, notes, avg, dur, hold):
    """A preset's Serum renders never change, so they are made once. `serum` is a function
    that gives the plugin only if it is needed, so a cached run never loads it."""
    path = OUT / "refs" / f"{re.sub(r'[^A-Za-z0-9]+', '_', name)}_{'_'.join(map(str, notes))}_{avg}x{dur}.npy"
    if path.exists():
        return list(np.load(path))
    refs = [serum().render(tree, notes, dur, hold) for _ in range(avg)]
    path.parent.mkdir(exist_ok=True)
    np.save(path, np.array(refs))
    return refs


def score_patch(serum, name, notes, avg=3, dur=2.0, hold=1.5):
    header, tree = load_preset(name)
    tree = strip_fx(tree)
    refs = reference(serum, name, tree, notes, avg, dur, hold)
    tabs = []
    for _ in range(avg):
        a, m = tablor_render(tree, notes, dur, hold, header=header)
        tabs.append(a)
    h0, h1 = int(0.05 * SR), int(hold * SR)
    avg_fn = lambda xs, fn: float(np.mean([fn(x) for x in xs]))
    res = {}
    for label, fn in (
        ("rms_db", lambda a: rms_db(a[:, :h1])),
        ("peak_db", lambda a: peak_db(a)),
        ("centroid_hz", lambda a: centroid(a[:, h0:h1])),
        ("rolloff_hz", lambda a: rolloff(a[:, h0:h1])),
        ("decay_t20_s", lambda a: decay_time(a)),
        ("side_mid_db", lambda a: side_mid_db(a[:, h0:h1])),
        ("correlation", lambda a: correlation(a[:, h0:h1])),
    ):
        res[label] = (avg_fn(refs, fn), avg_fn(tabs, fn))
    n = min(refs[0].shape[1], tabs[0].shape[1])
    er, et = np.mean([envelope_db(x[:, :n]) for x in refs], axis=0), np.mean([envelope_db(x[:, :n]) for x in tabs], axis=0)
    er, et = np.maximum(er - er.max(), -60.0), np.maximum(et - et.max(), -60.0)   # silence is 60 dB down, not 180
    k = (er > -60.0) | (et > -60.0)
    res["env_shape_rmse_db"] = float(np.sqrt(np.mean((er - et)[k] ** 2)))
    res["band_rmse_db"] = float(np.mean([spec_dist(tabs[i][:, h0:h1], refs[i][:, h0:h1]) for i in range(avg)]))
    r0 = np.mean([spec_dist(refs[i][:, h0:h1], refs[(i + 1) % avg][:, h0:h1]) for i in range(avg)])
    res["band_rmse_serum_vs_itself_db"] = float(r0)
    res["rms_serum_repeat_spread_db"] = float(np.ptp([rms_db(x[:, :h1]) for x in refs]))
    res["gaps"] = m["critical"] + m["approx"]
    res["tier"] = m["tier"]
    return res


def deltas(r):
    return {
        "rms": r["rms_db"][1] - r["rms_db"][0],
        "peak": r["peak_db"][1] - r["peak_db"][0],
        "centroid_st": 12 * math.log2(max(r["centroid_hz"][1], 1) / max(r["centroid_hz"][0], 1)),
        "rolloff_st": 12 * math.log2(max(r["rolloff_hz"][1], 1) / max(r["rolloff_hz"][0], 1)),
        "decay_ratio": r["decay_t20_s"][1] / max(r["decay_t20_s"][0], 1e-3),
        "env_rmse": r["env_shape_rmse_db"],
        "band_rmse": r["band_rmse_db"],
        "side_mid": r["side_mid_db"][1] - r["side_mid_db"][0],
        "corr": r["correlation"][1] - r["correlation"][0],
    }


def usable(tree):
    """Every enabled wavetable is one Serum can actually open."""
    for i in range(3):
        o = tree[f"Oscillator{i}"]
        p = o["plainParams"] if isinstance(o["plainParams"], dict) else {}
        if not p.get("kParamEnable", 1.0 if i == 0 else 0.0) or p.get("kParamType", "kOsc_WT") != "kOsc_WT":
            continue
        rel = o[f"WTOsc{i}"].get("relativePathToWT", "").replace("\\", "/").lstrip("/")
        if rel and not any((SERUM_TABLES / c).is_file() for c in (rel, "S2 Tables/" + rel)):
            return False
    return True


def sounding_notes(tree, name):
    """Play each preset where it is meant to sound. A bass with its oscillators three
    octaves down is written to be played high; at note 36 it would be 8 Hz."""
    best, oct_ = -1.0, 0.0
    for i in range(3):
        p = tree[f"Oscillator{i}"]["plainParams"]
        p = p if isinstance(p, dict) else {}
        if p.get("kParamEnable", 1.0 if i == 0 else 0.0) and p.get("kParamVolume", 0.75) > best:
            best, oct_ = p.get("kParamVolume", 0.75), p.get("kParamOctave", 0.0)
    shift = int(-12 * oct_)
    base = [36] if name.startswith("Bass") else [48, 55, 60, 63]
    return [n + shift for n in base]


def score_all(serum):
    out = {}
    sets = [("donk bass", CANONICAL["donk bass"]), ("poly chord stab", CANONICAL["poly chord stab"]), ("FM control", FM_CONTROL)]
    sets += [("held out", n) for n in HELD_OUT]
    for role, name in sets:
        tree = load_preset(name)[1]
        if not usable(tree):
            log(f"  {role:16s} {name:28s} SKIPPED: Serum cannot open one of its wavetables")
            continue
        notes = sounding_notes(tree, name)
        r = score_patch(serum, name, notes)
        r["role"] = role
        out[name] = r
        d = deltas(r)
        log(f"  {role:16s} {name:28s} rms {d['rms']:+6.1f} dB | peak {d['peak']:+6.1f} | centroid {d['centroid_st']:+6.1f} st | "
            f"rolloff {d['rolloff_st']:+6.1f} st | decay x{d['decay_ratio']:.2f} | env {d['env_rmse']:4.1f} dB | bands {d['band_rmse']:4.1f} dB "
            f"(Serum vs itself {r['band_rmse_serum_vs_itself_db']:.1f}) | side/mid {d['side_mid']:+5.1f} | corr {d['corr']:+.2f}")
    return out


def summary(scores, roles=None):
    keys = ["rms", "peak", "centroid_st", "rolloff_st", "env_rmse", "band_rmse", "side_mid"]
    rows = [deltas(r) for r in scores.values() if roles is None or r["role"] in roles]
    return {k: float(np.mean([abs(d[k]) for d in rows])) for k in keys} | {
        "decay_log2": float(np.mean([abs(math.log2(max(d["decay_ratio"], 1e-3))) for d in rows]))}


def write_cal_into_mapper(cal):
    p = ROOT / "deephouse2move.py"
    t = p.read_text(encoding="utf-8")
    def tidy(v):
        return round(v, 4) if isinstance(v, float) else [tidy(x) for x in v] if isinstance(v, list) else v
    body = "CAL = {\n" + "".join(f'    "{k}": {tidy(v)!r},\n' for k, v in cal.items()) + "}\n"
    t2, n = re.subn(r"CAL = \{\n.*?\n\}\n", lambda _: body, t, count=1, flags=re.S)
    assert n == 1
    p.write_text(t2, encoding="utf-8", newline="\n")


def load_cal(name):
    d2m.CAL.clear()
    d2m.CAL.update(json.loads((OUT / name).read_text(encoding="utf-8")))


NAMES = ["rms", "peak", "centroid_st", "rolloff_st", "env_rmse", "band_rmse", "side_mid", "decay_log2"]


def stage_fit(a):
    t0 = time.time()
    serum = Serum()
    _, base_src = load_preset("Bass - Gravity")
    start_cal = dict(d2m.CAL)
    log("\n== fitting on isolated probes ==")
    fit = Fit(serum, init_tree(base_src), a.avg)
    fit.base_src = base_src
    for step in (fit.level, fit.master, fit.unison_level, fit.cutoff, fit.resonance, fit.keytrack, fit.drive,
                 fit.envelope_times, fit.filter_envelope, fit.detune, fit.width, fit.sub, fit.octave_check):
        log(f"\n-- {step.__name__} ({time.time() - t0:.0f}s, {serum.audio_seconds:.0f}s of Serum audio)")
        step()
        (OUT / "probe_results.json").write_text(json.dumps(fit.report, indent=1, default=str), encoding="utf-8")
    # A unison level correction this small sits inside the 1.2 dB between identical Serum runs.
    if abs(d2m.CAL["unison_gain_exp"]) < 0.1:
        d2m.CAL["unison_gain_exp"] = 0.0
    (OUT / "cal_fitted.json").write_text(json.dumps(d2m.CAL, indent=1), encoding="utf-8")
    log("\n== fitted table ==")
    for k, v in d2m.CAL.items():
        log(f"  {k}: {start_cal.get(k)} -> {round(v, 4) if isinstance(v, float) else v}")


def stage_choose(a):
    """Filter variants on the acceptance set. Serum is only touched to fill the cache."""
    load_cal("cal_fitted.json")
    fitted = dict(d2m.CAL)
    serum = lazy_serum()
    accept = {"donk bass", "poly chord stab", "held out"}
    res_on = {"res_exp": fitted["res_exp"], "res_scale": fitted["res_scale"]}
    res_off = {"res_exp": 1.0, "res_scale": 1.0}
    variants = {}
    for offset in sorted({fitted["cutoff_offset"], -10.0, -8.0, -6.0, -4.0, -2.0, 0.0}):
        for rname, r in (("res fitted", res_on), ("res linear", res_off)):
            for env in (1.0, fitted["flt_env_scale"]):
                # The key tracking pivot was fitted at one offset, and a key-tracked patch adds
                # the two together, so it has to move with the offset to keep that sum.
                variants[f"cutoff {offset:+.0f}, {rname}, env {env:.2f}"] = fitted | r | {
                    "cutoff_offset": offset,
                    "cutoff_scale": fitted["cutoff_scale"] if offset == fitted["cutoff_offset"] else 1.0,
                    "flt_env_scale": env,
                    "key_pivot": fitted["key_pivot"] + offset - fitted["cutoff_offset"]}
    log("\n== filter variants, mean absolute delta over the acceptance set ==")
    log("  " + " " * 34 + "".join(f"{n:>12s}" for n in NAMES))
    table = {}
    quiet = globals()["log"]
    for vname, cal in variants.items():
        d2m.CAL.clear()
        d2m.CAL.update(cal)
        globals()["log"] = lambda *x: None
        scores = score_all(serum)
        globals()["log"] = quiet
        table[vname] = summary(scores, accept)
        log(f"  {vname:34s}" + "".join(f"{table[vname][n]:12.2f}" for n in NAMES))
    # Spectral fit is what these constants exist for.
    spectral = lambda v: table[v]["centroid_st"] / 12 + table[v]["rolloff_st"] / 12 + table[v]["band_rmse"] / 20
    winner = min(variants, key=spectral)
    log(f"\n  chosen: {winner}")
    (OUT / "cal_final.json").write_text(json.dumps(variants[winner], indent=1), encoding="utf-8")


def stage_validate(a):
    """Exactly as shipped, clip guard included. Decides nothing."""
    load_cal("cal_final.json")
    def shipped(tree, notes, dur, hold, vel=100, header=None):
        header = header or {"presetName": "probe", "tags": []}
        m = d2m.map_to_tablor(d2m.to_vital(d2m.physical(tree, header)), {}, SERUM_TABLES, WT_OUT, "Deep House")
        d2m.trim_for_headroom(m, MOVE_ROOT)
        return run_cli(d2m.blob(m), notes, dur, hold, vel), m
    globals()["tablor_render"] = shipped
    HELD_OUT[:] = VALIDATION
    log("\n== validation presets, as shipped ==")
    now = score_all(lazy_serum())
    (OUT / "scores_validation.json").write_text(json.dumps(now, indent=1), encoding="utf-8")

    keys = {"rms": lambda r: abs(r["rms_db"][1] - r["rms_db"][0]), "peak": lambda r: abs(r["peak_db"][1] - r["peak_db"][0]),
            "centroid": lambda r: abs(12 * math.log2(max(r["centroid_hz"][1], 1) / max(r["centroid_hz"][0], 1))),
            "rolloff": lambda r: abs(12 * math.log2(max(r["rolloff_hz"][1], 1) / max(r["rolloff_hz"][0], 1))),
            "band": lambda r: r["band_rmse_db"], "env": lambda r: r["env_shape_rmse_db"],
            "decay": lambda r: abs(math.log2(max(r["decay_t20_s"][1], 1e-3) / max(r["decay_t20_s"][0], 1e-3))),
            "side": lambda r: abs(r["side_mid_db"][1] - r["side_mid_db"][0])}
    base = {label: json.loads((OUT / f).read_text(encoding="utf-8")) for label, f in (("neutral", "fresh_neutral.json"), ("patch 1", "fresh_v1.json"))}
    log(f"\n{'metric':10s}" + "".join(f"{l:>10s}" for l in base) + f"{'now':>10s}   better / worse than patch 1 (of {len(VALIDATION)})")
    for k, f in keys.items():
        cols = "".join(f"{np.median([f(b[n]) for n in VALIDATION]):10.2f}" for b in base.values())
        x, y = [f(base["patch 1"][n]) for n in VALIDATION], [f(now[n]) for n in VALIDATION]
        log(f"{k:10s}{cols}{np.median(y):10.2f}   {sum(q < p for p, q in zip(x, y))} / {sum(q > p for p, q in zip(x, y))}")
    log("\nSerum against itself: band median %.1f dB, rms spread median %.1f dB" % (
        np.median([now[n]["band_rmse_serum_vs_itself_db"] for n in VALIDATION]), np.median([now[n]["rms_serum_repeat_spread_db"] for n in VALIDATION])))
    if a.apply:
        write_cal_into_mapper(json.loads((OUT / "cal_final.json").read_text(encoding="utf-8")))
        log("\nwrote the chosen table into deephouse2move.py")


def fx_metrics(a, hold):
    """The part of a render that effects change: the held note, and the tail after it."""
    h0, h1 = int(0.05 * SR), int(hold * SR)
    t0, t1 = int((hold + 0.3) * SR), int((hold + 1.3) * SR)
    tail = a[:, t0:t1]
    return {"rms": rms_db(a[:, h0:h1]), "centroid": centroid(a[:, h0:h1]), "side_mid": side_mid_db(a[:, h0:h1]),
            "decay": decay_time(a), "tail_rms": rms_db(tail) if tail.shape[1] > 1000 else -120.0,
            "tail_centroid": centroid(tail) if tail.shape[1] > 1000 and np.abs(tail).max() > 1e-6 else 0.0}


def stage_fx(a):
    """Serum with ALL of its effects against Tablor with the converter's FX block, and against
    the same Tablor with that block switched off, over the validation presets. The first is
    how close it is now; the second is what the FX block bought. Nothing here is fitted."""
    import fxmap
    load_cal("cal_final.json")
    serum = lazy_serum()
    dur, hold = 4.0, 1.0
    rows = {}
    log("\n== effects: Serum with its FX vs Tablor with and without the FX block ==")
    for name in VALIDATION + list(CANONICAL.values()):
        header, tree = load_preset(name)
        if not usable(tree):
            log(f"  {name:28s} SKIPPED: Serum cannot open one of its wavetables")
            continue
        notes = sounding_notes(tree, name)
        refs = reference(serum, name + "_fx", tree, notes, 2, dur, hold)
        m = d2m.map_to_tablor(d2m.to_vital(d2m.physical(tree, header)), {}, SERUM_TABLES, WT_OUT, "Deep House")
        d2m.trim_for_headroom(m, MOVE_ROOT)
        dry = copy.deepcopy(m)
        dry["params"].update(fxmap.fx_defaults())
        tab_fx = [run_cli(d2m.blob(m), notes, dur, hold) for _ in range(2)]
        tab_dry = [run_cli(d2m.blob(dry), notes, dur, hold) for _ in range(2)]
        mean = lambda xs: {k: float(np.mean([fx_metrics(x, hold)[k] for x in xs])) for k in fx_metrics(xs[0], hold)}
        h0, h1 = int(0.05 * SR), int(hold * SR)
        band = lambda ts: float(np.mean([spec_dist(t[:, h0:h1], r[:, h0:h1]) for t, r in zip(ts, refs)]))
        rec = {"serum": mean(refs), "fx": mean(tab_fx), "dry": mean(tab_dry), "band_fx": band(tab_fx), "band_dry": band(tab_dry),
               "info": m.get("fx_info", {}), "notes": [n for n in m["notes"] if n.startswith(("Reverb", "Delay", "Width", "EQ", "not carried"))]}
        rows[name] = rec
        s, f, d = rec["serum"], rec["fx"], rec["dry"]
        fl = lambda v: max(v, -80.0)
        log(f"  {name:28s} rms {f['rms'] - s['rms']:+5.1f} (dry {d['rms'] - s['rms']:+5.1f}) | tail {fl(f['tail_rms']) - fl(s['tail_rms']):+6.1f} (dry {fl(d['tail_rms']) - fl(s['tail_rms']):+6.1f}) | "
            f"bands {rec['band_fx']:4.1f} (dry {rec['band_dry']:4.1f}) | side/mid {f['side_mid'] - s['side_mid']:+5.1f} (dry {d['side_mid'] - s['side_mid']:+5.1f}) | "
            f"decay x{f['decay'] / max(s['decay'], 1e-3):.2f} (dry x{d['decay'] / max(s['decay'], 1e-3):.2f})")
    (OUT / "scores_fx.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    ab = lambda k, w: [abs(r[w][k] - r["serum"][k]) for r in rows.values()]
    log(f"\n{'metric':14s}{'FX block':>10s}{'dry':>10s}   better / worse with the FX block (of {len(rows)})")
    for label, k, fn in (("rms dB", "rms", None), ("centroid st", "centroid", "st"), ("tail rms dB", "tail_rms", None),
                         ("tail centroid st", "tail_centroid", "st"), ("side/mid dB", "side_mid", None), ("decay log2", "decay", "log")):
        def err(w, r):
            x, y = r[w][k], r["serum"][k]
            if fn == "st":
                return abs(12 * math.log2(max(x, 20) / max(y, 20)))
            if fn == "log":
                return abs(math.log2(max(x, 1e-3) / max(y, 1e-3)))
            if k == "tail_rms":                   # below -80 dB there is no tail to compare, only two kinds of silence
                return abs(max(x, -80.0) - max(y, -80.0))
            return abs(x - y)
        e1 = [err("fx", r) for r in rows.values()]
        e0 = [err("dry", r) for r in rows.values()]
        log(f"{label:14s}{np.median(e1):10.2f}{np.median(e0):10.2f}   {sum(a < b for a, b in zip(e1, e0))} / {sum(a > b for a, b in zip(e1, e0))}")
    b1 = [r["band_fx"] for r in rows.values()]
    b0 = [r["band_dry"] for r in rows.values()]
    log(f"{'band rmse dB':14s}{np.median(b1):10.2f}{np.median(b0):10.2f}   {sum(a < b for a, b in zip(b1, b0))} / {sum(a > b for a, b in zip(b1, b0))}")


def lazy_serum():
    holder = []
    def get():
        if not holder:
            holder.append(Serum())
        return holder[0]
    return get


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the chosen constants into deephouse2move.py")
    ap.add_argument("--avg", type=int, default=3)
    ap.add_argument("--stage", choices=("fit", "choose", "validate", "fx"))
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    if not a.stage:                  # each in its own process: a new plugin instance has a new render allowance
        LOG_PATH.write_text("", encoding="utf-8")
        for stage in ("fit", "choose", "validate", "fx"):
            cmd = [sys.executable, "-X", "utf8", __file__, "--stage", stage, "--avg", str(a.avg)] + (["--apply"] if a.apply else [])
            subprocess.run(cmd)
        return

    t0 = time.time()
    try:
        {"fit": stage_fit, "choose": stage_choose, "validate": stage_validate, "fx": stage_fx}[a.stage](a)
    except Exception as e:
        log(f"\nSTAGE {a.stage} FAILED: {e}")
        sys.stdout.flush()
        os._exit(1)
    log(f"\n{a.stage} done in {time.time() - t0:.0f}s")
    sys.stdout.flush()
    os._exit(0)      # Serum's plugin segfaults in teardown; every result is on disk by now


if __name__ == "__main__":
    main()
