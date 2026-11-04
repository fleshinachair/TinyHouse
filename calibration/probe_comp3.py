#!/usr/bin/env python3
"""Serum's compressor attack and release times. Attack: a saw switched on into a limiting compressor; the time the gain reduction takes to
reach 63 % of its final value, per Attack setting. Release: a loud saw that drops 15 dB; the time the gain reduction takes to fall to 37 %.
Output: calibration/probe_comp3.json."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H

SR = H.SR
W = 0.004


def env_db(x, t0, t1):
    m = x[0]
    n = int(W * SR)
    a = int(t0 * SR)
    return np.array([10 * np.log10((m[a + i * n:a + (i + 1) * n] ** 2).mean() + 1e-20) for i in range(int((t1 - t0) / W))])


def main():
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t)
    holder = [H.Serum()]
    def render(tree, dur, hold):
        if holder[0].audio_seconds > 40:
            holder[0] = H.Serum()
        return holder[0].render(tree, [84], dur, hold)
    out = {"attack": {}, "release": {}}
    # attack
    for att in (None, 0.5, 5.0, 20.0, 60.0, 90.0):
        t = P.fx_tree(tree0, "Comp", ({"kParamThresh": 0.7, "kParamRatio": 8.0, "kParamRelease": 300.0} | ({"kParamAttack": att} if att is not None else {})), 5)
        t["Oscillator0"]["plainParams"] = {"kParamVolume": 0.6}
        e = env_db(render(t, 1.0, 0.95), 0.0, 0.5)
        e0 = env_db(render(tree0 if False else (lambda q: (q["Oscillator0"].__setitem__("plainParams", {"kParamVolume": 0.6}), q)[1])(H.init_tree(base_t)), 1.0, 0.95), 0.0, 0.5)
        gr = e0 - e
        final = np.median(gr[-40:])
        k = int(np.argmax(gr >= 0.63 * final)) if final > 3 else -1
        out["attack"][str(att)] = {"final_gr_db": float(final), "t63_ms": k * W * 1000 if k >= 0 else None, "gr_first": [round(float(v), 1) for v in gr[:12]]}
        print(f"attack {att}: final GR {final:5.1f} dB, 63% at {k * W * 1000 if k >= 0 else None} ms; GR first windows {[round(float(v), 1) for v in gr[:10]]}", flush=True)
    # release: Env1 (amp) holds then drops
    for rel in (None, 50.0, 150.0, 400.0, 900.0):
        def tr(comp):
            q = H.init_tree(base_t)
            q["Env0"]["plainParams"] = {"kParamAttack": 0.001, "kParamHold": 0.5, "kParamDecay": 0.001, "kParamSustain": 0.12, "kParamRelease": 0.1}
            q["Oscillator0"]["plainParams"] = {"kParamVolume": 0.9}
            return P.fx_tree(q, "Comp", ({"kParamThresh": 0.7, "kParamRatio": 1e6, "kParamAttack": 1.0} | ({"kParamRelease": rel} if rel is not None else {})), 5) if comp else q
        a = env_db(render(tr(True), 2.0, 1.95), 0.45, 1.65)
        b = env_db(render(tr(False), 2.0, 1.95), 0.45, 1.65)
        gr = b - a
        i0 = int(np.argmax(np.diff(b) < -8)) + 2           # the drop (the window after it)
        g0 = gr[i0]
        k = int(np.argmax(gr[i0:] <= 0.37 * g0)) if g0 > 3 else -1
        out["release"][str(rel)] = {"gr_at_drop_db": float(g0), "t37_ms": k * W * 1000 if k >= 0 else None}
        print(f"release {rel}: GR at the drop {g0:5.1f} dB, falls to 37% after {k * W * 1000 if k >= 0 else None} ms", flush=True)
    Path("calibration/probe_comp3.json").write_text(json.dumps(out, indent=1))
    os._exit(0)


main()
