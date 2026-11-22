#!/usr/bin/env python3
"""Checks for converter/conv_deps.py (which presets an update converts again). Needs Serum 2's factory presets on this PC.

  python tools/deps_check.py

1. Caches: a preset's recorded path is the same whatever was converted before it (a cache conv_deps.CACHES does not empty would let a conversion skip work
   another one did, and the skipped code would be missing from its path).
2. Edits, on a copy of the converter: a comment, code in a function the preset never runs and a constant it never names keep it; a statement on its way,
   a constant it names and a data table it reads convert it again.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "converter"))
os.environ["TH_RECORD_DEPS"] = "1"
import conv_deps as CD  # noqa: E402
import import_web as W  # noqa: E402

FACTORY = Path(os.environ.get("TH_SERUM_FACTORY", Path.home() / "OneDrive/Documents/Xfer/Serum 2 Presets/Presets/Factory"))
PLAIN, KIT, OTHER = "BA - Bubba", "KIT - 808 Basic Kit", "PN - Piano Concert Hall"
fails = 0


def check(ok: bool, what: str) -> None:
    global fails
    fails += not ok
    print(("  ok: " if ok else "FAIL: ") + what)


def record(name: str) -> bytes:
    p = next(FACTORY.rglob(name + ".SerumPreset"))
    W.reset()
    W.add_files({p.name: p.read_bytes()})
    r = W.convert("Check")["presets"][0]
    assert r["deps"], r["error"]
    return Path(r["deps"][1]).read_bytes()


def edited(root: Path, rel: str, fn) -> CD.Code:
    """The converter copy at root with one file edited (fn: text -> text), as line tables; the file is put back after."""
    f = root / "converter" / rel
    keep = f.read_text(encoding="utf-8")
    new = fn(keep)
    assert new != keep, f"edit did not apply to {rel}"
    f.write_text(new, encoding="utf-8")
    try:
        return CD.Code(root / "converter")
    finally:
        f.write_text(keep, encoding="utf-8")


def main() -> int:
    if not FACTORY.is_dir():
        print(f"no Serum factory presets at {FACTORY} (set TH_SERUM_FACTORY)")
        return 2
    print("caches")
    a1 = record(PLAIN)
    record(KIT)
    record(OTHER)
    a2 = record(PLAIN)
    check(a1 == a2, f"{PLAIN}: the same path after other presets were converted")
    k1 = record(KIT)
    record(OTHER)
    k2 = record(KIT)
    check(k1 == k2, f"{KIT}: the same path after other presets were converted")
    check(CD.code().unchanged(a1) and CD.code().unchanged(k1), "a path checks out against the code that recorded it")

    print("edits")
    tmp = Path(tempfile.mkdtemp(prefix="deps_check_"))
    try:
        shutil.copytree(REPO / "converter", tmp / "converter", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO / "calibration", tmp / "calibration", ignore=lambda d, names: [n for n in names if not n.endswith(".json") or (Path(d) / n).stat().st_size > 2_000_000])
        c = CD.Code(tmp / "converter")
        check(c.unchanged(a1) and c.unchanged(k1), "a copy of the same code keeps both")
        c = edited(tmp, "deephouse2move.py", lambda s: s.replace("def map_to_tablor(", "# a comment\ndef map_to_tablor(", 1))
        check(c.unchanged(a1) and c.unchanged(k1), "a new comment keeps both")
        c = edited(tmp, "sample_export.py", lambda s: s.replace('    sfz = _root("Multisamples") / sfz_rel', '    _unused = 1\n    sfz = _root("Multisamples") / sfz_rel', 1))
        check(c.unchanged(a1), f"code added in export_multisample keeps {PLAIN} (no multisample)")
        check(not c.unchanged(k1), f"code added in export_multisample converts {KIT} again")
        c = edited(tmp, "sample_export.py", lambda s: s.replace("MULTISAMPLE_PLAYBACK_DB = 7.3", "MULTISAMPLE_PLAYBACK_DB = 7.4", 1))
        check(c.unchanged(a1), f"a constant only multisamples use keeps {PLAIN}")
        check(not c.unchanged(k1), f"a constant only multisamples use converts {KIT} again")
        c = edited(tmp, "deephouse2move.py", lambda s: s.replace("    m = _map_to_tablor(wrapped, pack", "    _unused = 1\n    m = _map_to_tablor(wrapped, pack", 1))
        check(not c.unchanged(a1) and not c.unchanged(k1), "a statement at the start of map_to_tablor converts both again")
        f = tmp / "calibration/level_law.json"
        keep = f.read_text(encoding="utf-8")
        f.write_text(keep.replace("offset_db", "offset_db_x", 1), encoding="utf-8")
        c = CD.Code(tmp / "converter")
        f.write_text(keep, encoding="utf-8")
        check(not c.unchanged(a1), "a changed data table the conversion reads (level_law.json) converts it again")
        c = edited(tmp, "conv_deps.py", lambda s: s.replace("EPOCH = 1", "EPOCH = 2", 1))
        check(not c.unchanged(a1), "raising EPOCH converts everything again")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'DEPS CHECK FAILED' if fails else 'DEPS CHECK PASSED'} ({fails} failures)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
