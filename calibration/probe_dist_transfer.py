#!/usr/bin/env python3
"""Serum's Distortion as a transfer curve: a sine at four input levels through every mode at several drives (wet 100, LevelOut unwritten), the amplitude of
the fundamental and of the 2nd..5th harmonics at the output, against the same sine without the effect. probe_dist.py measured the harmonics at one level;
the level the signal reaches the stage decides how hard it is driven, so the curve is needed at several.
Also LevelOut: the default mode at drive 50 with LevelOut 0.25 / 0.5 / 0.75 / 1.0, and Wet 50.
Output: calibration/probe_dist_transfer.json  {"ref": {vol: amp}, "rows": [{mode, drive, vol, a_in, amps: [A1..A5]}], "levelout": [...]}  (amplitudes are sine peaks, linear)"""
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
import test_render_calibration as H

SR = H.SR
F0 = 130.81
VOLS = (0.1, 0.25, 0.5, 1.0)
MODES = [None, "kSoftClip", "kAsym", "kOverdrive", "kHardClip", "kDiode2", "kSineShaper", "kTapeSat"]
DRIVES = (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)


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
    k1 = int(round(F0 / (SR / n)))
    def a(k):
        c = int(round(F0 * k / (SR / n)))
        return float(sp[c - 3:c + 4].max())
    return [a(k) for k in (1, 2, 3, 4, 5)]


def main():
    s = H.Serum()
    header, base = H.load_preset("Keys - Loop")
    out = {"ref": {}, "rows": [], "levelout": []}

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
                s = H.Serum()                       # a silent render may be the mode (a diode below its threshold) or the plugin's render limit: retry on a fresh plugin
        return [0.0] * 5

    for v in VOLS:
        out["ref"][str(v)] = render(v, None)[0]
        print("input vol", v, "-> sine peak %.4f" % out["ref"][str(v)], flush=True)
    for md in MODES:
        for d in DRIVES:
            for v in VOLS:
                node = dist_node()
                pp = {"kParamDrive": float(d), "kParamWet": 100.0}
                if md:
                    pp["kParamMode"] = md
                node["FXDistortion"]["plainParams"] = pp
                a = render(v, node)
                out["rows"].append({"mode": md or "default", "drive": d, "vol": v, "a_in": out["ref"][str(v)], "amps": a})
            r = [x for x in out["rows"] if x["mode"] == (md or "default") and x["drive"] == d]
            print("%-12s drive %3d  out/in fundamental: %s" % (md or "default", d, " ".join("%+5.1f" % (20 * np.log10(x["amps"][0] / x["a_in"] + 1e-6)) for x in r)), flush=True)
    for lo in (0.0, 0.25, 0.5, 0.75, 1.0):
        for wet in (100.0, 50.0):
            node = dist_node()
            node["FXDistortion"]["plainParams"] = {"kParamDrive": 50.0, "kParamWet": wet, "kParamLevelOut": lo}
            a = render(0.25, node)
            out["levelout"].append({"levelout": lo, "wet": wet, "vol": 0.25, "a_in": out["ref"]["0.25"], "amps": a})
            print("LevelOut %.2f wet %3.0f: fundamental %+5.1f dB" % (lo, wet, 20 * np.log10(a[0] / out["ref"]["0.25"])), flush=True)
    Path("calibration/probe_dist_transfer.json").write_text(json.dumps(out, indent=1))
    os._exit(0)


if __name__ == "__main__":
    main()
