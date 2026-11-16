#!/usr/bin/env python3
"""The reverb on the preset's own sound: Serum with only its master Reverb nodes enabled against the engine with only its reverb stage on, the first sounding note held 1 s and
released: the level of the tail at 0.1-0.3, 0.3-0.7 and 0.7-1.5 s after the release, in dB re the sustained level (before release), and the extra sustained level the reverb adds.
The effect audit measures a noise burst; this is the tail of a played note, where a wrong wet level or decay is heard. [names...] limits it. calibration/reverb_stage_audit.json"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, json, math, os, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fxmap
import stage_audit as A
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file
d2m, SR = H.d2m, H.SR
HOLD, DUR = 1.0, 3.2

def tail_levels(x):
    m = x.mean(axis=0)
    def lv(a, b):
        s = m[int(a * SR):int(b * SR)]
        return 10 * math.log10((s ** 2).mean() + 1e-20)
    sus = lv(0.5 * HOLD, HOLD)
    return sus, [lv(HOLD + a, HOLD + b) - sus for a, b in ((0.1, 0.3), (0.3, 0.7), (0.7, 1.5))]

def main():
    src = {d2m.ascii_name(p.stem): p for p in H.PRESET_DIR.glob("*.SerumPreset")}
    sdir = ROOT / "calibration/g15_final"
    names = sys.argv[1:] or sorted(src)
    serum = H.Serum()
    rows = {}
    for n in names:
        f = sdir / f"{n}.json"
        if not f.exists():
            continue
        h, t = decode_preset_file(src[n])
        if not H.usable(t):
            continue
        tt, nk = A.serum_tree(t, h, ["Reverb"])
        if not nk:
            continue
        t0, _ = A.serum_tree(t, h, [])
        notes = H.sounding_notes(t, n)[:1]
        state = json.loads(f.read_text(encoding="utf-8"))["state"]
        try:
            a1 = [serum.render(tt, notes, DUR, HOLD) for _ in range(2)]
            a0 = [serum.render(t0, notes, DUR, HOLD) for _ in range(2)]
        except Exception as err:
            print("  skipped", n, str(err)[:50], flush=True); serum = H.Serum(); continue
        s = state
        for k in ("dly_on", "wide_on", "eq_on", "chor_on", "flan_on", "phas_on", "dly_mix"):
            s = A.setk(s, k, 0)
        s = A.setk(s, "dist_mix", 0); s = A.setk(s, "comp_on", 0); s = A.setk(s, "comp_mix", 0); s = A.setk(s, "fxf_on", 0); s = A.setk(s, "volume", 30)
        s0 = A.setk(s, "rev_mix", 0)
        e1 = [H.run_cli(s, notes, DUR, HOLD) for _ in range(2)]
        e0 = [H.run_cli(s0, notes, DUR, HOLD) for _ in range(2)]
        r1 = [tail_levels(x) for x in a1]; r0 = [tail_levels(x) for x in a0]; q1 = [tail_levels(x) for x in e1]; q0 = [tail_levels(x) for x in e0]
        sv = [np.mean([r[1][i] for r in r1]) for i in range(3)]; sv0 = [np.mean([r[1][i] for r in r0]) for i in range(3)]
        en = [np.mean([r[1][i] for r in q1]) for i in range(3)]; en0 = [np.mean([r[1][i] for r in q0]) for i in range(3)]
        add_s = np.mean([r[0] for r in r1]) - np.mean([r[0] for r in r0]); add_e = np.mean([r[0] for r in q1]) - np.mean([r[0] for r in q0])
        rows[n] = {"serum_tail": [round(x, 1) for x in sv], "engine_tail": [round(x, 1) for x in en], "serum_dry_tail": [round(x, 1) for x in sv0], "engine_dry_tail": [round(x, 1) for x in en0],
                   "serum_sustain_add": round(float(add_s), 1), "engine_sustain_add": round(float(add_e), 1)}
        print("%-26s tail dB re sustain (0.1-0.3 / 0.3-0.7 / 0.7-1.5 s)  serum %s  engine %s   | no-reverb: serum %s engine %s | sustain +%.1f / +%.1f" % (
            n[:26], " ".join("%6.1f" % x for x in sv), " ".join("%6.1f" % x for x in en), " ".join("%6.1f" % x for x in sv0), " ".join("%6.1f" % x for x in en0), add_s, add_e), flush=True)
    Path("calibration/reverb_stage_audit.json").write_text(json.dumps(rows, indent=1))
    sys.stdout.flush(); os._exit(0)

main()
