#!/usr/bin/env python3
"""Loss against the cached Serum render and level error (dB) for named presets of one or more preset directories.

  quick_loss.py DIR[,DIR...] NAME [NAME...]
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import fit_chain as C
import fit_level as L
import fit_perceptual as F
import test_render_calibration as H


def main():
    dirs = sys.argv[1].split(",")
    src = {H.d2m.ascii_name(p.stem): p.stem for p in H.PRESET_DIR.glob("*.SerumPreset")}
    print("%-24s" % "preset" + "".join("%20s" % Path(d).name for d in dirs))
    for n in sys.argv[2:]:
        it = C.load_items([src.get(n, n)])[0]
        serum = L.rms_db(it["refs"], it["end"])
        row = "%-24s" % n
        for d in dirs:
            st = json.loads((Path(d) / f"{n}.json").read_text(encoding="utf-8"))["state"]
            rs = [H.run_cli(st, it["notes"], C.DUR, C.HOLD) for _ in range(3)]
            loss = F.loss_of(it["ref_feat"], F.features(rs, it["end"]))["loss"]
            row += "%12.1f (%+5.1f dB)" % (loss, L.rms_db(rs, it["end"]) - serum)
        print(row, flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
