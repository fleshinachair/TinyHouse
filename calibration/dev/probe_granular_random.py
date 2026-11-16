#!/usr/bin/env python3
"""P5 of docs/GRANULAR_DESIGN.md: Serum's Granular Random* settings. One grain at a time (Density 0.001: N = 30 (0.001/1000)^(1/4) = 0.9487), Scan 0, grain length T2, and Start
chosen so the single +0.9 click of click1.wav (at 50 ms) lies CL ms into each grain: the pulse of grain k then sits at birth_k + CL / pitch ratio (milliseconds after the grid birth),
its height is window(age) x gain, its L / R heights are the pan. Births are on the grid k * T2 / N. Run: python calibration/dev/probe_granular_random.py [pan|pitch|offset|len|gain|dir|seed|all]"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import granwave as G

SR = G.SR
N = 30 * (0.001 / 1000) ** 0.25


def pulses(x, thr=0.005, gap=0.03):
    """[(time s, peak L, peak R)] of the click pulses in the render: runs of samples above thr, split where the signal stays quiet for `gap` seconds."""
    m = np.abs(x).max(axis=0)
    idx = np.nonzero(m > thr)[0]
    out = []
    if not len(idx):
        return out
    cut = np.nonzero(np.diff(idx) > gap * SR)[0]
    for a, b in zip(np.r_[0, cut + 1], np.r_[cut, len(idx) - 1]):
        i0, i1 = idx[a], idx[b] + 1
        out.append((i0 / SR + float(np.argmax(m[i0:i1])) / SR, float(np.abs(x[0, i0:i1]).max()), float(np.abs(x[1, i0:i1]).max())))
    return out


class Grid:
    """Grains of length T2 on the grid, click CL ms into each grain at pitch 1. grid(gran, osc) -> [(k, delay ms after the birth, L, R, sign)]."""
    def __init__(self, T2=0.1, CL=15.0, dur=8.0):
        self.T2, self.CL, self.dur, self.SP = T2, CL, dur, T2 / N
        self.b0 = None
        self.h0 = None
        ref = self._run({}, None)
        self.b0 = ref[0][0] - CL / 1000.0
        self.h0 = float(np.median([l for _, l, _ in ref]))

    def _run(self, gran, osc, sample="click1.wav", **kw):
        x = G.render(sample, {"kParamDensity": 0.001, "kParamGrainLength": self.T2, **gran}, {"kParamScanRate": 0.0, "kParamStart": (50.0 - self.CL) / 80.0, **(osc or {})},
                     dur=self.dur, hold=self.dur - 0.2, **kw)
        self.x = x
        return pulses(x, gap=self.T2 * 0.4)

    def grid(self, gran, osc=None, **kw):
        p = self._run(gran, osc, **kw)
        out = []
        for t, l, r in p:
            k = int(np.floor((t - self.b0) / self.SP + 1e-9))
            i = int(t * SR)
            seg = self.x[0, i - 40:i + 40]
            out.append((k, (t - (self.b0 + k * self.SP)) * 1000.0, l / self.h0, r / self.h0, -1 if seg[int(np.argmax(np.abs(seg)))] < 0 else 1))
        return out


def pan(g):
    for v in (10, 25, 50, 75, 100):
        p = g.grid({"kParamRandomPan": float(v)})
        L = np.array([q[2] for q in p]); R = np.array([q[3] for q in p]); x = (R - L) / (R + L)
        print(f"RandomPan {v}: n {len(p)}  x=(R-L)/(R+L) min {x.min():+.3f} max {x.max():+.3f}  c=L/(1-x) at |x|>.8 : {np.r_[L / (1 - x), R / (1 + x)][np.abs(np.r_[x, x]) > .8].mean() if (np.abs(x) > .8).any() else float('nan'):.3f}")


def pitch(g):
    for v in (2, 6, 12, 24):
        p = g.grid({"kParamRandomPitch": float(v)})
        st = np.array([12 * np.log2(g.CL / q[1]) for q in p])
        print(f"RandomPitch {v}: n {len(p)} semitones min {st.min():+.2f} max {st.max():+.2f} mean {st.mean():+.2f} std {st.std():.2f}", np.histogram(st, bins=8, range=(-v, v))[0].tolist())


def offset(g):
    for v in (5, 20, 50, 100):
        p = g.grid({"kParamRandomOffset": float(v)})
        d = np.array([q[1] for q in p]); h = np.array([q[2] for q in p])
        print(f"RandomOffset {v}: n {len(p)} (of {int(g.dur / g.SP)} grains) delay ms min {d.min():.2f} max {d.max():.2f}  (offset = CL - delay)", np.histogram(g.CL - d, bins=8, range=(-g.CL, g.CL))[0].tolist())


def length(g):
    for v in (25, 50, 100):
        p = g.grid({"kParamRandomGrainLength": float(v)})
        h = np.array([q[2] for q in p]); ks = [q[0] for q in p]
        print(f"RandomGrainLength {v}: n {len(p)} height/ref min {h.min():.3f} max {h.max():.3f} mean {h.mean():.3f}; k gaps (grid kept?): {sorted(set(np.diff(ks).tolist()))[:6]}")


def gain(g):
    for v in (25, 50, 100):
        p = g.grid({"kParamRandomGain": float(v)})
        h = np.array([q[2] for q in p])
        print(f"RandomGain {v}: n {len(p)} height/ref min {h.min():.3f} max {h.max():.3f} mean {h.mean():.3f} std {h.std():.3f}", np.histogram(h, bins=8, range=(0, 1.6))[0].tolist())


def direction(g):
    # Start 100 ms of click.wav (+ at 50 ms, - at 150 ms): a forward grain meets the - click 50 ms in, a backward one the + click 50 ms in
    for v in (0, 25, 50, 100):
        x = G.render("click.wav", {"kParamDensity": 0.001, "kParamGrainLength": 0.2, "kParamRandomDir": float(v)}, {"kParamScanRate": 0.0, "kParamStart": 100.0 / 80.0}, dur=8.0, hold=7.8)
        P = pulses(x, gap=0.05)
        sg = [(-1 if x[0, int(t * SR) - 60:int(t * SR) + 60][int(np.argmax(np.abs(x[0, int(t * SR) - 60:int(t * SR) + 60])))] < 0 else 1) for t, _, _ in P]
        print(f"RandomDir {v}: pulses {len(P)}  backward(+) fraction {sg.count(1) / max(len(sg), 1):.2f}  signs {''.join('-' if s < 0 else '+' for s in sg[:40])}")


def runs(x, thr=0.01, pool=100):
    """Grains as runs of a flat-topped (Tukey Param 0 = rectangular window) grain: [(start s, duration s, rms L, rms R, mean frequency Hz)]. The pooled envelope widens a run by `pool` samples."""
    m = np.abs(x).max(axis=0)
    env = np.r_[np.lib.stride_tricks.sliding_window_view(m, pool).max(axis=1), np.zeros(pool - 1)]      # a trailing max: a run is widened by pool-1 samples after its end
    idx = np.nonzero(env > thr)[0]
    out = []
    if not len(idx):
        return out
    cut = np.nonzero(np.diff(idx) > 1)[0]
    for a, b in zip(np.r_[0, cut + 1], np.r_[cut, len(idx) - 1]):
        i0, i1 = idx[a], idx[b] + 1 - (pool - 1)
        if i1 - i0 < 20:
            continue
        seg = x[:, i0:i1]
        zc = np.nonzero(np.diff(np.signbit(seg[0])))[0]
        f = (len(zc) - 1) / 2.0 / ((zc[-1] - zc[0]) / SR) if len(zc) > 3 else 0.0
        out.append((i0 / SR, (i1 - i0) / SR, float(np.sqrt((seg[0] ** 2).mean())), float(np.sqrt((seg[1] ** 2).mean())), f))
    return out


RECT = {"kParamWindowShape": "kWindowTukey", "kParamWindowParam": 0.0}


def rlen(g):
    """Random Grain Length with a rectangular window on the steady tone: each grain's duration and level."""
    base = {"kParamDensity": 0.001, "kParamGrainLength": 0.2, **RECT}
    ref = runs(G.render("tone1k.wav", base, {"kParamScanRate": 0.0, "kParamStart": 25.0}, dur=8.0, hold=7.8))
    L0 = np.median([r[1] for r in ref]); a0 = np.median([r[2] for r in ref])
    print(f"reference: {len(ref)} grains, duration {L0 * 1000:.2f} ms, rms {a0:.4f}")
    for v in (10, 25, 50, 75, 100):
        r = runs(G.render("tone1k.wav", {**base, "kParamRandomGrainLength": float(v)}, {"kParamScanRate": 0.0, "kParamStart": 25.0}, dur=8.0, hold=7.8))
        d = np.array([q[1] for q in r]) / L0
        a = np.array([q[2] for q in r]) / a0
        print(f"RandomGrainLength {v}: n {len(r)}  length/nominal min {d.min():.3f} max {d.max():.3f} mean {d.mean():.3f} (log2 min {np.log2(d.min()):+.2f} max {np.log2(d.max()):+.2f})  rms/ref {a.min():.3f}..{a.max():.3f}", np.histogram(d, bins=8, range=(0, 2.4))[0].tolist())


