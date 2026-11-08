#!/usr/bin/env python3
"""Serum's Distortion transfer curve read directly: a 16 Hz sine (MIDI 12, far below the oversampling filters' delay) is rendered with and without the
effect, and the output is plotted against the input sample by sample (binned). A memoryless curve falls on one line, whatever the amplitude.
Usage: probe_dist_curve.py MODE [drive ...]   -> calibration/probe_dist_curve_MODE.json {"drive": {"x": [...], "y": [...], "spread": [...]}}"""
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fxmap
import test_render_calibration as H

SR = H.SR
NOTE = int(os.environ.get('NOTE', 36))


def dist_node():
    header, t = H.load_preset("Bass - AVAION")
    for n in t["FXRack0"]["FX"]:
        if fxmap.kind_of(n) == "Distortion":
            return copy.deepcopy(n)
    raise SystemExit("no distortion node")


def main():
    mode = sys.argv[1]
    drives = [float(a) for a in sys.argv[2:]] or [20, 40, 60, 80, 100]
    s = H.Serum()
    header, base = H.load_preset("Keys - Loop")

    def render(node):
        t = H.init_tree(base, table="Analog/Basic Shapes.wav", frames=7)
        t["Oscillator0"]["plainParams"] = {"kParamVolume": float(os.environ.get("VOL", 1.0))}
        if node is not None:
            t["FXRack0"]["FX"] = [node]
        return s.render(t, [NOTE], 1.6, 1.5, vel=100)[0]

    ref = render(None)
    f0 = 440.0 * 2 ** ((NOTE - 69) / 12.0)
    W = slice(int(0.3 * SR), int(1.4 * SR))

    def phase(x):
        n = np.arange(len(x[W])); return np.angle(np.sum(x[W] * np.exp(-2j * np.pi * f0 * n / SR)))

    def aligned(y):
        """y shifted so its fundamental has the reference's phase (the voice starts at a random phase)"""
        sh = (phase(y) - phase(ref)) / (2 * np.pi * f0) * SR          # samples y is late by
        k = np.arange(len(y))
        c = [np.interp(k + s_, k, y) for s_ in (sh, -sh)]
        e = [np.abs(q[W] - ref[W]).mean() for q in c]          # the sign of the shift: take the one that matches the reference at the linear end
        return c[int(np.argmin(e))]
    out = {}
    for d in drives:
        node = dist_node()
        node["FXDistortion"]["plainParams"] = {"kParamDrive": d, "kParamWet": 100.0, "kParamMode": mode}
        y = aligned(render(node))
        xs, ys = ref[int(0.3 * SR):int(1.4 * SR)], y[int(0.3 * SR):int(1.4 * SR)]
        edges = np.linspace(-np.abs(xs).max(), np.abs(xs).max(), 41)
        idx = np.digitize(xs, edges)
        X, Y, Sp = [], [], []
        for i in range(1, len(edges)):
            m = idx == i
            if m.sum() > 20:
                X.append(float(xs[m].mean())); Y.append(float(ys[m].mean())); Sp.append(float(ys[m].std()))
        out[str(d)] = {"x": X, "y": Y, "spread": Sp}
        print("drive", d, "y peak %.3f  mean bin spread %.4f" % (np.abs(ys).max(), np.mean(Sp)), flush=True)
    Path("calibration/probe_dist_curve_%s.json" % mode).write_text(json.dumps(out, indent=1))
    os._exit(0)


if __name__ == "__main__":
    main()
