#!/usr/bin/env python3
"""What do Serum's own FX do, in units a Tablor FX block can reproduce?

Each probe puts ONE Serum FX on a plain saw (note 36) and measures it against the same saw
with no FX. Serum's preset files store FX values in the plugin's internal units, and a missing
key means "default", so nothing here is assumed: every mapping the converter uses comes from a
probe in this file. Usage: fx_probe.py eq | reverb | delay | chorus | hyper   (one process each:
a new plugin instance has a new 1200 s render allowance).
"""
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter"))
os.chdir(ROOT)
import numpy as np
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/fx_probe.log"
log = H.log
SR = H.SR
GRID = np.array([50, 100, 200, 400, 800, 1600, 3200, 6400, 12800])


def fx_tree(base, name, params, type_id, extra=None, rack=0):
    t = copy.deepcopy(base)
    node = {f"FX{name}": {"plainParams": params}, "type": type_id}
    node.update(extra or {})
    t[f"FXRack{rack}"]["FX"] = [node]
    return t


def noise_base(base):
    """Serum's noise oscillator as the only source: a flat spectrum to measure an FX against."""
    t = copy.deepcopy(base)
    t["Oscillator0"]["plainParams"] = {"kParamEnable": 0.0}
    t["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.75}
    return t


def psd(a, lo=8820, hi=None, nfft=16384):
    """Averaged Hann periodogram of the mid channel, as (freq, power)."""
    x = a.mean(axis=0)[lo:hi]
    w = np.hanning(nfft)
    segs = [np.abs(np.fft.rfft(x[i:i + nfft] * w)) ** 2 for i in range(0, len(x) - nfft, nfft // 2)]
    return np.fft.rfftfreq(nfft, 1 / SR), np.mean(segs, axis=0)


def tf_db(dry, wet):
    """Level change in dB at each grid frequency, across a 1/3-octave band around it."""
    f, p0 = psd(dry)
    _, p1 = psd(wet)
    out = []
    for c in GRID:
        k = (f > c / 1.122) & (f < c * 1.122)
        out.append(10 * np.log10(p1[k].sum() / p0[k].sum()))
    return np.array(out)


def at_grid(f, g):
    return g


def start():
    serum = H.Serum()
    _, src = H.load_preset("Bass - Gravity")
    return serum, noise_base(H.init_tree(src))


def render(serum, tree, dur=2.0):
    return serum.render(tree, [48], dur, dur - 0.1)


def probe_eq():
    serum, base = start()
    dry = render(serum, base)
    log("EQ transfer in dB at " + " ".join(f"{g:6d}" for g in GRID) + "   (Hz)")
    for band in (1, 2):
        for ty in (0.0, 1.0, 2.0):
            for gain in (12.0, -12.0):
                p = {"kParamGain1": 0.0, "kParamGain2": 0.0, f"kParamType{band}": ty,
                     f"kParamFreq{band}": 1000.0, f"kParamGain{band}": gain, f"kParamReso{band}": 50.0}
                g = tf_db(dry, render(serum, fx_tree(base, "EQ", p, 7)))
                log(f"band {band} type {ty:.0f} f 1000 gain {gain:+5.1f} reso 50: " + " ".join(f"{x:6.1f}" for x in g))
    g = tf_db(dry, render(serum, fx_tree(base, "EQ", {}, 7)))
    log("defaults (no params):                  " + " ".join(f"{x:6.1f}" for x in g))
    log("")
    log("resonance, band 1 and 2 as peaks (gain +12 at 1000 Hz), and as cuts")
    for band in (1, 2):
        for ty in (1.0, 2.0):
            for reso in (0.0, 25.0, 50.0, 75.0, 100.0):
                p = {"kParamGain1": 0.0, "kParamGain2": 0.0, f"kParamType{band}": ty,
                     f"kParamFreq{band}": 1000.0, f"kParamGain{band}": 12.0, f"kParamReso{band}": reso}
                g = tf_db(dry, render(serum, fx_tree(base, "EQ", p, 7)))
                log(f"band {band} type {ty:.0f} reso {reso:5.0f}: " + " ".join(f"{x:6.1f}" for x in g))


def biquad_db(kind, fc, gain_db, q, f):
    """RBJ cookbook magnitude at f (Hz), at 44.1 kHz: the filters the engine will run."""
    w0 = 2 * np.pi * fc / SR
    A = 10 ** (gain_db / 40)
    cw, sw = np.cos(w0), np.sin(w0)
    al = sw / (2 * q)
    if kind == "peak":
        b = [1 + al * A, -2 * cw, 1 - al * A]; a = [1 + al / A, -2 * cw, 1 - al / A]
    elif kind == "lowshelf":
        r = 2 * np.sqrt(A) * al
        b = [A * ((A + 1) - (A - 1) * cw + r), 2 * A * ((A - 1) - (A + 1) * cw), A * ((A + 1) - (A - 1) * cw - r)]
        a = [(A + 1) + (A - 1) * cw + r, -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - r]
    elif kind == "highshelf":
        r = 2 * np.sqrt(A) * al
        b = [A * ((A + 1) + (A - 1) * cw + r), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - r)]
        a = [(A + 1) - (A - 1) * cw + r, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - r]
    elif kind == "hp":
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]; a = [1 + al, -2 * cw, 1 - al]
    elif kind == "lp":
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]; a = [1 + al, -2 * cw, 1 - al]
    z = np.exp(-1j * 2 * np.pi * np.asarray(f) / SR)
    h = (b[0] + b[1] * z + b[2] * z ** 2) / (a[0] + a[1] * z + a[2] * z ** 2)
    return 20 * np.log10(np.abs(h))


