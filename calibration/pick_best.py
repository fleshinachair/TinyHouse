#!/usr/bin/env python3
"""Per preset, ship whichever of two preset sets is closer to Serum's full chain, on notes it was fitted on AND on notes
five semitones away (two independent Serum renders). The new one has to win both and by at least MARGIN on average,
else the old one stays: a refit that is no better is noise, and noise should not replace something measured.

  pick_best.py OLD_DIR NEW_DIR OUT_DIR report_insample.json report_shift5.json
Writes OUT_DIR/*.json and calibration/pick_best.json (the choice and the numbers).
"""
import json
import shutil
import sys
from pathlib import Path

MARGIN = 1.0


def main():
    old, new, out = (Path(a) for a in sys.argv[1:4])
    a = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8"))
    b = json.loads(Path(sys.argv[5]).read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    choice = {}
    for f in sorted(new.glob("*.json")):
        n = f.stem
        # the reports are keyed by the Serum file stem ("Keys -  Zerb"); the files by the converted name
        key = n if n in a else next((k for k in a if k.replace("  ", " ") == n), None)
        if key is None or not (old / f.name).exists():
            shutil.copyfile(f, out / f.name)
            choice[n] = {"pick": "new", "why": "no comparison"}
            continue
        gi = a[key]["before"]["loss"] - a[key]["after"]["loss"]           # positive: new is better
        gs = b[key]["before"]["loss"] - b[key]["after"]["loss"]
        use_new = gi > 0 and gs > 0 and (gi + gs) / 2 >= MARGIN
        shutil.copyfile(f if use_new else old / f.name, out / f.name)
        choice[n] = {"pick": "new" if use_new else "old", "gain_in_sample": round(gi, 2), "gain_shifted": round(gs, 2)}
    Path("calibration/pick_best.json").write_text(json.dumps(choice, indent=1), encoding="utf-8")
    k = sum(1 for c in choice.values() if c["pick"] == "new")
    print(f"{k} of {len(choice)} take the new version, {len(choice) - k} keep the old")


if __name__ == "__main__":
    main()
