#!/usr/bin/env python3
"""How Serum levels a file IR: noise through one Conv node (Wet 100, Size 100, IpTrim 0, no other knob) for several factory IR files, the mean power gain over 80 Hz-14 kHz against the file's own
peak, energy (per channel and mono-summed) and length. Prints a table and writes calibration/probe_conv_norm.json.

  python calibration/dev/probe_conv_norm.py [relative IR paths under Serum's Impulses folder ...]
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np, soundfile as sf
import serum_paths
import probe_conv_size as P
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file
SR = H.SR
FILES = ["Factory/Medium/Baroque Ballroom.flac", "Factory/Cab/Electric Guitar Cab 1.flac", "Factory/Coloration/Woodpecker.flac", "Factory/Long/L90 Hall - Choir Hall.flac",
         "Factory/Long/L90 Room - Large Vocal Space.flac", "Factory/Massive/Sea of Tranquility.flac", "Factory/Medium/Maze.flac", "Factory/Short/L90 Room - Small Chamber.flac",
         "Factory/Weird/Wet Chimes.flac"]


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
        n = copy.deepcopy(node)
        c = n["FXConv"]
        c.pop("embeddedIR", None)
        c.update(relativePathToIR=rel, numFrames=int(len(x)), numChannels=int(x.shape[1]), sampleRate=int(sr), plainParams={"kParamWet": 100.0, "kParamSize": 100.0, "kParamIpTrim": 0.0})
        _, pb = psd(S.render(only(n), [60], 4.0, 3.8))
        gain = 10 * np.log10(np.mean(pb[sel] / pa[sel]))
        e_ch = [float((x[:, i] ** 2).sum()) for i in range(x.shape[1])]
        e_mono = float((x.mean(1) ** 2).sum())
        pk = float(np.abs(x).max())
        out[rel] = {"gain_db": float(gain), "peak": pk, "energy_ch": e_ch, "energy_mono": e_mono, "seconds": len(x) / sr, "channels": int(x.shape[1]), "sr": int(sr)}
        print(f"{rel:50s} {len(x) / sr:5.2f}s ch{x.shape[1]} peak {pk:.3f} E_ch {[round(e, 2) for e in e_ch]} E_mono {e_mono:8.3f}  serum gain {gain:+6.2f} dB  gain-10log(E_mono) {gain - 10 * np.log10(e_mono + 1e-12):+6.2f}", flush=True)
        Path("calibration/probe_conv_norm.json").write_text(json.dumps(out, indent=1))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
