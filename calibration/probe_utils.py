#!/usr/bin/env python3
"""Serum's Utils effect: the HPF and LPF knobs (Hz) and the Width knob, measured with noise through one Utils node.

  probe_utils.py filters   transfer of HPF / LPF at several settings in third-octave bands: corner (-3 dB) and slope
  probe_utils.py width     side/mid of a stereo source against Width, and a macro route into Width (amount x macro) against the stored value
Writes calibration/probe_utils_<what>.json."""
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import probe_fx_macro as PM
import fxmetrics as FM
import test_render_calibration as H

GRID = np.geomspace(40.0, 18000.0, 56)


def tf(dry, wet):
    f, p0 = P.psd(dry)
    _, p1 = P.psd(wet)
    out = []
    for c in GRID:
        k = (f > c / 1.09) & (f < c * 1.09)
        out.append(10 * np.log10(p1[k].sum() / p0[k].sum()))
    return np.array(out)


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "filters"
    serum, base = P.start()
    dry = P.render(serum, base)
    out = {}
    if what == "filters":
        for key, fs in (("kParamHPF", (30.0, 100.0, 400.0, 1500.0)), ("kParamLPF", (400.0, 1500.0, 6000.0, 15000.0))):
            for f0 in fs:
                c = tf(dry, P.render(serum, P.fx_tree(base, "Utils", {key: f0}, 12)))
                ref = np.median(c[:6]) if key == "kParamLPF" else np.median(c[-6:])        # the passband
                below = np.where(c < ref - 3.0)[0] if key == "kParamLPF" else np.where(c < ref - 3.0)[0]
                if key == "kParamLPF":
                    k3 = int(below[0]) if below.size else len(c) - 1
                    k12, k24 = (int(np.where(c < ref - d)[0][0]) if (c < ref - d).any() else None for d in (12.0, 24.0))
                else:
                    k3 = int(below[-1]) if below.size else 0
                    k12, k24 = (int(np.where(c < ref - d)[0][-1]) if (c < ref - d).any() else None for d in (12.0, 24.0))
                hz = lambda k: float(GRID[k]) if k is not None else None
                out[f"{key}|{f0}"] = {"passband_db": float(ref), "minus3_hz": hz(k3), "minus12_hz": hz(k12), "minus24_hz": hz(k24), "curve": c.round(2).tolist()}
                print(f"{key} {f0:7.0f} Hz: passband {ref:+5.1f} dB, -3 dB at {hz(k3)} Hz, -12 dB at {hz(k12)}, -24 dB at {hz(k24)}", flush=True)
    if what == "width":
        hyper = {"FXHyperD": {"plainParams": {"kParamWet": 100.0}}, "type": 9}                    # something stereo to narrow or widen (noise is mono)
        def rack(params, route=None):
            t = copy.deepcopy(base)
            t["FXRack0"]["FX"] = [copy.deepcopy(hyper), {"FXUtils": {"plainParams": params}, "type": 12}]
            return PM.with_route(t, "Utils", "kParamWidth", route, dest_id=1) if route is not None else t
        def side_mid(x):
            a = x[:, int(0.4 * H.SR):]
            m, sd = (a[0] + a[1]) / 2, (a[0] - a[1]) / 2
            return float(10 * np.log10((sd ** 2).mean() / (m ** 2).mean() + 1e-20))
        ref = side_mid(P.render(serum, rack({})))
        print(f"default width: side/mid {ref:.1f} dB", flush=True)
        for w in (0.0, 25.0, 50.0, 100.0, 150.0, 200.0, 284.0):
            sm = side_mid(P.render(serum, rack({"kParamWidth": w})))
            out[f"width|{w}"] = {"side_mid_db": sm, "vs_default_db": sm - ref}
            print(f"stored width {w:5.0f}: side/mid {sm:6.1f} dB ({sm - ref:+5.1f} vs default; 20log10(w/100) = {20 * np.log10(max(w, 1e-3) / 100):+5.1f})", flush=True)
        for w0, amt in ((50.0, 25.0), (50.0, 50.0), (100.0, -25.0), (100.0, 25.0), (0.0, 50.0)):
            sm = side_mid(P.render(serum, rack({"kParamWidth": w0}, route=amt)))
            out[f"route|{w0}|{amt}"] = {"side_mid_db": sm, "vs_default_db": sm - ref}
            print(f"width {w0:4.0f} + route {amt:+4.0f} % x macro 100 %: {sm - ref:+6.1f} dB vs default  (the sum read as a width of {100 * 10 ** ((sm - ref) / 20):6.1f} %)", flush=True)
        # the Width knob's own law: from a stored 0 each route amount (at macro 100 %) reads the width at that knob position
        law = {}
        for amt in (10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0):
            sm = side_mid(P.render(serum, rack({"kParamWidth": 0.0}, route=amt)))
            law[amt] = 100 * 10 ** ((sm - ref) / 20)
            print(f"knob position {amt / 100:.1f}: width {law[amt]:7.1f} %", flush=True)
        out["knob_law"] = law
        for w0, amt, mv in ((0.0, 100.0, 50.0), (0.0, 50.0, 100.0), (50.0, 25.0, 100.0)):
            sm = side_mid(P.render(serum, rack({"kParamWidth": w0}, route=amt) if mv == 100.0 else PM.with_route(rack({"kParamWidth": w0}), "Utils", "kParamWidth", amt, value=mv, dest_id=1)))
            out[f"macro|{w0}|{amt}|{mv}"] = 100 * 10 ** ((sm - ref) / 20)
            print(f"width {w0:3.0f} + route {amt:3.0f} % x macro {mv:3.0f} %: {100 * 10 ** ((sm - ref) / 20):7.1f} %", flush=True)
    if what == "route":
        for key, f0s in (("kParamHPF", (60.0, 200.0, 800.0)), ("kParamLPF", (1500.0, 5000.0))):
            for f0 in f0s:
                for amt in (10.0, 25.0, -10.0):
                    t = PM.with_route(P.fx_tree(base, "Utils", {key: f0}, 12), "Utils", key, amt, dest_id=0)
                    c = tf(dry, P.render(serum, t))
                    ref = np.median(c[-6:]) if key == "kParamHPF" else np.median(c[:6])
                    idx = np.where(c < ref - 12.0)[0]
                    if not idx.size:
                        print(f"{key} {f0:6.0f} + route {amt:+4.0f} %: no -12 dB point in 40..18000 Hz (filter out of range)", flush=True); continue
                    k12 = int(idx[-1]) if key == "kParamHPF" else int(idx[0])
                    # the same -12 dB point for the static knob: HPF -12 dB at 0.70 x stated (corner 1.14 x), LPF -12 dB at 2.0 x stated
                    f_eff = float(GRID[k12]) / (0.70 if key == "kParamHPF" else 2.0)
                    out[f"{key}|{f0}|{amt}"] = {"effective_hz": f_eff, "octaves": float(np.log2(f_eff / f0))}
                    print(f"{key} {f0:6.0f} + route {amt:+4.0f} %: behaves as a stated {f_eff:8.0f} Hz ({np.log2(f_eff / f0):+5.2f} octaves)", flush=True)
    Path(ROOT / f"calibration/probe_utils_{what}.json").write_text(json.dumps(out, indent=1))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
