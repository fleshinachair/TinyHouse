#!/usr/bin/env python3
"""What a macro route does to a Comp's own knobs, measured on Serum (the same method as probe_fx_macro.py): a sine through one Comp, the level for a range of stored
values (the static curve), then the same Comp with a route from Macro 1 (at 100 %) of a given amount, and the equivalent static value read off the static curve.

  Makeup       single band, no compression (Thresh 0, a quiet sine): the output gain against the stored Makeup (a linear factor; the converter reads 20 log10)
  Gain0/1/2    multiband, a sine in each band (note 36 / 72 / 108): the band gain in dB against the stored Gain
  Thresh       single band, a loud sine at ratio 4: the output level against the stored Thresh knob

Writes calibration/probe_comp_macro.json and prints the equivalent static values.   python calibration/dev/probe_comp_macro.py [makeup|gain|thresh]
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H

SR = H.SR
IDS = {"kParamMakeup": 6, "kParamThresh": 2, "kParamGain0": 14, "kParamGain1": 15, "kParamGain2": 16}


def rms_db(x, a=0.6, b=1.2):
    m = x[:, int(a * SR):int(b * SR)]
    return float(10 * np.log10((m ** 2).mean() + 1e-20))


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t, table="Analog/Basic Shapes.wav")
    serum = [H.Serum()]

    def run(params, level, note, route=None):
        if serum[0].audio_seconds > 600:
            serum[0] = H.Serum()
        t = P.fx_tree(tree0, "Comp", params, 5)
        t["Oscillator0"]["plainParams"] = {"kParamVolume": level}
        if route:
            param, amount = route
            t["Macro0"]["plainParams"] = {"kParamValue": 100.0}
            t["ModSlot0"] = {"destModuleID": 0, "destModuleParamID": IDS[param], "destModuleParamName": param, "destModuleTypeString": "FXComp",
                             "plainParams": {"kParamAmount": amount}, "source": [25, 0]}
        return rms_db(serum[0].render(t, [note], 1.3, 1.25))

    out = {}
    MB = {"kParamMultiband": 1.0}

    def static_and_routes(name, param, stored, base_params, level, note, routes, unit):
        ref = run(base_params | {param: 0.0} if False else base_params, level, note)
        curve = []
        for v in stored:
            curve.append(run(base_params | {param: v}, level, note))
        out[f"{name}|static"] = {"stored": list(stored), "db": curve, "no_comp_ref_db": run(None, level, note) if False else ref}
        print(f"{name} static ({unit}): " + "  ".join(f"{v:g}:{c:+.1f}" for v, c in zip(stored, curve)), flush=True)
        for v0, amt in routes:
            y = run(base_params | {param: v0}, level, note, (param, amt))
            order = np.argsort(curve)
            eq = float(np.interp(y, np.array(curve)[order], np.array(stored)[order]))
            out[f"{name}|route|{v0}|{amt}"] = {"db": y, "equivalent_stored": eq}
            print(f"  {name} stored {v0:g} + route {amt:+.0f} % x macro 100 %: {y:+.1f} dB = static {eq:.3f}", flush=True)
        Path("calibration/probe_comp_macro.json").write_text(json.dumps(out, indent=1))

    if what in ("makeup", "all"):
        stored = [1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0]
        static_and_routes("makeup", "kParamMakeup", stored, {"kParamThresh": 0.0}, 0.1, 72,
                          [(1.0, 10.0), (1.0, 25.0), (1.0, 50.0), (1.0, 100.0), (2.0, 25.0), (2.0, 50.0), (4.0, 25.0), (1.0, -50.0), (4.0, -50.0)], "factor")
    if what == "makeup2":
        stored = [1.0, 1.1, 1.2, 1.3, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 32.0, 48.0, 64.0, 100.0]
        static_and_routes("makeup2", "kParamMakeup", stored, {"kParamThresh": 0.0}, 0.1, 72,
                          [(1.0, a) for a in (2.0, 4.0, 6.0, 8.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 60.0, 80.0)] + [(2.0, 10.0), (4.0, 10.0), (4.0, 20.0), (8.0, 10.0)], "factor")
    if what in ("gain", "all"):
        for b, note in ((0, 36), (1, 72), (2, 108)):
            stored = [-24.0, -12.0, -6.0, 0.0, 6.0, 12.0, 24.0]
            static_and_routes(f"gain{b}", f"kParamGain{b}", stored, MB | {"kParamThresh": 0.0}, 0.1, note,
                              [(0.0, 10.0), (0.0, 25.0), (0.0, 50.0), (0.0, 100.0), (-12.0, 25.0), (-12.0, 50.0), (6.0, 25.0), (0.0, -50.0)], "dB")
    if what in ("thresh", "all"):
        stored = [0.0, 0.2, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 0.9, 1.0]
        static_and_routes("thresh", "kParamThresh", stored, {"kParamRatio": 4.0}, 1.0, 72,
                          [(0.3, 10.0), (0.3, 25.0), (0.3, 50.0), (0.5, 10.0), (0.5, 25.0), (0.5, -10.0), (0.5, -25.0), (0.7, -10.0), (0.7, -25.0)], "knob")
    sys.stdout.flush(); os._exit(0)


if __name__ == "__main__":
    main()
