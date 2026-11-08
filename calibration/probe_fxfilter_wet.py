#!/usr/bin/env python3
"""The Wet law of the Filter effect: noise through one Filter FX (a low-pass of a known pole count) at several Wet values against the dry noise,
the response read in half-octave bands, and the mix law that fits it: linear (1 - w) + w h or the smoothstep s of the other effects, (1 - s^2) + s h.
Writes calibration/probe_fxfilter_wet.json."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import fxmap

GRID = np.array([100, 141, 200, 283, 400, 566, 800, 1131, 1600, 2263, 3200, 4525, 6400, 9051])


def band_db(dry, wet):
    f, p0 = P.psd(dry)
    _, p1 = P.psd(wet)
    out = []
    for c in GRID:
        k = (f > c / 1.09) & (f < c * 1.09)
        out.append(10 * np.log10(p1[k].sum() / p0[k].sum()))
    return np.array(out)


def smooth(w):
    return w * w * (3 - 2 * w)


def main():
    serum, base = P.start()
    dry = P.render(serum, base)
    out = {}
    for ty, poles in (("MgL24", 4), ("MgL12", 2)):
        cut = 0.45
        fp = fxmap.serum_filter_hz(cut)                                # the stated cutoff: each pole is a one-pole low-pass at it (probe_fxfilter_types.py)
        x = GRID / fp
        h = (1.0 / (1.0 + 1j * x)) ** poles
        for wet in (25.0, 50.0, 75.0, 100.0):
            node = {"kParamType": ty, "kParamFreq": cut, "kParamWet": wet, "kParamReso": 0.0}
            m = band_db(dry, P.render(serum, P.fx_tree(base, "Filter", node, 8)))
            w = wet / 100.0
            lin = 20 * np.log10(np.abs((1 - w) + w * h))
            sm = 20 * np.log10(np.abs((1 - smooth(w) ** 2) + smooth(w) * h))
            sm2 = 20 * np.log10(np.abs((1 - smooth(w)) + smooth(w) * h))
            r = lambda y: float(np.sqrt(np.mean((y - m) ** 2)))
            out[f"{ty}|{wet}"] = {"measured": m.round(2).tolist(), "rms_linear": r(lin), "rms_smoothstep_dry_1_s2": r(sm), "rms_smoothstep_dry_1_s": r(sm2)}
            print(f"{ty} wet {wet:5.0f}: rms vs linear mix {r(lin):4.1f} dB, vs smoothstep (1-s^2) {r(sm):4.1f}, vs smoothstep (1-s) {r(sm2):4.1f}   measured {np.round(m[[0, 4, 8, 12]], 1)}", flush=True)
    Path(ROOT / "calibration/probe_fxfilter_wet.json").write_text(json.dumps(out, indent=1))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