FINE = 1000 * 2 ** (np.arange(-24, 22) / 6)          # 1/6-octave grid, 63 Hz .. 11 kHz


def fine_tf(dry, wet):
    f, p0 = psd(dry)
    _, p1 = psd(wet)
    out = []
    for c in FINE:
        k = (f > c / 2 ** (1 / 12)) & (f < c * 2 ** (1 / 12))
        out.append(10 * np.log10(p1[k].sum() / p0[k].sum()))
    return np.array(out)


def fit_band(measured, kind, fc, gain, qs=np.geomspace(0.05, 12, 140), fcs=None):
    """Best (q, fc) of a biquad of this kind against a measured transfer."""
    best = (1e9, None, None)
    for q in qs:
        for c in (fcs if fcs is not None else [fc]):
            e = np.sqrt(np.mean((biquad_db(kind, c, gain, q, FINE) - measured) ** 2))
            if e < best[0]:
                best = (e, q, c)
    return best


def probe_eq_fit():
    """Q as a function of Serum's Reso, for each band type, plus the unwritten defaults."""
    serum, base = start()
    dry = render(serum, base, 3.0)
    kinds = {(1, 0): "lowshelf", (2, 0): "highshelf", (1, 1): "peak", (2, 1): "peak", (1, 2): "hp", (2, 2): "lp"}
    log("band type reso | best Q   fc   rms error (dB)   [fit with fc fixed at 1000 | fc free]")
    table = {}
    for (band, ty), kind in kinds.items():
        for reso in (0.0, 10.0, 25.0, 40.0, 50.0, 60.0, 75.0, 90.0, 100.0):
            p = {"kParamGain1": 0.0, "kParamGain2": 0.0, f"kParamType{band}": float(ty), f"kParamFreq{band}": 1000.0,
                 f"kParamGain{band}": 10.0, f"kParamReso{band}": reso}
            m = fine_tf(dry, render(serum, fx_tree(base, "EQ", p, 7), 3.0))
            e1, q1, _ = fit_band(m, kind, 1000.0, 10.0)
            e2, q2, c2 = fit_band(m, kind, 1000.0, 10.0, fcs=1000 * 2 ** np.linspace(-0.6, 0.6, 25))
            table[f"{band},{ty},{reso:.0f}"] = [q1, q2, c2]
            log(f"  {band}   {ty}   {reso:5.0f} | Q {q1:6.3f}  fc 1000  err {e1:4.2f}  | Q {q2:6.3f}  fc {c2:6.0f}  err {e2:4.2f}")
    # defaults: write only the gain and see what the other keys fall back to
    log("\ndefaults: only Gain set to +10, everything else unwritten")
    for band in (1, 2):
        p = {"kParamGain1": 0.0, "kParamGain2": 0.0, f"kParamGain{band}": 10.0}
        m = fine_tf(dry, render(serum, fx_tree(base, "EQ", p, 7), 3.0))
        for kind in ({1: ("lowshelf", "peak", "hp"), 2: ("highshelf", "peak", "lp")}[band]):
            e, q, c = fit_band(m, kind, 1000.0, 10.0, fcs=100 * 2 ** np.linspace(0, 7, 36))
            log(f"  band {band} as {kind:9s}: best Q {q:6.3f}  fc {c:7.0f}  err {e:4.2f}")
    json.dump(table, open(ROOT / "calibration/fx_eq_q.json", "w"), indent=1)


