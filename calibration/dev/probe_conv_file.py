#!/usr/bin/env python3
"""What the Convolution reverb's Decay, Damping, Attack and Size do to a file IR, measured on Serum: a short noise burst through one Conv node (Wet 100 %), its energy over time in octave
bands (the burst is the same in every run, so a band's energy over time is the IR's), against the same measure of the unmodified IR file stretched by Size.

  python calibration/dev/probe_conv_file.py [preset.serumpreset]      (default Spectral_Strings_4.serumpreset: Factory/Medium/Baroque Ballroom.flac)
Prints, per case, the energy in dB re the first 50 ms of the unmodified IR at five times, for four octave bands."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np, soundfile as sf
import serum_paths
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file
SR = H.SR
BANDS = [(125, 250), (500, 1000), (2000, 4000), (6000, 12000)]
TIMES = [0.0, 0.1, 0.25, 0.5, 1.0, 2.0]


def band_env(x, step=0.05):
    out = []
    for lo, hi in BANDS:
        n = int(step * SR)
        e = []
        for i in range(0, len(x) - n, n):
            s = np.fft.rfft(x[i:i + n] * np.hanning(n)); f = np.fft.rfftfreq(n, 1 / SR)
            e.append(float((np.abs(s[(f >= lo) & (f < hi)]) ** 2).sum()) + 1e-20)
        out.append(np.array(e))
    return np.array(out)


def show(tag, e, ref):
    row = []
    for b in range(len(BANDS)):
        row.append(" ".join(f"{10 * np.log10(e[b][int(t / 0.05)] / ref[b][0]):+6.1f}" for t in TIMES))
    print(f"{tag:28s} | " + " | ".join(row), flush=True)


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "Spectral_Strings_4.serumpreset"
    _, t = decode_preset_file(src)
    node = next(n for n in t["FXRack0"]["FX"] if "FXConv" in n)
    ir_file = Path(serum_paths.SERUM_ROOT) / "Impulses" / node["FXConv"]["relativePathToIR"]
    ir, sr = sf.read(str(ir_file), dtype="float64")
    ir = ir.mean(1) if ir.ndim == 2 else ir
    print("IR", ir_file.name, len(ir) / sr, "s; columns: 125-250 | 500-1k | 2-4k | 6-12k Hz at", TIMES, "s", flush=True)
    rng = np.random.default_rng(1)
    burst = rng.standard_normal(int(0.004 * SR))
    ref = band_env(np.convolve(np.r_[burst, np.zeros(int(4 * SR) - len(burst))], ir)[:int(4 * SR)])
    show("IR unmodified", ref, ref)
    base = {"kParamWet": 100.0}

    def only(pp):
        tt = copy.deepcopy(t)
        for r in ("FXRack0", "FXRack1", "FXRack2"):
            tt[r]["FX"] = []
        n = copy.deepcopy(node)
        n["FXConv"]["plainParams"] = pp
        tt["FXRack0"]["FX"] = [n]
        for i in (0, 1, 2):
            tt[f"Oscillator{i}"]["plainParams"] = {"kParamEnable": 0.0, "kParamVolume": 0.0}
        tt["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.8}
        tt["Env0"]["plainParams"] = {"kParamAttack": 0.0, "kParamDecay": 0.004, "kParamSustain": 0.0, "kParamRelease": 0.004, "kParamHold": 0.0}
        tt["VoiceFilter0"]["plainParams"] = {"kParamEnable": 0.0}
        for i in range(64):
            tt[f"ModSlot{i}"] = {"plainParams": "default"}
        return tt
    S = H.Serum()
    allp = dict(node["FXConv"]["plainParams"])
    cases = [("stored", allp)]
    cases += [(f"Size {s:g} (rest default)", dict(base, kParamSize=s, kParamIpTrim=0.0)) for s in (100.0, 39.1)]
    sweep = os.environ.get("SWEEP")
    sweeps = json.loads(sweep) if sweep else {"kParamDecay": (0.0, 12.27, 50.0, 100.0), "kParamDamping": (0.0, 13.6, 50.0, 100.0), "kParamAttack": (0.0, 0.0041, 0.05, 0.3)}
    if sweep:
        cases = cases[:2]
    for k, vals in sweeps.items():
        for v in vals:
            cases.append((f"{k[6:]} {v:g} (Size 100)", dict(base, kParamSize=100.0, kParamIpTrim=0.0, **{k: v})))
    for tag, pp in cases:
        pp = dict(pp, kParamWet=100.0)
        x = S.render(only(pp), [60], 4.5, 0.01).mean(0)[:int(4.2 * SR)]
        show(tag, band_env(x), ref)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
