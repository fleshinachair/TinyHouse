#!/usr/bin/env python3
"""Pack the browser importer: build/import_bundle.zip.

The remote UI's Import runs the converter (deephouse2move.py and its helpers, ../../converter/import_web.py) under Pyodide. The page downloads this zip
from the module's folder on the Move once per session and unpacks it into Pyodide's file system. It holds:
  - the converter's Python files,
  - the measured data tables it reads (calibration/*.json: filter models, resonance and distortion curves, LFO scales). Per-preset fits and
    fingerprints are left out on purpose: they are keyed by Serum's factory preset names and a user's preset must be mapped by the laws alone,
  - serum2_preset_loader (MIT, by its author) with its licence, to read .SerumPreset files, and cbor2.py, a small reader standing in for the
    compiled cbor2 package Pyodide does not have.
  python tools/build_import_bundle.py [--out build/import_bundle.zip]
"""
import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PY = ["import_web.py", "reconvert.py", "conv_deps.py", "deephouse2move.py", "fxmap.py", "fxmetrics.py", "sample_export.py", "serum_paths.py"]
VITAL = ["vital_import.py", "vital_morph.py", "vital_wtmod.py", "vital_pd_table.npz"]     # the Vital import: GPL-3.0-or-later, so the bundle carries the licence text and the notice with it (--no-vital leaves it out)
VITAL_DOCS = ["LICENSE-GPL-3.0", "THIRD-PARTY.md"]
DATA = ["dist_curves", "dist_scales", "dist_table_modes", "filter_models", "filter_res", "fxfilter_drive_probe2", "fxfilter_models_probe", "fx_laws", "fxf_cutoff", "level_law", "lfo_scales", "warp_curve_modes", "warp_dist_curves"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HERE.parent / "build/import_bundle.zip")
    ap.add_argument("--no-vital", action="store_true", help="leave the GPL Vital importer out (Serum presets only)")
    a = ap.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    try:
        import serum2_preset_loader
    except ImportError:
        sys.exit("pip install serum2-preset-loader (the bundle vendors it)")
    pkg = Path(serum2_preset_loader.__file__).parent
    lic = next(iter(sorted(pkg.parent.glob("serum2_preset_loader-*/licenses/LICENSE*"))), None)
    entries: list[tuple[str, bytes]] = []                 # (name in the zip, bytes)
    for f in PY:
        entries.append((f, (REPO / "converter" / f).read_bytes()))
    if not a.no_vital:
        for f in VITAL:
            entries.append((f, (REPO / "converter" / f).read_bytes()))
        for f in VITAL_DOCS:
            entries.append((f"licenses/{f}", (REPO / f).read_bytes()))
    for d in DATA:
        # minified: the page downloads this
        entries.append((f"calibration/{d}.json", json.dumps(json.loads((REPO / "calibration" / f"{d}.json").read_text(encoding="utf-8")), separators=(",", ":")).encode()))
    for f in sorted(pkg.glob("*.py")):
        entries.append((f"serum2_preset_loader/{f.name}", f.read_bytes()))
    if lic:
        entries.append(("serum2_preset_loader/LICENSE", lic.read_bytes()))
    entries.append(("cbor2.py", (HERE / "import_shim/cbor2.py").read_bytes()))
    entries.append((CONV_VERSION_FILE, json.dumps(conv_version(entries)).encode()))
    with zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in entries:
            z.writestr(name, data)
    print(f"{a.out}: {len(entries)} files, {a.out.stat().st_size // 1024} KB, converter {entries[-1][1].decode()}")


CONV_VERSION_FILE = "conv_version.json"


def release_version() -> str:
    """The module's version (gen_params.py VERSION), read as text: importing gen_params would generate the param tables."""
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', (HERE / "gen_params.py").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "dev"


def conv_version(entries: list[tuple[str, bytes]]) -> dict:
    """What a converted preset is stamped with: the release, and a hash of everything in the bundle that can change a conversion (the code and the data tables, not the
    licence texts). The Move reconverts a preset whose stamp differs from its own bundle's (convert on load), so the hash changes by itself whenever the converter does:
    there is no number to remember to bump. Line endings are made LF first, so a checkout with CRLF files gives the same hash."""
    h = hashlib.sha1()
    for name, data in sorted(entries):
        if name.startswith("licenses/") or name.endswith("LICENSE"):
            continue
        h.update(name.encode() + b"\0" + data.replace(b"\r\n", b"\n") + b"\0")
    return {"version": release_version(), "hash": h.hexdigest()[:12]}


if __name__ == "__main__":
    main()
