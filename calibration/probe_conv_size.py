#!/usr/bin/env python3
"""What the Convolution reverb's Size and input trim do to a short IR, measured on Serum: noise through one Conv node (Wet 100 %), the power ratio against the same noise without it,
and the IR (resampled to a time stretch s) whose |H|, smoothed in 1/3 octave, matches it. Prints s and the level offset for each (Size, IpTrim); writes calibration/probe_conv_size.json.

  python calibration/probe_conv_size.py [preset.serumpreset]     (default: Synth_Pierre.serumpreset in the repo root, whose Conv embeds a 33 ms microphone IR)
"""
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file

SR = H.SR


def welch(x, nperseg=4096):
    w = np.hanning(nperseg)
    n = (len(x) - nperseg) // (nperseg // 2)
    acc = sum(np.abs(np.fft.rfft(x[i * nperseg // 2:i * nperseg // 2 + nperseg] * w)) ** 2 for i in range(n))
    return np.fft.rfftfreq(nperseg, 1 / SR), acc / n


def smooth(y, f, w=1 / 6):
    out = np.zeros_like(y)
    for i, fc in enumerate(f):
        if fc <= 0:
            out[i] = y[i]
            continue
        m = (f >= fc * 2 ** -w) & (f <= fc * 2 ** w)
        out[i] = 10 * np.log10(np.mean(10 ** (y[m] / 10)))
    return out


def stretched_db(ir, s, f):
    n = len(ir)
    r = np.interp(np.arange(int(n * s) + 1) / s, np.arange(n), ir, right=0)
    h = np.abs(np.fft.rfft(r, 65536))
    return 20 * np.log10(np.interp(f, np.fft.rfftfreq(65536, 1 / SR), h) + 1e-9), float((r ** 2).sum())


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "Synth_Pierre.serumpreset"
    _, t = decode_preset_file(src)
    node = next(n for n in t["FXRack0"]["FX"] if "FXConv" in n)
    ir = np.array(node["FXConv"]["embeddedIR"], dtype=float).ravel()
    print("IR", ir.shape, "energy", float((ir ** 2).sum()), flush=True)

    def only(conv):
        tt = copy.deepcopy(t)
        for r in ("FXRack0", "FXRack1", "FXRack2"):
            tt[r]["FX"] = []
        if conv is not None:
            tt["FXRack0"]["FX"] = [conv]
        for i in (0, 1, 2):
            tt[f"Oscillator{i}"]["plainParams"] = {"kParamEnable": 0.0, "kParamVolume": 0.0}
        tt["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.8}
        return tt
    S = H.Serum()
    psd = lambda a: welch(a[:, int(.5 * SR):int(2.5 * SR)].mean(0))
    f, pa = psd(S.render(only(None), [60], 3.0, 2.8))
    sel = (f > 80) & (f < 14000)
    out = {}
    cases = [(sz, tr) for sz in (5.0, 10.0, 20.0, 35.0, 50.0, 75.0, 100.0) for tr in (-11.86, 0.0)] + [(None, None)]       # (None, None): Size and IpTrim not stored, i.e. Serum's defaults
    for size, trim in cases:
        if True:
            n = copy.deepcopy(node)
            n["FXConv"]["plainParams"] = {"kParamWet": 100.0} if size is None else dict(n["FXConv"]["plainParams"], kParamSize=size, kParamIpTrim=trim, kParamWet=100.0)
            size, trim = (-1.0, -1.0) if size is None else (size, trim)
            _, pb = psd(S.render(only(n), [60], 3.0, 2.8))
            meas = smooth(10 * np.log10((pb + 1e-20) / (pa + 1e-20)), f)
            best = (1e9, None, None, None)
            for s in np.geomspace(0.02, 4.0, 260):
                hdb, en = stretched_db(ir, s, f)
                hdb = smooth(hdb, f)
                d = meas[sel] - hdb[sel]
                off = float(d.mean())
                err = float(np.sqrt(np.mean((d - off) ** 2)))
                if err < best[0]:
                    best = (err, float(s), off, en)
            err, s, off, en = best
            out[f"{size:g}|{trim:g}"] = {"stretch": s, "offset_db": off, "rms_err_db": err, "stretched_energy": en}
            print(f"Size {size:5.1f} IpTrim {trim:+6.2f}: stretch {s:.4f}  offset {off:+6.2f} dB  fit rms {err:.2f} dB  (stretched energy {en:.4f})", flush=True)
            Path("calibration/probe_conv_size.json").write_text(json.dumps(out, indent=1))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