def roffset(g):
    """Random Offset: ruler.wav read from Start 50 % (4 s, segment 8) with a rectangular window; the tone says where in the file each grain began."""
    base = {"kParamDensity": 0.001, "kParamGrainLength": 0.1, **RECT}
    for v in (0, 5, 10, 25, 50):
        r = runs(G.render("ruler.wav", {**base, "kParamRandomOffset": float(v)}, {"kParamScanRate": 0.0, "kParamStart": 50.0}, dur=8.0, hold=7.8))
        k = np.array([4 * np.log2(q[4] / 300.0) for q in r if q[4] > 100])
        pos = (k - 8) * 0.5          # seconds from Start
        print(f"RandomOffset {v}: n {len(r)}  position offset s min {pos.min():+.2f} max {pos.max():+.2f} mean {pos.mean():+.2f}  hist", np.histogram(pos, bins=8, range=(-4, 4))[0].tolist())


def make_chirp():
    """chirp.wav: a linear chirp 300 -> 3000 Hz over 8 s: a grain's frequency says where in the file it reads (f = 300 + 337.5 t) and which way (rising = forward)."""
    import soundfile as sf, serum_paths
    d = serum_paths.SERUM_ROOT / "Samples" / "User" / "_tinyhouse_probe"
    t = np.arange(8 * 44100) / 44100.0
    sf.write(d / "chirp.wav", (0.5 * np.sin(2 * np.pi * (300 * t + 168.75 * t * t))).astype("float32"), 44100, subtype="PCM_16")


