#!/usr/bin/env python3
"""How a Serum LFO curve's stored bends (curveVals) shape a segment, rising and falling.

A 4-point custom curve with the structure of a stored preset, x = [0, .1, .5, 1], y = [1, 1, 0, 1] (value 1 - y: 0, 0, 1, 0), runs at 0.5 Hz and moves an oscillator's fine pitch
(25 % unipolar = +50 cents at value 1). The pitch of one harmonic, tracked every 0.1 s, is the LFO value. One entry of curveVals is changed at a time, so the output says which entry
bends which segment (the rising one between x .1 and .5, or the falling one between .5 and 1) and what the exponent is: the value at fraction t of a rising segment is v = t**p, of
a falling one v = 1 - t**q.   Output: calibration/probe_lfo_bend.json and a printed table (the fits p, q per curveVals entry and value).
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

SR, NOTE, K = H.SR, 48, 6
F0 = 440 * 2 ** ((NOTE - 69) / 12)
X, Y = [0.0, 0.1, 0.5, 1.0], [1.0, 1.0, 0.0, 1.0]
CENTS_AT_ONE = 50.0                                         # a 25 % unipolar fine route
PERIOD, STEP = 2.0, 0.1


def track(x):
    x = np.asarray(x).mean(axis=0)
    out = []
    for i in range(int(PERIOD / STEP)):
        seg = x[int(i * STEP * SR):int((i + 1) * STEP * SR + 0.05 * SR)]
        N = 1 << 18
        sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), N)) ** 2
        fr = np.fft.rfftfreq(N, 1 / SR)
        t = F0 * K
        m = (fr > t * 2 ** (-150 / 1200)) & (fr < t * 2 ** (150 / 1200))
        c = 1200 * np.log2(fr[m] / t)
        out.append(float((c * sp[m]).sum() / sp[m].sum()))
    return np.array(out)


def values(S, cv):
    curve = {"xVals": X, "yVals": Y, "curveVals": cv, "numPoints": 3}
    t = P.tree_with(curve=curve, route=("Oscillator", "kParamFine", 0, 5, 25, 0), lfo_params={"kParamRate": 0.5, "kParamBeatSync": 0.0})
    t["Oscillator0"]["plainParams"] = {"kParamVolume": 0.5}
    tr = np.mean([track(S.render(t, [NOTE], PERIOD + 0.2, PERIOD + 0.1)) for _ in range(2)], axis=0)
    return (tr - tr[0]) / CENTS_AT_ONE                      # the LFO value at t = 0, 0.1, ... (0 at the start of the cycle)


def main():
    S = H.Serum()
    res = {}
    base = values(S, [0.5, 0.5, 0.5, 0.5])
    print("straight curve: value at t = 0..1.9 s:", " ".join(f"{v:+.2f}" for v in base))
    for idx in (1, 2, 3):
        for c in (0.3, 0.7, 0.9):
            cv = [0.5, 0.5, 0.5, 0.5]; cv[idx] = c
            v = values(S, cv)
            res[f"{idx}@{c}"] = v.tolist()
            rise = v[1:5]                                   # t = .1 .. .4: the rising segment x .1 .. .5 (period 2 s: x = t / 2)
            fall = v[11:20]
            print(f"curveVals[{idx}] = {c}:  rise (x .1-.5) " + " ".join(f"{a:+.2f}" for a in v[2:10]) + " | fall (x .5-1) " + " ".join(f"{a:+.2f}" for a in v[10:20]), flush=True)
    (ROOT / "calibration/probe_lfo_bend.json").write_text(json.dumps({"straight": base.tolist(), **res}, indent=1), encoding="utf-8")
    os._exit(0)


if __name__ == "__main__":
    main()
