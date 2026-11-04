#!/usr/bin/env python3
"""Does capping the mapped EQ boost move the engine toward Serum's full chain, or just darken it?

  eq_cap_test.py [cap_db]      default 12

For every preset whose mapped EQ has a bell/shelf boost above the cap, play the converter's output
with the boost as mapped and with it capped, against the cached full-chain Serum render. Level-free
measures only (band levels re the render's total): the signed error of the 3-8 kHz and >8 kHz bands
(Tablor minus Serum, dB), the full-chain fit loss, and the 1-3 kHz error as a check that the cap does
not just darken everything. Output: calibration/eq_cap_test.json
"""
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fit_chain as C
import fit_perceptual as F
import fxmap
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/eq_cap_test.log"
log = H.log
d2m, SR = H.d2m, H.SR
BANDS = {"1-3k": (1000, 3000), "3-8k": (3000, 8000), ">8k": (8000, 20000)}


def band_levels(runs):
    pw, n, w = 0, 8192, np.hanning(8192)
    for x in runs:
        M = (x[0] + x[1]) / 2
        for i in range(0, len(M) - n, n // 2):
            pw = pw + np.abs(np.fft.rfft(M[i:i + n] * w)) ** 2
    f = np.fft.rfftfreq(n, 1 / SR)
    tot = pw[(f >= 20) & (f < 20000)].sum()
    return {k: float(10 * np.log10(pw[(f >= lo) & (f < hi)].sum() / tot + 1e-12)) for k, (lo, hi) in BANDS.items()}


def capped(m, cap_pot):
    q = copy.deepcopy(m)
    hit = 0
    for n in range(1, fxmap.EQ_SLOTS + 1):
        if q["params"][f"eq{n}_type"] in (fxmap.EQ_BELL, fxmap.EQ_LOSHELF, fxmap.EQ_HISHELF) and q["params"][f"eq{n}_gain"] > cap_pot:
            q["params"][f"eq{n}_gain"] = cap_pot
            hit += 1
    return q, hit


def main():
    cap = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
    cap_pot = fxmap.pot_gain(cap)
    d2m.APPLY_CHAIN_FITS = False
    items = C.load_items(sorted(p.stem for p in H.PRESET_DIR.glob("*.SerumPreset")))
    rows = []
    for it in items:
        m = C.mapped(it["name"], it["header"], it["tree"])
        q, hit = capped(m, cap_pot)
        if not hit:
            continue
        ref = band_levels(it["refs"])
        res = {}
        for lab, mm in (("asis", m), ("capped", q)):
            blob = d2m.blob(mm)
            rs = list(F.POOL.map(lambda _: H.run_cli(blob, it["notes"], C.DUR, C.HOLD), range(4)))
            b = band_levels(rs)
            res[lab] = {"loss": F.loss_of(it["ref_feat"], F.features(rs, it["end"]))["loss"], **{k: b[k] - ref[k] for k in b}}
        rows.append({"name": it["name"], "slots": hit, **res})
        a, c = res["asis"], res["capped"]
        log(f"  {it['name']:28s} slots {hit}  loss {a['loss']:5.1f} -> {c['loss']:5.1f}   " +
            "   ".join(f"{k} {a[k]:+5.1f} -> {c[k]:+5.1f}" for k in BANDS))
    for key in ("loss", "1-3k", "3-8k", ">8k"):
        a = np.array([[r["asis"][key], r["capped"][key]] for r in rows])
        if key == "loss":
            log(f"\nn={len(rows)}  loss mean {a[:, 0].mean():.2f} -> {a[:, 1].mean():.2f}   better {int((a[:, 1] < a[:, 0] - 0.3).sum())}, worse {int((a[:, 1] > a[:, 0] + 0.3).sum())}")
        else:
            log(f"{key:5s} signed error mean {a[:, 0].mean():+5.1f} -> {a[:, 1].mean():+5.1f} dB   |error| {np.abs(a[:, 0]).mean():5.1f} -> {np.abs(a[:, 1]).mean():5.1f}   closer {int((abs(a[:, 1]) < abs(a[:, 0]) - 0.5).sum())}, further {int((abs(a[:, 1]) > abs(a[:, 0]) + 0.5).sum())}")
    (H.OUT / "eq_cap_test.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