def env_db(a, hop=441):
    x = a.mean(axis=0)
    n = len(x) // hop
    return 10 * np.log10(np.maximum((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1), 1e-14))


def tail_stats(a, t_off=0.25):
    """Tail of a burst: level re the burst, RT60 from the slope between -5 and -25 dB re the
    tail's start, spectral centroid, side/mid. The burst is gone by t_off."""
    e = env_db(a)                               # 10 ms hops
    i0 = int(t_off / 0.01)
    peak = e[:i0].max()
    tail = e[i0:]
    start = tail[:5].max()
    idx = np.where(tail < start - 5)[0]
    lo = idx[0] if idx.size else 0
    idx2 = np.where(tail < start - 25)[0]
    hi = idx2[0] if idx2.size else len(tail) - 1
    rt = float("nan")
    if hi - lo >= 5:
        k = np.polyfit(np.arange(lo, hi) * 0.01, tail[lo:hi], 1)[0]   # dB per second
        rt = -60.0 / k if k < 0 else float("inf")
    seg = a[:, int(t_off * SR):int((t_off + 1.0) * SR)]
    return {"tail_vs_burst_db": float(start - peak), "rt60": rt, "centroid": H.centroid(seg), "side_mid": H.side_mid_db(seg)}


def burst(serum, base, tree_fx, dur=6.0, hold=0.12, notes=(48,)):
    return serum.render(tree_fx, list(notes), dur, hold)


def probe_reverb():
    serum, base = start()
    dry = burst(serum, base, base)
    log("dry burst: " + json.dumps({k: round(v, 2) for k, v in tail_stats(dry, 0.5).items()}))
    log("env of dry (dB per 10 ms, first 60): " + " ".join(f"{v:.0f}" for v in env_db(dry)[:60]))
    log("\nreverb, Wet 100, others unwritten (type = whatever Serum defaults to)")
    for size in (0.0, 25.0, 50.0, 75.0, 100.0):
        r = tail_stats(burst(serum, base, fx_tree(base, "Reverb", {"kParamWet": 100.0, "kParamSize": size}, 6)))
        log(f"  size {size:5.0f}: " + " ".join(f"{k} {v:8.2f}" for k, v in r.items()))
    log("\nreverb, Type kHall")
    for size in (0.0, 25.0, 50.0, 75.0, 100.0):
        r = tail_stats(burst(serum, base, fx_tree(base, "Reverb", {"kParamWet": 100.0, "kParamSize": size, "kParamType": "kHall"}, 6)))
        log(f"  size {size:5.0f}: " + " ".join(f"{k} {v:8.2f}" for k, v in r.items()))
    log("\nreverb, Type kVintage")
    for size in (0.0, 50.0, 100.0):
        r = tail_stats(burst(serum, base, fx_tree(base, "Reverb", {"kParamWet": 100.0, "kParamSize": size, "kParamType": "kVintage"}, 6)))
        log(f"  size {size:5.0f}: " + " ".join(f"{k} {v:8.2f}" for k, v in r.items()))
    log("\nreverb, default type, Size 50, Feedback sweep")
    for fb in (0.0, 25.0, 50.0, 75.0, 100.0):
        r = tail_stats(burst(serum, base, fx_tree(base, "Reverb", {"kParamWet": 100.0, "kParamSize": 50.0, "kParamFeedback": fb}, 6)))
        log(f"  feedback {fb:5.0f}: " + " ".join(f"{k} {v:8.2f}" for k, v in r.items()))
    log("\nreverb, kHall, Size 50: Wet sweep (level of the tail vs the dry burst; does the dry fade?)")
    for wet in (0.0, 10.0, 25.0, 50.0, 75.0, 100.0):
        a = burst(serum, base, fx_tree(base, "Reverb", {"kParamWet": wet, "kParamSize": 50.0, "kParamType": "kHall"}, 6))
        r = tail_stats(a)
        log(f"  wet {wet:5.0f}: burst peak {env_db(a)[:25].max():6.1f} dB (dry {env_db(dry)[:25].max():6.1f}) " + " ".join(f"{k} {v:8.2f}" for k, v in r.items()))
    log("\nreverb, kHall, Size 50, Wet 100: Freq / FreqB / Width / Delay one at a time (centroid and rt60 tell the tone)")
    for key, vals in (("kParamFreq", (0.0, 25.0, 50.0, 75.0, 100.0)), ("kParamFreqB", (0.0, 25.0, 50.0, 75.0, 100.0)),
                      ("kParamWidth", (0.0, 50.0, 100.0)), ("kParamDelay", (0.0, 25.0, 50.0, 100.0))):
        for v in vals:
            p = {"kParamWet": 100.0, "kParamSize": 50.0, "kParamType": "kHall", key: v}
            r = tail_stats(burst(serum, base, fx_tree(base, "Reverb", p, 6)))
            log(f"  {key[6:]:6s} {v:5.0f}: " + " ".join(f"{k} {x:8.2f}" for k, x in r.items()))


