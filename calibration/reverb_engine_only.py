#!/usr/bin/env python3
"""The engine half of reverb_stage_audit.py against the Serum tails stored in reverb_stage_audit.json (Serum's side does not change with the engine, so it is not re-rendered).
Prints the median and mean engine-minus-Serum tail level in each of the three windows, and the worst presets.  usage: reverb_engine_only.py [presets dir]"""
import json, math, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import stage_audit as A
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file
d2m, SR, HOLD, DUR = H.d2m, H.SR, 1.0, 3.2

def tail_levels(x):
    m = x.mean(axis=0)
    lv = lambda a, b: 10 * math.log10((m[int(a * SR):int(b * SR)] ** 2).mean() + 1e-20)
    sus = lv(0.5 * HOLD, HOLD)
    return sus, [lv(HOLD + a, HOLD + b) - sus for a, b in ((0.1, 0.3), (0.3, 0.7), (0.7, 1.5))]

def main():
    ref = json.loads((ROOT / "calibration/reverb_stage_audit.json").read_text())
    sdir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "calibration/g18_final"
    src = {d2m.ascii_name(p.stem): p for p in H.PRESET_DIR.glob("*.SerumPreset")}
    diffs, rows, adds = [], [], []
    for n, r in ref.items():
        f = sdir / f"{n}.json"
        if not f.exists() or n not in src: continue
        h, t = decode_preset_file(src[n])
        notes = H.sounding_notes(t, n)[:1]
        s = json.loads(f.read_text(encoding="utf-8"))["state"]
        for k in ("dly_on", "wide_on", "eq_on", "chor_on", "flan_on", "phas_on", "dly_mix", "dist_mix", "comp_on", "comp_mix", "fxf_on"): s = A.setk(s, k, 0)
        s = A.setk(s, "volume", 30)
        e = [tail_levels(H.run_cli(s, notes, DUR, HOLD)) for _ in range(2)]
        e0 = [tail_levels(H.run_cli(A.setk(s, "rev_mix", 0), notes, DUR, HOLD)) for _ in range(2)]
        adds.append(float(np.mean([x[0] for x in e]) - np.mean([x[0] for x in e0])) - r["serum_sustain_add"])
        en = [float(np.mean([x[1][i] for x in e])) for i in range(3)]
        d = [a - b for a, b in zip(en, r["serum_tail"])]
        diffs.append(d); rows.append((n, d))
    D = np.array(diffs)
    for i in range(3): print(f"window {i}: engine - Serum median {np.median(D[:, i]):+.1f}  mean {D[:, i].mean():+.1f}  rms {np.sqrt((D[:, i] ** 2).mean()):.1f}  (n={len(D)})")
    print(f"sustain level added by the reverb, engine - Serum: median {np.median(adds):+.1f} dB")
    print(f"median abs err per window: {[round(float(np.median(np.abs(D[:, i]))), 1) for i in range(3)]}")
    print("worst:", "; ".join(f"{n[:18]} {max(d, key=abs):+.0f}" for n, d in sorted(rows, key=lambda r: -max(abs(x) for x in r[1]))[:6]))
    sys.stdout.flush(); os._exit(0)

main()
