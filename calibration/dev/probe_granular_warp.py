#!/usr/bin/env python3
"""P8: what a Warp does to the Granular oscillator (is it applied to each grain, or to the summed output?).
python calibration/dev/probe_granular_warp.py <probe>   probes: dist, ..."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import sys
sys.path.insert(0, __file__.rsplit("calibration", 1)[0] + "calibration")
import numpy as np
import granwave as G


def harm(x, t0=0.8, t1=2.4, f0=1000.0):
    """level of harmonics 1..5 of f0 (dB re the fundamental) in the left channel."""
    f, s = G.spec(x, t0, t1)
    out = []
    for h in range(1, 6):
        i = np.argmin(np.abs(f - f0 * h)); out.append(np.sqrt(s[max(0, i - 3):i + 4].max()))
    out = np.array(out)
    return 20 * np.log10(out / out[0] + 1e-12), 20 * np.log10(out[0] + 1e-12)


def dens(k):  # stored Density for knob position k (Serum: 1000 k^4 ... the notes' law is N = 30 k)
    return 1000 * k ** 4


def probe_dist():
    for menu in ("kDistSoftSat",):
        for warp in (0.0, 0.3, 0.6):
            for n, ln in ((8, 0.25), (20, 0.25), (40, 0.25), (100, 0.25)):
                g = {"kParamDensity": n, "kParamGrainLength": ln, "kParamWarpMenu": menu, "kParamWarp": warp}
                x = G.render("tone1k.wav", g, {"kParamScanRate": 0.0}, dur=2.6, hold=2.5)
                h, lv = harm(x)
                print(f"{menu:14s} warp {warp:.1f} N {n:4d} T {ln}  fund {lv:6.1f} dB  H2..H5 {np.round(h[1:], 1)}")


def rms(x, t0=0.8, t1=2.4):
    m = x[0, int(t0 * G.SR):int(t1 * G.SR)]
    return float(np.sqrt(np.mean(m ** 2))), float(np.max(np.abs(m)))


def probe_noise():
    """white-noise grains: incoherent, so per-grain and summed shaping differ in how much the shaper bites as the overlap grows."""
    for menu, warp in (("kDistSoftSat", 1.0), ("kDistTube", 1.0)):
        for n in (4, 20, 100, 400):
            r = []
            for w in (0.0, warp):
                g = {"kParamDensity": n, "kParamGrainLength": 0.25, "kParamWarpMenu": menu, "kParamWarp": w}
                x = G.render("noise.wav", g, {"kParamScanRate": 0.0}, dur=2.6, hold=2.5)
                r.append(rms(x))
            print(f"{menu:13s} N {n:4d} overlap {n * 0.25:6.1f}  rms {r[0][0]:.4f} -> {r[1][0]:.4f} ({20 * np.log10(r[1][0] / r[0][0]):+.2f} dB)  peak {r[0][1]:.3f} -> {r[1][1]:.3f}")


def probe_pulse():
    """click1.wav (one +0.9 click at 50 ms) at Scan +100 %: every grain born before 50 ms reads the click at output time 50 ms, so n = N * 0.05 grain pulses coincide.
    Warp on each grain: the peaks add after the shaper (ratio warped / plain ~ the small-signal slope). Warp on the sum: the shaper sees the sum (the ratio falls as it saturates)."""
    for menu, warp in (("kDistSoftSat", 1.0), ("kDistTube", 1.0), ("kDistAsym", 1.0)):
        for n in (20, 100, 400, 1000):
            r = []
            for w in (0.0, warp):
                g = {"kParamDensity": n, "kParamGrainLength": 0.25, "kParamWarpMenu": menu, "kParamWarp": w}
                x = G.render("click1.wav", g, {"kParamScanRate": 100.0}, dur=0.5, hold=0.4)
                m = x[0, int(0.03 * G.SR):int(0.07 * G.SR)]
                r.append(float(m[np.argmax(np.abs(m))]))
            print(f"{menu:13s} N {n:5d} pulses {n * 0.05:5.1f}  plain {r[0]:+.4f}  warped {r[1]:+.4f}  ratio {r[1] / r[0] if r[0] else 0:.3f}")


def make_loud_noise(peak=1.0, name="noise_loud.wav"):
    import soundfile as sf, serum_paths
    d = serum_paths.SERUM_ROOT / "Samples" / "User" / "_tinyhouse_probe"; sr = 44100
    n = np.random.default_rng(2).standard_normal(8 * sr)
    n = np.clip(n / 3.0, -1, 1) * peak                      # std 0.33 of full scale
    sf.write(d / name, n.astype("float32"), sr, subtype="PCM_16")


def probe_loud():
    make_loud_noise()
    for menu, warp in (("kDistSoftSat", 1.0), ("kDistTube", 1.0)):
        for n in (4, 20, 100, 400):
            r = []
            for w in (0.0, warp):
                g = {"kParamDensity": n, "kParamGrainLength": 0.25, "kParamWarpMenu": menu, "kParamWarp": w}
                x = G.render("noise_loud.wav", g, {"kParamScanRate": 0.0}, dur=2.6, hold=2.5)
                r.append(rms(x))
            print(f"{menu:13s} N {n:4d} overlap {n * 0.25:6.1f}  rms {r[0][0]:.4f} -> {r[1][0]:.4f} ({20 * np.log10(r[1][0] / r[0][0]):+.2f} dB)  peak {r[0][1]:.3f} -> {r[1][1]:.3f}")


if __name__ == "__main__":
    for p in sys.argv[1:] or ["dist"]:
        globals()["probe_" + p]()
