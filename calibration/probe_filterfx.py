#!/usr/bin/env python3
"""What does Serum's Filter effect do? A bright saw through it, the response read at the saw's harmonics.

For each setting the output is compared harmonic by harmonic with the same saw without the effect: that ratio, in dB against
frequency, is the filter's response. From it: low-pass / high-pass / other, the frequency where it is 3 dB down, and the slope.
Run for the unwritten (default) type and for each model, at several cutoffs. Output: calibration/probe_filterfx.json
"""
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fxmap
import probe_lfo as P
import test_render_calibration as H

SR = H.SR


def filter_node():
    header, t = H.load_preset("Pad - Reiny")
    for n in t["FXRack0"]["FX"]:
        if fxmap.kind_of(n) == "Filter":
            return copy.deepcopy(n)
    raise SystemExit("no filter node found")


def harmonics(x, f0=65.406):
    m = x[0, int(0.5 * SR):int(2.5 * SR)]
    n = 1 << 16
    sp = np.abs(np.fft.rfft(m[:n] * np.hanning(min(n, len(m))))) if len(m) >= n else None
    f = np.fft.rfftfreq(n, 1 / SR)
    hs, ls = [], []
    for k in range(1, 300):
        fk = f0 * k
        if fk > 19000:
            break
        j = int(round(fk / (SR / n)))
        hs.append(fk); ls.append(sp[j - 3:j + 4].max())
    return np.array(hs), 20 * np.log10(np.array(ls) + 1e-12)


def main():
    s = H.Serum()
    header, base = H.load_preset("Keys - Loop")
    t0 = H.init_tree(base)
    ref_f, ref_l = harmonics(s.render(t0, [36], 3.0, 2.9, vel=100))
    out = {}
    print(f"{'type':10s} {'x':>5s} {'naive Hz':>9s}  {'-3 dB at':>9s} {'-12 dB at':>10s} {'kind':>6s}  slope(dB/oct) 1-2 oct above   level 100 Hz / 4 kHz")
    for ty in (None, "MgL24", "MgL18", "MgL12", "L12", "H18", "H12"):
        for x in (0.45, 0.6, 0.75):
            node = filter_node()
            pp = {"kParamFreq": x, "kParamWet": 100.0}
            if ty:
                pp["kParamType"] = ty
            node["FXFilter"]["plainParams"] = pp
            t = copy.deepcopy(t0)
            t["FXRack0"]["FX"] = [node]
            f, l = harmonics(s.render(t, [36], 3.0, 2.9, vel=100))
            r = l - ref_l[:len(l)]
            r = r - np.mean(r[f < 140]) if ty not in ("H18", "H12") else r - np.mean(r[f > 10000])       # relative to the passband
            def at(hz): return float(np.interp(np.log(hz), np.log(f), r))
            naive = 8.18 * 2697 ** x
            lowpass = at(80) > at(8000) + 6 or (ty in (None,) and at(80) > at(8000) + 6)
            highpass = at(8000) > at(80) + 6
            kind = "LP" if lowpass else "HP" if highpass else "other"
            if lowpass:
                idx3 = np.argmax(r < -3); idx12 = np.argmax(r < -12)
                f3, f12 = (f[idx3] if r[idx3] < -3 else float("nan")), (f[idx12] if r[idx12] < -12 else float("nan"))
                sl = (at(f3 * 2) - at(f3 * 1)) if f3 == f3 else float("nan")
            elif highpass:
                idx3 = len(r) - 1 - np.argmax(r[::-1] < -3); idx12 = len(r) - 1 - np.argmax(r[::-1] < -12)
                f3, f12 = f[idx3], f[idx12]
                sl = (at(f3 * 0.5) - at(f3)) if f3 == f3 else float("nan")
            else:
                f3 = f12 = sl = float("nan")
            out[f"{ty}_{x}"] = {"kind": kind, "f3": float(f3), "f12": float(f12), "naive": naive, "slope": float(sl)}
            print(f"{str(ty):10s} {x:5.2f} {naive:9.0f}  {f3:9.0f} {f12:10.0f} {kind:>6s}  {sl:8.1f}   {at(100):7.1f} / {at(4000):7.1f}", flush=True)
    Path("calibration/probe_filterfx.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    os._exit(0)


if __name__ == "__main__":
    main()
