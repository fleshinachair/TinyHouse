#!/usr/bin/env python3
"""What does Serum's Distortion do to a sine, and what engine drive does the same?

A sine through the Distortion effect (wet 100) at several drive settings and modes: the level of the 2nd, 3rd and 5th harmonics
against the fundamental, and the output level. Then the engine's own distortion stage is run on the same sine to find, for each
Serum drive, the engine drive (0..127) whose harmonics match: that table is the unit conversion the converter uses.
Output: calibration/dist_scales.json  {"serum_drive": [...], "engine_drive": {"soft": [...], "asym": [...], "hard": [...]}} and a printed table.
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fxmap
import test_render_calibration as H

SR = H.SR
F0 = 130.81      # note 48


def dist_node():
    header, t = H.load_preset("Bass - AVAION")
    for n in t["FXRack0"]["FX"]:
        if fxmap.kind_of(n) == "Distortion":
            return copy.deepcopy(n)
    raise SystemExit("no distortion node")


def harm(x):
    m = x[0, int(0.5 * SR):int(2.5 * SR)]
    n = 1 << 16
    sp = np.abs(np.fft.rfft(m[:n] * np.hanning(n)))
    f = np.fft.rfftfreq(n, 1 / SR)
    amp = lambda k: sp[int(round(F0 * k / (SR / n))) - 3:int(round(F0 * k / (SR / n))) + 4].max()
    a1 = amp(1)
    return [20 * np.log10(amp(k) / a1 + 1e-9) for k in (2, 3, 5)], 20 * np.log10(np.sqrt((m ** 2).mean()) + 1e-9)


def main():
    s = H.Serum()
    header, base = H.load_preset("Keys - Loop")
    t0 = H.init_tree(base, table="Analog/Basic Shapes.wav", frames=7)
    t0["Oscillator0"]["plainParams"] = {"kParamVolume": 0.5}
    ref_h, ref_l = harm(s.render(t0, [48], 3.0, 2.9, vel=100))
    print(f"input sine: level {ref_l:.1f} dB, 2nd/3rd/5th {['%.0f' % v for v in ref_h]} dB")
    modes = [None, "kSoftClip", "kAsym", "kOverdrive", "kHardClip", "kDiode2", "kSineShaper", "kTapeSat"]
    drives = (10, 25, 50, 75, 100)
    table = {}
    print(f"{'mode':12s} {'drive':>5s}  {'2nd':>6s} {'3rd':>6s} {'5th':>6s}   level change")
    for md in modes:
        for d in drives:
            node = dist_node()
            pp = {"kParamDrive": float(d), "kParamWet": 100.0}
            if md:
                pp["kParamMode"] = md
            node["FXDistortion"]["plainParams"] = pp
            t = copy.deepcopy(t0); t["FXRack0"]["FX"] = [node]
            h, l = harm(s.render(t, [48], 3.0, 2.9, vel=100))
            table[f"{md}_{d}"] = {"h": [float(v) for v in h], "level": float(l - ref_l)}
            print(f"{str(md):12s} {d:5d}  {h[0]:6.1f} {h[1]:6.1f} {h[2]:6.1f}   {l - ref_l:+.1f} dB", flush=True)
    Path("calibration/probe_dist.json").write_text(json.dumps(table, indent=1), encoding="utf-8")
    os._exit(0)


if __name__ == "__main__":
    main()