def echoes(a, max_s=2.0):
    """Echo times in ms from the envelope of a short burst: local maxima of the autocorrelation of
    the mid channel's absolute value after the burst."""
    x = np.abs(a.mean(axis=0))[: int(max_s * SR)]
    e = np.convolve(x, np.ones(220) / 220, mode="same")      # 5 ms smoothing
    d = e[: int(0.025 * SR)]
    c = np.correlate(e, d, mode="full")[len(e) - 1:][: int(max_s * SR)]
    c = c / (c[0] + 1e-30)
    out = []
    for i in range(int(0.03 * SR), len(c) - 1):
        if c[i] > 0.1 and c[i] >= c[i - 1] and c[i] > c[i + 1] and (not out or i / SR * 1000 > out[-1][0] + 20):
            out.append((i / SR * 1000, c[i]))
        if len(out) >= 3:
            break
    return out


def first_echo_ms(a, ch, dry):
    """Delay of channel ch, in ms: the lag at which the wet-only output best matches the dry burst."""
    x, d = a[ch], dry[ch][: int(0.3 * SR)]
    if np.abs(x).max() < 1e-6:
        return None
    n = 1 << int(np.ceil(np.log2(len(x) + len(d))))
    c = np.fft.irfft(np.fft.rfft(x, n) * np.conj(np.fft.rfft(d, n)), n)[: len(x) - len(d)]
    return float(np.argmax(np.abs(c)) / SR * 1000)


def probe_delay():
    serum, base = start()
    log("delay probe: time of the first echo (ms) in L and R, from a short burst")
    def run(label, params):
        try:
            a = serum.render(fx_tree(base, "Delay", dict(params, kParamWet=100.0, kParamFeedback=0.0), 4), [48], 4.0, 0.03)
        except RuntimeError:
            serum.audio_seconds -= 4.0
            log(f"  {label:34s} silent")
            return
        log(f"  {label:34s} L {first_echo_ms(a, 0, dry)}  R {first_echo_ms(a, 1, dry)}")
    dry = serum.render(base, [48], 4.0, 0.03)
    run("defaults", {})
    for mode in (0.0, 1.0):
        run(f"Mode {mode:.0f}", {"kParamMode": mode})
    for tl in (0.005, 0.01, 0.02, 0.03, 0.05, 0.1, 0.2):
        run(f"Mode 1 TimeL {tl} TimeR {tl * 2}", {"kParamMode": 1.0, "kParamTimeL": tl, "kParamTimeR": tl * 2})
    for tl in (0.01, 0.05):
        run(f"Mode 0 TimeL {tl} TimeR {tl * 2}", {"kParamMode": 0.0, "kParamTimeL": tl, "kParamTimeR": tl * 2})
    for off in (1.0, 1.5, 2.0, 3.0):
        run(f"Mode 0 OffsetL {off} OffsetR {off * 2}", {"kParamMode": 0.0, "kParamOffsetL": off, "kParamOffsetR": off * 2})
        run(f"Mode 1 OffsetL {off} OffsetR {off * 2}", {"kParamMode": 1.0, "kParamOffsetL": off, "kParamOffsetR": off * 2})
    run("Link 1 TimeL 0.03 TimeR 0.09", {"kParamLink": 1.0, "kParamTimeL": 0.03, "kParamTimeR": 0.09})
    run("Link 0 TimeL 0.03 TimeR 0.09", {"kParamLink": 0.0, "kParamTimeL": 0.03, "kParamTimeR": 0.09})

    log("\nwet and feedback laws, Mode 1, TimeL = TimeR = 0.03")
    pk = env_db(dry)[:5].max()
    log(f"  dry burst peak {pk:.1f} dB")
    for wet in (10.0, 25.0, 50.0, 75.0, 100.0):
        a = serum.render(fx_tree(base, "Delay", {"kParamWet": wet, "kParamFeedback": 0.0, "kParamMode": 1.0, "kParamTimeL": 0.03, "kParamTimeR": 0.03}, 4), [48], 4.0, 0.03)
        e = env_db(a)
        ms = first_echo_ms(a, 0, dry) or 0
        k = int(ms / 10)
        log(f"  wet {wet:5.0f}: burst peak {e[:6].max() - pk:6.1f} dB re dry; echo ({ms} ms) peak {e[k:k + 8].max() - pk:6.1f} dB")
    for fb in (0.0, 25.0, 50.0, 75.0, 100.0):
        a = serum.render(fx_tree(base, "Delay", {"kParamWet": 100.0, "kParamFeedback": fb, "kParamMode": 1.0, "kParamTimeL": 0.03, "kParamTimeR": 0.03}, 4), [48], 4.0, 0.03)
        e = env_db(a)
        ms = first_echo_ms(a, 0, dry) or 1
        step = max(int(ms / 10), 1)
        log(f"  fb {fb:5.0f}: echo peaks 1..5 (dB re dry burst) " + " ".join(f"{e[step * n:step * n + 8].max() - pk:6.1f}" for n in range(1, 6)))


