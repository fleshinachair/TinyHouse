#!/usr/bin/env python3
"""Serum's Distortion transfer (probe_dist_transfer.json) -> the engine stage's curve: for every mode and drive the pre-gain G, output gain K and (for the
biased curve) bias B of y = K f(G x) whose fundamental and 2nd..5th harmonics match what Serum made of a sine at four input levels. The amplitudes in the
probe are at Serum's output (after its master, with the probe preset's default master); the engine stage sits before its own master gain, so the table
is written in the probe's units and the converter scales it by the engine's gain at that default master (fxmap.DIST_PROBE_GAIN).
Output: calibration/dist_curves.json  {mode: {"drives": [...], "G": [...], "K": [...], "B": [...], "engine_mode": n, "err_db": [...]}}"""
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
ENGINE_MODE = {"default": 0, "kSoftClip": 0, "kTapeSat": 0, "kSineShaper": 3, "kAsym": 1, "kDiode2": 1, "kOverdrive": 2, "kHardClip": 2}
N, CYC = 1 << 12, 16
ENGINE_BIAS = 0.003027                                           # the biased curve's fixed input bias in the probe's units (fx.h processDistCal: 0.004 / the probe gain in the engine's)
FLOOR = 3e-4                                                  # amplitudes below -70 dB are not compared (the probe's own noise floor)
T = np.sin(2 * np.pi * CYC * np.arange(N) / N)
W = np.hanning(N)


def curve(mode, G, B, x):
    if mode == 1:
        return np.tanh(G * (x + B)) - math.tanh(G * B)
    if mode == 2:
        return np.clip(G * x, -1, 1)
    if mode == 3:
        return np.sin(G * x)
    return np.tanh(G * x)


def harmonics(mode, G, B, a):
    y = curve(mode, G, B, a * T)
    sp = np.abs(np.fft.rfft(y * W)) * 2 / W.sum()
    return np.array([sp[CYC * k] for k in (1, 2, 3, 4, 5)])


def fit_row(rows, mode):
    """Best (G, K, B) over the four input levels of one (mode, drive)."""
    meas = [(r["a_in"], np.array(r["amps"])) for r in rows if r["amps"][0] > 0]
    if len(meas) < 3:
        return None
    best = [1e9, None]

    def trial(G, B):
        hs = [harmonics(mode, G, B, a) for a, _ in meas]
        # K from the fundamentals (geometric mean of the ratios), then the error over all five partials in dB
        K = math.exp(np.mean([math.log(m[0] / max(h[0], 1e-9)) for (_, m), h in zip(meas, hs)]))
        err = 0.0
        for (_, m), h in zip(meas, hs):
            d = 20 * np.log10(np.maximum(K * h, FLOOR)) - 20 * np.log10(np.maximum(m, FLOOR))
            err += float((d[:5] ** 2 * np.array([3, 1, 1, 0.5, 0.5])).sum())
        if err < best[0]:
            best[0], best[1] = err, (float(G), float(K), float(B))

    bgrid = (0.0,) if mode != 1 else (ENGINE_BIAS,)
    for G in np.geomspace(0.3, 100.0 if mode == 3 else 400.0, 90):
        for B in bgrid:
            trial(G, B)
    g0, b0 = best[1][0], best[1][2]                             # a folding curve is sensitive to G: refine around the best
    for G in g0 * np.linspace(0.93, 1.07, 29):
        for B in (b0,):
            trial(G, B)
    return best[1] + (math.sqrt(best[0] / (len(meas) * 6.0)),)


def main():
    pr = json.loads((ROOT / "calibration/probe_dist_transfer.json").read_text())
    out = {}
    for mode_name, em in ENGINE_MODE.items():
        drives, Gs, Ks, Bs, errs = [], [], [], [], []
        for d in sorted({r["drive"] for r in pr["rows"] if r["mode"] == mode_name}):
            f = fit_row([r for r in pr["rows"] if r["mode"] == mode_name and r["drive"] == d], em)
            if f is None:
                continue
            drives.append(d); Gs.append(f[0]); Ks.append(f[1]); Bs.append(f[2]); errs.append(round(f[3], 2))
            print("%-12s drive %3d  G %7.2f  K %6.3f (%+5.1f dB)  B %.3f  rms err %.2f dB" % (mode_name, d, f[0], f[1], 20 * math.log10(f[1]), f[2], f[3]), flush=True)
        out[mode_name] = {"drives": drives, "G": Gs, "K": Ks, "B": Bs, "engine_mode": em, "err_db": errs}
    (ROOT / "calibration/dist_curves.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
