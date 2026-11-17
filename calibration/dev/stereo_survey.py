#!/usr/bin/env python3
"""Does the unison panning law over- or under-spread, and for which stacks?

  stereo_survey.py [--json out.json]

For every preset with a cached dry Serum render: side/mid and L/R correlation of Serum against Tablor
with the FX block off, over the sounding part, with the loudest oscillator's unison count and the
spread the converter sent. Dry on both sides so the FX do not hide the oscillators.
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter"))
os.chdir(ROOT)
import numpy as np
import fit_perceptual as F
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/stereo_survey.log"
log = H.log
SR = H.SR


def stats(runs, end):
    t0, t1 = int(0.01 * SR), int(end * SR)
    sm, co = [], []
    for r in runs:
        x = r[:, t0:t1]
        m, s = (x[0] + x[1]) / 2, (x[0] - x[1]) / 2
        sm.append(10 * np.log10(max((s ** 2).sum(), 1e-20) / max((m ** 2).sum(), 1e-20)))
        l, rr = x[0] - x[0].mean(), x[1] - x[1].mean()
        co.append((l * rr).sum() / max(np.sqrt((l ** 2).sum() * (rr ** 2).sum()), 1e-20))
    return float(np.mean(sm)), float(np.mean(co))


def main():
    H.load_cal("cal_final.json")
    names = sorted(p.stem for p in H.PRESET_DIR.glob("*.SerumPreset"))
    items = F.load_set(names)
    rows = []
    for it in items:
        m = H.d2m.map_to_tablor(H.d2m.wrap_preset(H.d2m.physical(it["tree"], it["header"])), {}, H.SERUM_TABLES, H.WT_OUT, "Deep House")
        P = m["params"]
        blob = H.d2m.blob(m)
        rs = list(F.POOL.map(lambda _: H.run_cli(blob, it["notes"], 2.0, 1.5), range(3)))
        ss, sc = stats(it["refs"], it["end"])
        ts, tc = stats(rs, it["end"])
        # the loudest enabled oscillator decides the stack that matters
        osc = max(((P[f"wt{i}_level"], P[f"wt{i}_uni"], P[f"wt{i}_spread"]) for i in (1, 2, 3) if P[f"wt{i}_level"] > 0), default=(0, 1, 0))
        rows.append({"name": it["name"], "uni": int(osc[1]), "spread": int(osc[2]), "serum_sm": ss, "tablor_sm": ts, "d_sm": ts - ss,
                     "serum_corr": sc, "tablor_corr": tc})
        log(f"  {it['name']:28s} uni {osc[1]:2d} spread {osc[2]:3d}   side/mid serum {ss:+6.1f} tablor {ts:+6.1f} ({ts - ss:+5.1f})   corr serum {sc:+.2f} tablor {tc:+.2f}")
    d = np.array([r["d_sm"] for r in rows])
    log(f"\n{len(rows)} presets: side/mid tablor - serum, mean {d.mean():+.2f} dB, median {np.median(d):+.2f}, abs median {np.median(np.abs(d)):.2f}")
    for lo, hi in ((1, 1), (2, 3), (4, 5), (6, 9), (10, 16)):
        g = [r for r in rows if lo <= r["uni"] <= hi]
        if g:
            x = np.array([r["d_sm"] for r in g])
            log(f"  unison {lo}-{hi}: {len(g):2d} presets, delta mean {x.mean():+5.2f} median {np.median(x):+5.2f} dB, "
                f"spread {x.min():+.1f}..{x.max():+.1f}")
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
