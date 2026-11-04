#!/usr/bin/env python3
"""Do the per-preset fits hold on notes they were not fitted on?

  check_fits.py [--shift N]

The filter and width fits were made on one set of notes, with the cached Serum render. A fit that
had only learned that pitch would look good there and wrong anywhere else. So for every preset that
got a fit, Serum plays the same patch dry on notes `shift` semitones away (new renders, cached
under their own names), and Tablor plays them with the fits on and with them off. The same loss
as the fit uses is scored, and so are two measures it does not contain: spectral centroid and
the side/mid. Serum is run here, for the new notes only; the plugin's render limit is not reached at this size.
"""
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fit_perceptual as F
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/check_fits.log"
log = H.log
d2m, SR = H.d2m, H.SR


def kept(path):
    p = Path(path)
    return {n for n, r in json.loads(p.read_text(encoding="utf-8")).items() if r.get("kept")} if p.exists() else set()


def pooled_sm(runs, end):
    s = m = 0.0
    for r in runs:
        x = r[:, int(0.01 * SR):int(end * SR)]
        s += (((x[0] - x[1]) / 2) ** 2).sum()
        m += (((x[0] + x[1]) / 2) ** 2).sum()
    return float(10 * np.log10(max(s, 1e-20) / max(m, 1e-20)))


def main():
    shift = int(sys.argv[sys.argv.index("--shift") + 1]) if "--shift" in sys.argv else 5
    filt, wide = kept(d2m.FILTER_FITS), kept(d2m.WIDTH_FITS)
    names = sorted(filt | wide)
    holder = []
    serum = lambda: holder[0] if holder else (holder.append(H.Serum()) or holder[0])
    rows = []
    log(f"{len(names)} presets with a fit, checked on notes shifted by {shift} semitones")
    for n in names:
        header, tree = H.load_preset(n)
        notes = [x + shift for x in H.sounding_notes(tree, n)]
        stripped = H.strip_fx(tree)
        refs = H.reference(serum, n, stripped, notes, 3, 2.0, 1.5)       # 3 x 2.0 s, cached like any other
        end = F.sounding_end(refs)
        ref_feat = F.features(refs, end)
        res = {}
        for label, on in (("off", False), ("on", True)):
            d2m.APPLY_FILTER_FITS = d2m.APPLY_WIDTH_FITS = on
            m = d2m.map_to_tablor(d2m.to_vital(d2m.physical(stripped, header)), {}, H.SERUM_TABLES, H.WT_OUT, "Deep House")
            blob = d2m.blob(m)
            rs = list(F.POOL.map(lambda _: H.run_cli(blob, notes, 2.0, 1.5), range(4)))
            lo = F.loss_of(ref_feat, F.features(rs, end))
            h0, h1 = int(0.01 * SR), int(end * SR)
            cen = lambda xs: float(np.mean([H.centroid(x[:, h0:h1]) for x in xs]))
            res[label] = {**lo, "centroid_st": abs(12 * math.log2(max(cen(rs), 1) / max(cen(refs), 1))),
                          "sm_err": abs(pooled_sm(rs, end) - pooled_sm(refs, end))}
        rows.append((n, res))
        tag = "+".join(t for t, s in (("filter", filt), ("width", wide)) if n in s)
        log(f"  {n:28s} [{tag:12s}] loss {res['off']['loss']:5.1f} -> {res['on']['loss']:5.1f}   "
            f"spec {res['off']['spec']:4.1f} -> {res['on']['spec']:4.1f}   centroid {res['off']['centroid_st']:4.1f} -> {res['on']['centroid_st']:4.1f} st   "
            f"side/mid err {res['off']['sm_err']:4.1f} -> {res['on']['sm_err']:4.1f} dB")
    d2m.APPLY_FILTER_FITS = d2m.APPLY_WIDTH_FITS = True
    log("")
    for label, key, sel in (("filter-fitted: loss", "loss", filt), ("filter-fitted: spectrum dB", "spec", filt),
                            ("filter-fitted: centroid st", "centroid_st", filt), ("width-fitted: side/mid err dB", "sm_err", wide)):
        a = np.array([[r["off"][key], r["on"][key]] for n, r in rows if n in sel])
        if len(a):
            log(f"{label:30s} n={len(a):2d}  median {np.median(a[:, 0]):5.2f} -> {np.median(a[:, 1]):5.2f}   "
                f"mean {a[:, 0].mean():5.2f} -> {a[:, 1].mean():5.2f}   better {int((a[:, 1] < a[:, 0] - 0.3).sum())}, worse {int((a[:, 1] > a[:, 0] + 0.3).sum())}")
    (H.OUT / "check_fits.json").write_text(json.dumps({n: r for n, r in rows}, indent=1), encoding="utf-8")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