def steady(a, lo=1.5, hi=3.5):
    x = a[:, int(lo * SR):int(hi * SR)]
    return H.rms_db(x), H.side_mid_db(x), H.centroid(x)


def probe_levels():
    """Steady-state level and width of each effect against the same noise, so the engine's wet
    gains are matched to Serum's and not guessed. 4 s noise, last 2 s measured."""
    serum, base = start()
    def go(label, tree):
        a = serum.render(tree, [48], 4.0, 3.9)
        r = steady(a)
        log(f"  {label:48s} rms {r[0]:7.2f} dB  side/mid {r[1]:7.2f} dB  centroid {r[2]:7.0f} Hz")
        return r
    ref = go("no effect", base)
    log("\nreverb at wet 100: level re the dry noise")
    for ty, sizes in (("kHall", (0.0, 25.0, 50.0, 75.0)), (None, (0.0, 25.0, 50.0, 75.0)), ("kVintage", (25.0, 50.0))):
        for size in sizes:
            p = {"kParamWet": 100.0, "kParamSize": size}
            if ty: p["kParamType"] = ty
            r = go(f"reverb {ty or 'default type'} size {size:.0f}", fx_tree(base, "Reverb", p, 6))
            log(f"      -> {r[0] - ref[0]:+.2f} dB re dry")
    log("\nreverb kHall size 50, Delay sweep (the other decay control)")
    for d in (0.0, 25.0, 50.0, 100.0):
        r = go(f"reverb kHall size 50 delay {d:.0f}", fx_tree(base, "Reverb", {"kParamWet": 100.0, "kParamSize": 50.0, "kParamType": "kHall", "kParamDelay": d}, 6))
        log(f"      -> {r[0] - ref[0]:+.2f} dB re dry")
    log("\ndelay at wet 100, feedback 0 and 50: level re the dry noise (steady)")
    for fb in (0.0, 50.0):
        for mode in (0.0, 1.0):
            r = go(f"delay mode {mode:.0f} feedback {fb:.0f}", fx_tree(base, "Delay", {"kParamWet": 100.0, "kParamFeedback": fb, "kParamMode": mode, "kParamTimeL": 0.045, "kParamTimeR": 0.045}, 4))
            log(f"      -> {r[0] - ref[0]:+.2f} dB re dry")


