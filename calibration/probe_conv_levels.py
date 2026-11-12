#!/usr/bin/env python3
"""How Size, Decay and IpTrim move the level of a FILE impulse response, measured on Serum: noise through one Conv node (Wet 100), the mean power gain over 80 Hz-14 kHz, against the
same band power of the processed IR (stretched by s = max(0.2, (Size/100)^0.7), times the Decay envelope 10^(-52.8 t / Decay / 20) for Decay below 35). Prints each case's measured gain minus
the model's, relative to the Size 100 / no Decay / IpTrim 0 case: what is left is the level law the converter has to add.

  python calibration/probe_conv_levels.py [relative IR path ...]
"""
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np, soundfile as sf
import serum_paths
import probe_conv_size as P
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file
SR = H.SR
FILES = ["Factory/Medium/Baroque Ballroom.flac", "Factory/Medium/Maze.flac"]
CASES = [(100.0, None, 0.0), (100.0, None, -9.3), (39.1, None, 0.0), (39.1, 12.27, 0.0), (100.0, 12.27, 0.0), (100.0, 5.0, 0.0), (20.0, None, 0.0), (60.0, 20.0, -6.0)]


def processed(x, sr, size, decay):
    s = max(0.2, (size / 100.0) ** 0.7)
    n = len(x)
    idx = np.arange(int(n * s) + 1) / s
    y = np.stack([np.interp(idx, np.arange(n), x[:, c], right=0) for c in range(x.shape[1])], 1)
    if decay is not None and decay < 35:
        t = np.arange(len(y)) / sr
        y = y * (10 ** (-52.8 * t / decay / 20))[:, None]
    return y


def band_db(y, sr):
    f = np.fft.rfftfreq(1 << 18, 1 / sr); sel = (f > 80) & (f < 14000)
    H2 = np.abs(np.fft.rfft(y, 1 << 18, axis=0)) ** 2
    return 10 * np.log10(np.mean(H2[sel].mean(0)) + 1e-30)


def main():
    files = sys.argv[1:] or FILES
    _, t = decode_preset_file(ROOT / "Spectral_Strings_4.serumpreset")
    node = next(n for n in t["FXRack0"]["FX"] if "FXConv" in n)
    S = H.Serum()

    def only(conv):
        tt = copy.deepcopy(t)
        for r in ("FXRack0", "FXRack1", "FXRack2"):
            tt[r]["FX"] = []
        if conv is not None:
            tt["FXRack0"]["FX"] = [conv]
        for i in (0, 1, 2):
            tt[f"Oscillator{i}"]["plainParams"] = {"kParamEnable": 0.0, "kParamVolume": 0.0}
        tt["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": float(os.environ.get("NOISEVOL", 0.8))}
        tt["VoiceFilter0"]["plainParams"] = {"kParamEnable": 0.0}
        for i in range(64):
            tt[f"ModSlot{i}"] = {"plainParams": "default"}
        return tt
    psd = lambda a: P.welch(a[:, int(.5 * SR):int(3.5 * SR)].mean(0))
    f, pa = psd(S.render(only(None), [60], 4.0, 3.8))
    sel = (f > 80) & (f < 14000)
    out = {}
    for rel in files:
        x, sr = sf.read(str(Path(serum_paths.SERUM_ROOT) / "Impulses" / rel), dtype="float64")
        x = x[:, None] if x.ndim == 1 else x
        base = None
        print(rel, flush=True)
        for size, decay, trim in CASES:
            n = copy.deepcopy(node); c = n["FXConv"]; c.pop("embeddedIR", None)
            pp = {"kParamWet": 100.0, "kParamSize": size, "kParamIpTrim": trim}
            if decay is not None:
                pp["kParamDecay"] = decay
            c.update(relativePathToIR=rel, numFrames=int(len(x)), numChannels=int(x.shape[1]), sampleRate=int(sr), plainParams=pp)
            _, pb = psd(S.render(only(n), [60], 4.0, 3.8))
            gain = 10 * np.log10(np.mean(pb[sel] / pa[sel]))
            model = band_db(processed(x, sr, size, decay), sr)
            d = gain - model
            if base is None:
                base = d
            out[f"{rel}|{size}|{decay}|{trim}"] = {"gain_db": float(gain), "model_db": float(model), "resid_vs_base": float(d - base)}
            print(f"  Size {size:5.1f} Decay {str(decay):>5s} IpTrim {trim:+5.1f}: serum {gain:+6.2f} dB  model {model:+6.2f}  (serum-model) minus base {d - base:+6.2f}  [IpTrim itself {trim:+.1f}]", flush=True)
            Path("calibration/probe_conv_levels.json").write_text(json.dumps(out, indent=1))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
