#!/usr/bin/env python3
"""Serum's Distortion Overdrive (and HardClip for comparison) out to the 15th harmonic: a sine at several input levels through the mode at several drives
(wet 100, LevelOut unwritten). probe_dist_transfer.py only read harmonics 1..5; the engine's hard clip was fitted to those, and Bass - AVAION showed it makes
high-order harmonics Serum does not. Output calibration/probe_dist_od.json {"ref": {vol: amp}, "rows": [{mode, drive, vol, a_in, amps: [A1..A15]}]}.
Usage: probe_dist_od.py [mode ...]   (default kOverdrive kHardClip)"""
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fxmap
import test_render_calibration as H

SR = H.SR
F0 = 130.81
VOLS = (0.1, 0.25, 0.5, 1.0)
DRIVES = (20, 30, 40, 50, 60, 80, 100)
NH = 15


def dist_node():
    header, t = H.load_preset("Bass - AVAION")
    for n in t["FXRack0"]["FX"]:
        if fxmap.kind_of(n) == "Distortion":
            return copy.deepcopy(n)
    raise SystemExit("no distortion node")


def amps(x):
    m = x[0, int(0.3 * SR):int(1.1 * SR)]
    n = 1 << 15
    w = np.hanning(n)
    sp = np.abs(np.fft.rfft(m[:n] * w)) * 2 / w.sum()
    def a(k):
        c = int(round(F0 * k / (SR / n)))
        return float(sp[c - 3:c + 4].max())
    return [a(k) for k in range(1, NH + 1)]


def main():
    s = H.Serum()
    header, base = H.load_preset("Keys - Loop")
    modes = sys.argv[1:] or ["kOverdrive", "kHardClip"]
    out = {"ref": {}, "rows": []}

    def tree(vol, node):
        t = H.init_tree(base, table="Analog/Basic Shapes.wav", frames=7)
        t["Oscillator0"]["plainParams"] = {"kParamVolume": vol}
        if node is not None:
            t["FXRack0"]["FX"] = [node]
        return t

    def render(vol, node):
        nonlocal s
        for attempt in range(2):
            try:
                return amps(s.render(tree(vol, node), [48], 1.2, 1.1, vel=100))
            except Exception:
                s = H.Serum()
        return [0.0] * NH

    for v in VOLS:
        out["ref"][str(v)] = render(v, None)[0]
    for md in modes:
        for d in DRIVES:
            for v in VOLS:
                node = dist_node()
                node["FXDistortion"]["plainParams"] = {"kParamDrive": float(d), "kParamWet": 100.0, "kParamMode": md}
                a = render(v, node)
                out["rows"].append({"mode": md, "drive": d, "vol": v, "a_in": out["ref"][str(v)], "amps": a})
            print(md, d, flush=True)
    Path("calibration/probe_dist_od.json").write_text(json.dumps(out, indent=1))
    os._exit(0)


if __name__ == "__main__":
    main()