def probe_delaycheck():
    """Is a delay tap -6.4 dB (a 30 ms burst) or unity (steady noise)? Bursts of 30, 100, 300 ms and 1 s."""
    serum, base = start()
    for hold in (0.03, 0.1, 0.3, 1.0):
        dry = serum.render(base, [48], 3.0, hold)
        wet = serum.render(fx_tree(base, "Delay", {"kParamWet": 100.0, "kParamFeedback": 0.0, "kParamMode": 0.0, "kParamTimeL": 0.045, "kParamTimeR": 0.045}, 4), [48], 3.0, hold)
        e_d = (dry.mean(axis=0) ** 2).sum()
        e_w = (wet.mean(axis=0) ** 2).sum()
        pk_d = np.abs(dry).max(); pk_w = np.abs(wet).max()
        log(f"  burst {hold:4.2f} s: energy wet/dry {10 * np.log10(e_w / e_d):6.2f} dB   peak wet/dry {20 * np.log10(pk_w / pk_d):6.2f} dB")


def echo_windows(a, ms, n=4, burst=0.1):
    """Mid-channel segments of the first n echoes of a noise burst, `ms` apart."""
    x = a.mean(axis=0)
    w = int(burst * SR)
    step = int(ms * SR / 1000)
    return [x[k * step:k * step + w] for k in range(1, n + 1)]


def band_levels(x):
    f = np.fft.rfftfreq(len(x), 1 / SR)
    p = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    out = []
    for c in GRID:
        k = (f > c / 1.414) & (f < c * 1.414)
        out.append(10 * np.log10(p[k].sum() + 1e-30))
    return np.array(out)


def probe_delaydefaults():
    """What an unwritten Feedback, Freq and BW do, and whether Freq/BW is the band-pass I model:
    the high-pass at f / 2^(bw/2) and the low-pass at f * 2^(bw/2), in the feedback path."""
    serum, base = start()
    def echoes_of(label, params):
        a = serum.render(fx_tree(base, "Delay", dict({"kParamWet": 100.0, "kParamMode": 0.0, "kParamTimeL": 0.03, "kParamTimeR": 0.03}, **params), 4), [48], 3.0, 0.1)
        w = echo_windows(a, 0.03 * 5030)
        e = [10 * np.log10((x ** 2).sum() + 1e-30) for x in w]
        log(f"  {label:40s} echo energies re echo 1 (dB): " + " ".join(f"{v - e[0]:6.1f}" for v in e))
        return w
    log("feedback: unwritten, then 50 (amplitude per repeat should be 0.5 = -6 dB in amplitude, -6 in energy re the previous)")
    echoes_of("Feedback unwritten", {})
    echoes_of("Feedback 50", {"kParamFeedback": 50.0})
    log("\nthe feedback filter: level of echo 2 re echo 1 per band, Feedback 100 so only the filter acts")
    log("  band centres Hz              " + " ".join(f"{g:6d}" for g in GRID))
    for label, pr in (("Freq/BW unwritten", {}), ("Freq 1000 BW 2", {"kParamFreq": 1000.0, "kParamBW": 2.0}),
                      ("Freq 1000 BW 4", {"kParamFreq": 1000.0, "kParamBW": 4.0}), ("Freq 3000 BW 1", {"kParamFreq": 3000.0, "kParamBW": 1.0}),
                      ("Freq 200 BW 2", {"kParamFreq": 200.0, "kParamBW": 2.0})):
        w = echoes_of(label, dict({"kParamFeedback": 100.0}, **pr))
        d = band_levels(w[1]) - band_levels(w[0])
        log(f"  {label:28s} " + " ".join(f"{v:6.1f}" for v in d))
        f, bw = pr.get("kParamFreq"), pr.get("kParamBW")
        if f:
            lo, hi = f / 2 ** (bw / 2), f * 2 ** (bw / 2)
            hp = 1 / np.sqrt(1 + (lo / GRID) ** 2)
            lp = 1 / np.sqrt(1 + (GRID / hi) ** 2)
            log(f"  {'  model: one-pole HP at %.0f, LP at %.0f' % (lo, hi):28s} " + " ".join(f"{v:6.1f}" for v in 20 * np.log10(hp * lp)))


PROBES = {"eq": probe_eq, "eqfit": probe_eq_fit, "reverb": probe_reverb, "delay": probe_delay, "levels": probe_levels, "delaycheck": probe_delaycheck, "delaydef": probe_delaydefaults}

if __name__ == "__main__":
    PROBES[sys.argv[1]]()
    sys.stdout.flush()
    os._exit(0)
