"""Lower each Deep House preset's volume until at most import_pack.SHARE_CAP of its samples reach the engine's limiter shoulder (import_pack.cap_limiter).

The converter sets the master volume in closed form; a preset whose voices and effects come out hotter than Serum's runs into the soft limiter and gains
distortion that Serum's render does not have (Keys - BUNT: volume 52 -> 45, band loss 16.0 -> 8.3). Presets that never reach the shoulder are left as they are.

    python calibration/dev/cap_bank.py SRC_DIR DEST_DIR     (SRC_DIR holds the preset JSONs the converter wrote)
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "calibration"))
import import_pack as IP  # noqa: E402

H, C, PF, d2m = IP.H, IP.C, IP.PF, IP.d2m


def main():
    src, dest = Path(sys.argv[1]), Path(sys.argv[2])
    dest.mkdir(parents=True, exist_ok=True)
    names = {d2m.ascii_name(p.stem): p.stem for p in H.PRESET_DIR.glob("*.SerumPreset")}
    for f in sorted(src.glob("*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        its = C.load_items([names.get(f.stem, f.stem)])
        v0 = int(PF.getk(doc["state"], "volume", 0))
        if its:
            doc["state"] = IP.cap_limiter(doc["state"], its[0])
        v1 = int(PF.getk(doc["state"], "volume", 0))
        (dest / f.name).write_text(json.dumps(doc) + "\n", encoding="utf-8")
        print(f"  {f.stem:30s} volume {v0} -> {v1}", flush=True)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