def freq(seg):
    zc = np.nonzero(np.diff(np.signbit(seg)))[0]
    return (len(zc) - 1) / 2.0 / ((zc[-1] - zc[0]) / SR) if len(zc) > 3 else 0.0


def chirp_grains(gran, osc, T=0.3):
    x = G.render("chirp.wav", {"kParamDensity": 0.001, "kParamGrainLength": T, **RECT, **gran}, {"kParamScanRate": 0.0, **osc}, dur=8.0, hold=7.8)
    out = []
    for st, du, a, b, f in runs(x, pool=40):
        if du < 0.9 * T:
            continue
        i0 = int(st * SR); n = int(du * SR); w = n // 4
        f1, f2 = freq(x[0, i0 + w // 2:i0 + w // 2 + w]), freq(x[0, i0 + n - w - w // 2:i0 + n - w // 2])
        out.append((st, du, (f1 + f2) / 2, f2 - f1))
    return out


def roffset2(g=None):
    for v in (0, 5, 10, 25, 50, 100):
        r = chirp_grains({"kParamRandomOffset": float(v)}, {"kParamStart": 25.0})
        pos = np.array([(q[2] - 300) / 337.5 - 0.15 for q in r])      # seconds in the file where the grain began (its mid frequency belongs to its mid point, 0.15 s in)
        print(f"RandomOffset {v}: n {len(r)} grain start in file (Start = 2.0 s): min {pos.min():.3f} max {pos.max():.3f} mean {pos.mean():.3f}", np.histogram(pos - 2.0, bins=8, range=(-2, 2))[0].tolist())


def rdir2(g=None):
    for v in (0, 25, 50, 100):
        r = chirp_grains({"kParamRandomDir": float(v)}, {"kParamStart": 50.0})
        back = [q[3] < 0 for q in r]
        print(f"RandomDir {v}: n {len(r)}  fraction backwards {sum(back) / len(r):.2f}  slopes Hz {[round(q[3]) for q in r[:12]]}")


def rgain(g):
    base = {"kParamDensity": 0.001, "kParamGrainLength": 0.2, **RECT}
    ref = runs(G.render("tone1k.wav", base, {"kParamScanRate": 0.0, "kParamStart": 25.0}, dur=8.0, hold=7.8))
    a0 = np.median([r[2] for r in ref])
    for v in (25, 50, 100):
        r = runs(G.render("tone1k.wav", {**base, "kParamRandomGain": float(v)}, {"kParamScanRate": 0.0, "kParamStart": 25.0}, dur=8.0, hold=7.8))
        a = np.array([q[2] for q in r]) / a0
        print(f"RandomGain {v}: n {len(r)} rms/ref min {a.min():.3f} max {a.max():.3f} mean {a.mean():.3f}", np.histogram(a, bins=8, range=(0, 1.6))[0].tolist())


def seed(g):
    a = g.grid({"kParamRandomPan": 100.0}); b = g.grid({"kParamRandomPan": 100.0}); c = g.grid({"kParamRandomPan": 100.0}, note=61)
    pa = [round(q[3] - q[2], 3) for q in a[:8]]
    print("pan sequence run 1:", pa)
    print("pan sequence run 2:", [round(q[3] - q[2], 3) for q in b[:8]], "-> identical:", pa == [round(q[3] - q[2], 3) for q in b[:8]])


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    g = Grid()
    for name, fn in (("pan", pan), ("seed", seed), ("pitch", pitch), ("offset", offset), ("len", length), ("gain", gain), ("rlen", lambda g: rlen(g)), ("roffset", roffset), ("roffset2", roffset2), ("rdir2", rdir2), ("rgain", rgain), ("dir", direction)):
        if which in (name, "all"):
            fn(g)
