#!/usr/bin/env python3
"""How Serum's Distortion Wet mixes: a sine at 0.25 through the default mode at drive 0 / 50 / 100 with Wet 0..100 (LevelOut unwritten and 1.0), the fundamental
and harmonics at the output re the dry sine. Output: calibration/probe_dist_wet.json"""
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import probe_dist_transfer as T
import test_render_calibration as H

def main():
    s = H.Serum()
    header, base = H.load_preset("Keys - Loop")
    out = []
    def render(node, vol=0.25):
        t = H.init_tree(base, table="Analog/Basic Shapes.wav", frames=7)
        t["Oscillator0"]["plainParams"] = {"kParamVolume": vol}
        t["FXRack0"]["FX"] = [node] if node else []
        return T.amps(s.render(t, [48], 1.2, 1.1, vel=100))
    ref = render(None)[0]
    for lo in (None, 1.0):
        for d in (0, 50, 100):
            row = []
            for wet in (0, 25, 50, 75, 100):
                node = T.dist_node()
                pp = {"kParamDrive": float(d), "kParamWet": float(wet)}
                if lo is not None:
                    pp["kParamLevelOut"] = lo
                node["FXDistortion"]["plainParams"] = pp
                a = render(node)
                out.append({"levelout": lo, "drive": d, "wet": wet, "a_in": ref, "amps": a})
                row.append(20 * np.log10(a[0] / ref + 1e-9))
            print("LevelOut %-5s drive %3d  wet 0/25/50/75/100 fundamental: %s" % (lo, d, " ".join("%+6.1f" % x for x in row)), flush=True)
    Path("calibration/probe_dist_wet.json").write_text(json.dumps(out, indent=1))
    os._exit(0)

main()
