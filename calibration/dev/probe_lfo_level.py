#!/usr/bin/env python3
"""How a Serum LFO route moves an oscillator's volume: a constant-value LFO (a flat curve at value v), unipolar and bipolar,
several base knobs and amounts; the resulting amplitude is turned back into the static knob that gives it, so the move is
measured in Serum's own knob domain and (taking the square root, the engine's knob) compared with the amount.
Output: calibration/probe_lfo_level.json and a printed table.
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import probe_lfo as P
import test_render_calibration as H

SR = H.SR
OSC = ("Oscillator", "kParamVolume", 0, 1)


def amp(x):
    m = x[0, int(0.3 * SR):int(1.0 * SR)]
    return float(np.sqrt((m ** 2).mean()))


def flat(v):
    """A curve that holds the LFO at value v (a stored y is measured down from the top: value = 1 - y)."""
    return {"xVals": [0.0, 1.0], "yVals": [1.0 - v, 1.0 - v], "curveVals": [0.5, 0.5]}


def main():
    s = [H.Serum()]

    def render(base, v=None, amt=0, bip=0):
        t = P.tree_with(curve=flat(v) if v is not None else None, route=(OSC + (amt, bip)) if v is not None else None,
                        lfo_params={"kParamRate": 1.0, "kParamBeatSync": 0.0})
        t["Oscillator0"]["plainParams"] = {"kParamVolume": base}
        if s[0].audio_seconds > 36:
            s[0] = H.Serum()
        return amp(s[0].render(t, [48], 1.2, 1.1, vel=100))
    ks = [0.05, 0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9, 1.0]
    table = {k: render(k) for k in ks}
    print("static knob -> amplitude", {k: round(a, 4) for k, a in table.items()})
    full = table[1.0]

    def knob_of(a):
        return float(np.interp(a, [table[k] for k in ks], ks))
    out = {}
    print("\n  v  pol  base  amount -> knob reached (sqrt domain: base^.5 -> reached^.5, difference / amount)")
    for v in (1.0, 0.5, 0.0):
        for bip in (0, 1):
            for base in (0.04, 0.16, 0.36):
                for amt in (25, 50):
                    a = render(base, v, amt, bip)
                    kn = knob_of(a) if a < 0.97 * full else 1.0
                    d = (kn ** 0.5 - base ** 0.5) / (amt / 100)
                    out[f"v{v}_b{bip}_base{base}_amt{amt}"] = {"amp": a, "knob": kn, "per_unit": d}
                    print(f"  {v:3.1f} {'bi' if bip else 'uni'}  {base:4.2f}  {amt:3d} -> knob {kn:5.3f}   d(sqrt)/amount = {d:+.2f}{'  (clipped)' if kn >= 0.999 else ''}")
                    sys.stdout.flush()
    Path("calibration/probe_lfo_level.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    os._exit(0)


if __name__ == "__main__":
    main()
