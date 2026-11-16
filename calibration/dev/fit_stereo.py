#!/usr/bin/env python3
"""Per-preset Master Width, from the side/mid difference to Serum's full chain (Patch 7).

  fit_stereo.py [name ...]

band_profile.py found the stereo image the largest remaining mismatch in dB: |side/mid error| averages 10 dB over the 70
presets, Tablor narrower than Serum on average (-4 dB), and the loss weights it at 0.25 so the other fits barely touch it.
The engine now has a Master Width (it scales the side channel: 64 = as is), so the correction is direct: render the
converter's full output, pool the side and mid power over the part that sounds (several renders, because unison phases
are random), and scale the width by the difference in dB. One re-render checks it; a preset is kept only if the check
lands at least MIN_GAIN_DB closer. Output: calibration/stereo_fits.json {preset: {start, fit, ...}}.
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fit_chain as C
import fit_perceptual as F
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/fit_stereo.log"
log = H.log
d2m, SR = H.d2m, H.SR
OUT = H.OUT / "stereo_fits.json"
MIN_ERROR_DB = 2.0       # closer than this to Serum's side/mid: left alone
MIN_GAIN_DB = 1.5        # and a fit must bring it at least this much closer
RENDERS = 6


def pooled_db(runs, end):
    s = m = 0.0
    for r in runs:
        x = r[:, int(0.01 * SR):int(end * SR)]
        s += (((x[0] - x[1]) / 2) ** 2).sum()
        m += (((x[0] + x[1]) / 2) ** 2).sum()
    return float(10 * np.log10(max(s, 1e-20) / max(m, 1e-20)))


def fit_item(it):
    m = C.mapped(it["name"], it["header"], it["tree"])
    P = m["params"]
    start = {"mwidth": P["mwidth"], "monohz": P["monohz"]}
    target = pooled_db(it["refs"], it["end"])

    def measure(width_pot):
        q = copy.deepcopy(m)
        q["params"]["mwidth"] = int(width_pot)
        blob = d2m.blob(q)
        return pooled_db([H.run_cli(blob, it["notes"], C.DUR, C.HOLD) for _ in range(RENDERS)], it["end"])

    before = measure(P["mwidth"])
    err = before - target
    if abs(err) < MIN_ERROR_DB or before < -60 or target < -60:
        return {"kept": False, "target_db": target, "before_db": before, "why": "close enough, or no side image to scale"}
    pot = int(round(min(max(P["mwidth"] * 10 ** (-err / 20.0), 0), 127)))
    after = measure(pot)
    return {"start": start, "fit": {"mwidth": pot}, "target_db": target, "before_db": before, "after_db": after,
            "kept": bool(pot != P["mwidth"] and abs(after - target) <= abs(err) - MIN_GAIN_DB)}


def main():
    given = [a for a in sys.argv[1:] if not a.startswith("--")]
    names = given or sorted(p.stem for p in H.PRESET_DIR.glob("*.SerumPreset"))
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    d2m.APPLY_FILTER_FITS = d2m.APPLY_WIDTH_FITS = d2m.APPLY_CHAIN_FITS = d2m.APPLY_TUNE_FITS = True
    d2m.APPLY_STEREO_FITS = False
    items = [it for it in C.load_items(names) if it["name"] not in data]
    log(f"{len(items)} presets to fit")
    lock = threading.Lock()

    def work(it):
        try:
            rec = fit_item(it)
        except Exception as e:
            rec = {"kept": False, "error": str(e)[:120]}
        with lock:
            data[it["name"]] = rec
            OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
            if "after_db" in rec:
                log(f"  {it['name']:28s} serum {rec['target_db']:+6.1f}  tablor {rec['before_db']:+6.1f} -> {rec['after_db']:+6.1f}  width {rec['start']['mwidth']} -> {rec['fit']['mwidth']}  {'kept' if rec['kept'] else 'not kept'}")
            else:
                log(f"  {it['name']:28s} {rec.get('why') or rec.get('error')}")
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(work, items))
    kept = [n for n, r in data.items() if r.get("kept")]
    log(f"\n{len(kept)} of {len(data)} presets get a stereo fit")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    if "--fresh" in sys.argv and OUT.exists():
        OUT.unlink()
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
