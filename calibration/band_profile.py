#!/usr/bin/env python3
"""Where do the installed presets still differ from Serum's full chain, band by band?

  band_profile.py [presets_dir]        default out/move/data/UserData/schwung/presets/tinyhouse

Per preset: Tablor minus Serum, in dB, for each third-octave band (re the render's total energy, over the part
that sounds), plus the FX racks it has. Output: calibration/band_profile.json and a summary in the log: the median
and spread of the error per band across all presets and by preset type, and how much of the spectral error a
single global curve (the per-band median) would remove -- the measure of whether a global fix is worth making.
"""
import json
import os
import re
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

H.LOG_PATH = ROOT / "calibration/band_profile.log"
log = H.log
d2m, SR = H.d2m, H.SR
BANDS = H._BANDS


def window(runs, end):
    a, b = int(0.01 * SR), int(end * SR)
    return [r[:, a:b] for r in runs]


def main():
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "out/move/data/UserData/schwung/presets/tinyhouse"
    src = {d2m.ascii_name(p.stem): p.stem for p in H.PRESET_DIR.glob("*.SerumPreset")}
    rows = {}
    for f in sorted(d.glob("*.json")):
        n = src.get(f.stem, f.stem)
        state = json.loads(f.read_text(encoding="utf-8"))["state"]
        its = C.load_items([n])
        if not its:
            continue
        it = its[0]
        tab = window([H.run_cli(state, it["notes"], C.DUR, C.HOLD) for _ in range(3)], it["end"])
        ref = window(it["refs"], it["end"])
        bt = np.mean([H.band_db(x) for x in tab], axis=0)
        br = np.mean([H.band_db(x) for x in ref], axis=0)
        nodes = it["tree"].get("FXRack0", {}).get("FX", []) or []
        kinds = sorted({fxmap.kind_of(x) for x in nodes if fxmap.enabled(x)})
        uni = [int(m) for m in re.findall(r"wt\d_uni=(\d+)", state)]
        rows[f.stem] = {"err": [None if not (np.isfinite(a) and np.isfinite(b)) else float(a - b) for a, b in zip(bt, br)],
                        "ref": [None if not np.isfinite(b) else float(b) for b in br],
                        "fx": kinds, "type": f.stem.split(" - ")[0], "uni": max(uni) if uni else 1,
                        "loss": F.loss_of(it["ref_feat"], F.features([H.run_cli(state, it["notes"], C.DUR, C.HOLD) for _ in range(2)], it["end"]))}
        log(f"  {f.stem:28s} loss {rows[f.stem]['loss']['loss']:5.1f} (spec {rows[f.stem]['loss']['spec']:4.1f} env {rows[f.stem]['loss']['env']:4.1f} sm {rows[f.stem]['loss']['ms']:4.1f})")
    (H.OUT / "band_profile.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
