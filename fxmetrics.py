"""Measurements of an effect on a noise burst, written once so Serum's renders and Tablor's go
through the same code. numpy only: the converter imports this, and it must not need DawDreamer.

All inputs are (2, N) arrays at 44.1 kHz.
"""
from __future__ import annotations

import numpy as np

SR = 44100


def env_db(a, hop=441):
    """Mid-channel level in dB per 10 ms."""
    x = a.mean(axis=0)
    n = len(x) // hop
    return 10 * np.log10(np.maximum((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1), 1e-14))


def window_db(a, t0, t1):
    """Mean-square level of both channels over [t0, t1) s, in dB."""
    x = a[:, int(t0 * SR):int(t1 * SR)]
    return float(10 * np.log10(max((x ** 2).mean(), 1e-14)))


def centroid(a, t0, t1):
    x = a.mean(axis=0)[int(t0 * SR):int(t1 * SR)]
    if len(x) < 1024:
        return 0.0
    p = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return float((f * p).sum() / max(p.sum(), 1e-30))


def side_mid_db(a, t0, t1):
    x = a[:, int(t0 * SR):int(t1 * SR)]
    m, s = (x[0] + x[1]) / 2, (x[0] - x[1]) / 2
    return float(10 * np.log10(max((s ** 2).mean(), 1e-20) / max((m ** 2).mean(), 1e-20)))


def side_added_db(fx, dry, t0=0.3, t1=1.3):
    """Side power the effect added, relative to the mid, in dB. Subtracting the dry side first
    means a source that is already a little stereo (Serum's noise is) does not count as width."""
    def sm(a):
        x = a[:, int(t0 * SR):int(t1 * SR)]
        return ((x[0] - x[1]) / 2) ** 2, ((x[0] + x[1]) / 2) ** 2
    s1, m1 = sm(fx)
    s0, _ = sm(dry)
    added = max(float(s1.mean() - s0.mean()), 1e-20)
    return float(10 * np.log10(added / max(float(m1.mean()), 1e-20)))


def rt60(a, t_burst_end=0.25):
    """Decay time from the slope of the tail between 5 and 25 dB under its start. inf if it
    does not decay, nan if there is no tail to measure."""
    e = env_db(a)
    i0 = int(t_burst_end / 0.01)
    tail = e[i0:]
    if len(tail) < 20:
        return float("nan")
    # The tail starts where it peaks, not at a fixed offset: a convolution reverb whose impulse response begins 0.3 s in (OR - Thermite) has nothing in the first 50 ms, and a
    # start read from there made every later level "under it" by nothing, so the decay read as infinite.
    pk = int(np.argmax(tail[:150]))
    start = tail[pk]
    if start < -85.0:
        return 0.0                                   # nothing above the floor: no tail
    tail = tail[pk:]
    lo = np.where(tail < start - 5)[0]
    hi = np.where(tail < start - 25)[0]
    if not lo.size:
        return float("inf")
    lo = lo[0]
    hi = hi[0] if hi.size else len(tail) - 1
    if hi - lo < 5:
        return float("nan")
    k = np.polyfit(np.arange(lo, hi) * 0.01, tail[lo:hi], 1)[0]
    return float(-60.0 / k) if k < 0 else float("inf")


def reverb_tail(a):
    """What the converter matches a reverb on: how long it rings, how bright the ring is, and how
    wide. The window starts after the burst is over."""
    t0, t1 = tail_window(a)
    if t1 is None:                                   # nothing audible to read a colour from
        return {"rt60": rt60(a), "centroid": float("nan"), "side_mid": float("nan")}
    return {"rt60": rt60(a), "centroid": centroid(a, t0, t1), "side_mid": side_mid_db(a, t0, t1)}


def tail_window(a, t0=0.3, longest=1.0, shortest=0.1, drop=35.0, floor_db=-85.0, end_db=-92.0):
    """The stretch of a reverb's tail to read its colour (centroid, width) from: from 0.3 s on, until the tail has fallen `drop` dB or `longest` s.
    A short reverb is silent (a numerical floor, which is white: the engine's dither at -101 dB, Serum's own at -140) for most of a fixed window,
    and that read as a bright tail; the window also ends where the tail reaches `end_db`. (t0, None) if the tail is below `floor_db` at its start."""
    e = env_db(a)
    i0 = int(t0 / 0.01)
    if len(e) <= i0 + 2:
        return t0, None
    # from where the tail starts (its first stretch above the floor, up to 1.5 s in): a late impulse response is a tail too
    late = np.where(e[i0:i0 + 150] >= floor_db)[0]
    if not late.size:
        return t0, None
    i0 += int(late[0])
    t0 = i0 * 0.01
    start = float(e[i0:i0 + 3].max())
    below = np.where(e[i0:] < max(start - drop, end_db))[0]
    span = below[0] * 0.01 if below.size else longest
    return t0, t0 + min(max(span, shortest), longest)


def echo_amp_ratio(actual, full, ms):
    """First echo's amplitude in `actual` relative to `full` (the same effect at wet 100): both
    are measured in the window where that echo sits."""
    t0 = ms / 1000
    return float(10 ** ((window_db(actual, t0, t0 + 0.1) - window_db(full, t0, t0 + 0.1)) / 20))


def wet_ratio(actual, full, t0=0.3, t1=0.6):
    """Tail amplitude of `actual` relative to the same effect at wet 100."""
    return float(10 ** ((window_db(actual, t0, t1) - window_db(full, t0, t1)) / 20))


def inv_smoothstep(y):
    """x such that 3x^2 - 2x^3 = y, on 0..1."""
    y = min(max(y, 0.0), 1.0)
    lo, hi = 0.0, 1.0
    for _ in range(40):
        m = (lo + hi) / 2
        if m * m * (3 - 2 * m) < y:
            lo = m
        else:
            hi = m
    return (lo + hi) / 2


def smooth(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)
