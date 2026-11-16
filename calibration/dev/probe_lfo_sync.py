#!/usr/bin/env python3
"""How does Serum read an LFO's stored Rate when kParamBeatSync is NOT written (Lead - Loop's LFO 1: Rate 14.69, Triplets 1, no BeatSync)?
Lead - Loop's own tree (effects stripped; its LFO 1 drives oscillator A's level from knob 0, so the level trace is the LFO) with the LFO's Rate,
Triplets and BeatSync set; the trace's period is read by autocorrelation and given in seconds and in beats at the harness tempo (120 BPM).
Output: calibration/probe_lfo_sync.json and a printed table.
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
import test_render_calibration as H

SR = H.SR
WIN = 0.01


def trace(s, tree, **kw):
    t = copy.deepcopy(tree); pp = t["LFO0"]["plainParams"]
    for k, v in kw.items():
        if v is None: pp.pop(k, None)
        else: pp[k] = v
    if s.audio_seconds > 30:
        raise StopIteration
    x = s.render(t, [55], 6.2, 6.0, vel=100)
    m = x[0, int(0.2 * SR):int(6.0 * SR)]; n = int(WIN * SR)
    return np.array([np.sqrt((m[i:i + n] ** 2).mean()) for i in range(0, len(m) - n, n)])


def period(w):
    d = np.log(w + 1e-6); d = d - d.mean()
    ac = np.correlate(d, d, mode="full")[len(d) - 1:]
    lo, hi = 3, int(2.0 / WIN)
    k = lo
    best = lo + int(np.argmax(ac[lo:hi]))
    # the first strong peak, not a harmonic of a longer one
    for j in range(lo + 1, hi - 1):
        if ac[j] > ac[j - 1] and ac[j] >= ac[j + 1] and ac[j] > 0.6 * ac[best]:
            return j * WIN
    return best * WIN


def main():
    tree = H.strip_fx(H.load_preset("Lead - Loop")[1])
    out = {}
    rates = [0.0072, 0.1, 0.3, 0.79, 1.0, 1.5, 2.0, 2.32, 2.71, 3.0, 3.32, 3.37, 4.0, 5.0, 6.0, 7.0, 8.0, 8.13, 9.0, 10.0, 12.0, 14.69, 17.08, 20.0, 25.0, 30.0, 33.9, 40.0, 50.0, 64.0, 100.0]
    for trip in (None, 1.0):
        for rate in rates:
            s = H.Serum()
            dur = 16.2 if rate < 3.5 else 6.2
            t = copy.deepcopy(tree); pp = t["LFO0"]["plainParams"]; pp["kParamRate"] = rate
            if trip is None: pp.pop("kParamTriplets", None)
            else: pp["kParamTriplets"] = trip
            x = s.render(t, [55], dur, dur - 0.2, vel=100)
            m = x[0, int(0.2 * SR):int((dur - 0.2) * SR)]; n = int(WIN * SR)
            w = np.array([np.sqrt((m[i:i + n] ** 2).mean()) for i in range(0, len(m) - n, n)])
            d = np.log(w + 1e-6); d = d - d.mean()
            ac = np.correlate(d, d, mode="full")[len(d) - 1:]
            hi = min(int(8.0 / WIN), len(ac) - 2)
            best = 3 + int(np.argmax(ac[3:hi])); per = best * WIN
            for j in range(4, hi - 1):
                if ac[j] > ac[j - 1] and ac[j] >= ac[j + 1] and ac[j] > 0.6 * ac[best]:
                    per = j * WIN; break
            out[f"trip{trip}_rate{rate}"] = {"period_s": per, "beats": per * 2.0}
            print(f"triplets {str(trip):4s} rate {rate:8.4f}: period {per * 1000:6.0f} ms = {per * 2.0:6.3f} beats")
            sys.stdout.flush()
    Path("calibration/probe_lfo_sync.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    os._exit(0)


if __name__ == "__main__":
    main()
