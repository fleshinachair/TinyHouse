#!/usr/bin/env python3
"""Pack the browser importer: build/import_bundle.zip.

The remote UI's Import runs the converter (deephouse2move.py and its helpers, ../../import_web.py) under Pyodide. The page downloads this zip
from the module's folder on the Move once per session and unpacks it into Pyodide's file system. It holds:
  - the converter's Python files,
  - the measured data tables it reads (calibration/*.json: filter models, resonance and distortion curves, LFO scales). Per-preset fits and
    fingerprints are left out on purpose: they are keyed by Serum's factory preset names and a user's preset must be mapped by the laws alone,
  - serum2_preset_loader (MIT, by its author) with its licence, to read .SerumPreset files, and cbor2.py, a small reader standing in for the
    compiled cbor2 package Pyodide does not have.
  python tools/build_import_bundle.py [--out build/import_bundle.zip]
"""
import argparse
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PY = ["import_web.py", "deephouse2move.py", "fxmap.py", "fxmetrics.py", "sample_export.py", "serum_paths.py"]
DATA = ["dist_curves", "dist_scales", "filter_models", "filter_res", "fxfilter_drive_probe2", "fxfilter_models_probe", "fx_laws", "fxf_cutoff", "level_law", "lfo_scales", "warp_dist_curves"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HERE.parent / "build/import_bundle.zip")
    a = ap.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    try:
        import serum2_preset_loader
    except ImportError:
        sys.exit("pip install serum2-preset-loader (the bundle vendors it)")
    pkg = Path(serum2_preset_loader.__file__).parent
    lic = next(iter(sorted(pkg.parent.glob("serum2_preset_loader-*/licenses/LICENSE*"))), None)
    n = 0
    with zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in PY:
            z.write(REPO / f, f); n += 1
        for d in DATA:
            # minified: the page downloads this
            z.writestr(f"calibration/{d}.json", json.dumps(json.loads((REPO / "calibration" / f"{d}.json").read_text(encoding="utf-8")), separators=(",", ":"))); n += 1
        for f in sorted(pkg.glob("*.py")):
            z.write(f, f"serum2_preset_loader/{f.name}"); n += 1
        if lic:
            z.write(lic, "serum2_preset_loader/LICENSE"); n += 1
        z.write(HERE / "import_shim/cbor2.py", "cbor2.py"); n += 1
    print(f"{a.out}: {n} files, {a.out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
