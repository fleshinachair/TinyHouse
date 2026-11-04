#!/usr/bin/env python3
"""Presets whose oscillator level comes from an LFO route and which stay short of Serum's level at maximum volume.

  fix_lfo_level.py IN_DIR OUT_DIR

fit_level.py can only raise the volume, and a preset whose oscillator is sounded by an LFO (the oscillator's own level is 0)
can run out of volume before it reaches Serum's loudness. For each preset with an LFO route to a level that is more than 1.5 dB
short, the depth of its level routes is raised by the shortfall (the knob is squared, so gain goes as depth^2: +x dB needs
depth * 10^(x/40)), up to the pot's end, and the result is re-measured, up to four times. Writes OUT_DIR/*.json and
calibration/fix_lfo_level.json.
"""
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fit_chain as C
import fit_level as L
import post_fit as PF
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/fix_lfo_level.log"
log = H.log
LEVEL_DST = {3, 4, 15, 16, 19}


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.glob("*.json"):
        shutil.copyfile(f, dst / f.name)
    srcname = {H.d2m.ascii_name(p.stem): p.stem for p in H.PRESET_DIR.glob("*.SerumPreset")}
    out = {}
    for f in sorted(src.glob("*.json")):
        j = json.loads(f.read_text(encoding="utf-8"))
        st = j["state"]
        routes = [r for r in range(1, 9) if PF.getk(st, f"lr{r}_src", 0) and int(PF.getk(st, f"lr{r}_dst", 0)) in LEVEL_DST]
        if not routes:
            continue
        it = C.load_items([srcname.get(f.stem, f.stem)])[0]
        serum = L.rms_db(it["refs"], it["end"])
        rms = lambda s: L.rms_db([H.run_cli(s, it["notes"], C.DUR, C.HOLD) for _ in range(4)], it["end"])
        first = rms(st)
        short = serum - first
        if short <= 1.5:
            continue
        hist = [round(first - serum, 1)]
        cur = first
        for _ in range(4):
            if serum - cur <= 1.5:
                break
            f_ = 10 ** (min(serum - cur, 30) / 40.0)
            for r in routes:
                a = PF.getk(st, f"lr{r}_amt", 64) - 64
                st = PF.setk(st, f"lr{r}_amt", int(round(min(max(64 + a * f_, 0), 127))))
            cur = rms(st)
            hist.append(round(cur - serum, 1))
        j["state"] = st
        (dst / f.name).write_text(json.dumps(j) + "\n", encoding="utf-8")
        out[f.stem] = {"error_db_history": hist, "routes": {r: int(PF.getk(st, f"lr{r}_amt", 64)) for r in routes}}
        log(f"  {f.stem:26s} RMS against Serum {hist}")
    Path("calibration/fix_lfo_level.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    log(f"{len(out)} presets raised")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
