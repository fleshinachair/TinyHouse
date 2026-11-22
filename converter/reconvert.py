#!/usr/bin/env python3
"""Convert on load: the Move converts a preset again from its original when its converter is newer than the one that made it.

The page's import (import_web.py) stamps each preset file it writes with the converter that made it ("conv", a hash of the bundle: build_import_bundle.py) and where
its original is kept on the Move ("src", under import_web.MOVE_SRC_ROOT). When the module is updated its bundle's hash changes, and the engine runs this for a
preset whose stamp differs as it is loaded (and in the background for the rest), so a converter fix reaches presets imported before it without importing them again.

  python3 reconvert.py <preset .json> [...]   -> one line of JSON per preset on stdout: {"path", "status": "same"|"updated"|"current"|"no-source"|"error", "ms", "error"}

The conversion is import_web.convert_one, the page's own, reading what the import left on the Move: the original, the noise / impulse audio it read (decoded by
the page), the wavetables and sample sets it wrote. A preset is rewritten only when its state changes (the stamp always is); the write replaces the file whole
(a temporary file renamed over it), so a reader sees the old preset or the new one, never part of one. A preset without "src" (imported before this, or saved
from a slot) is never touched.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

def background() -> None:
    """Out of the audio's way, as the engine's own worker threads are (src/dsp/rt.h): the lowest priority, and never on core 3, which the Move's SPI
    callback has to itself (the engine starts this on 0-2 already; this keeps it so if it is started some other way). On 0-2 the real-time render
    threads always go first. Done first, before the converter and numpy load (about 1.5 s of the Move's CPU)."""
    try:
        os.nice(19)
    except (AttributeError, OSError):
        pass
    try:
        cores = os.sched_getaffinity(0)
        if 3 in cores and len(cores) > 1:
            os.sched_setaffinity(0, cores - {3})
    except (AttributeError, OSError):
        pass


if "--serve" in sys.argv[1:2]:
    background()

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_lib = HERE / "lib"                          # numpy, shipped with the module (the Move's Python has none)
if _lib.is_dir():
    sys.path.insert(0, str(_lib))
try:
    import zstandard  # noqa: F401
except ImportError:                          # the Move: a stand-in over its own libzstd
    sys.path.insert(0, str(HERE / "shim"))

import deephouse2move as d2m                 # noqa: E402
import import_web as W                       # noqa: E402
import sample_export as SE                   # noqa: E402

SRC_ROOT = Path(os.environ.get("TH_SRC_ROOT", W.MOVE_SRC_ROOT))
WT_ROOT = Path(os.environ.get("TH_WT_ROOT", d2m.MOVE_WT_ROOT))        # the sample sets are beside it: <WT_ROOT>/../Samples/<bank>


def _read_doc(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, text: str) -> None:
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def reconvert(path: Path) -> dict:
    t = time.perf_counter()
    res = {"path": str(path), "status": "error", "ms": 0, "error": ""}
    try:
        doc = _read_doc(path)
        src = doc.get("src")
        if not src:
            res["status"] = "no-source"
            return res
        if doc.get("conv") == W.CONV["hash"]:
            res["status"] = "current"
            return res
        subdir = src.split("/")[0]
        orig = SRC_ROOT / src
        if not orig.is_file():
            res.update(status="no-source", error=f"original not found: {orig}")
            return res
        audio = SRC_ROOT / subdir / "audio"                           # the noise / impulse audio the import kept (Samples/, Impulses/); sample sets are found as written
        if SE.BROWSER_DIR != audio:                                   # another bank's audio: what was found (by name) and measured in the last one does not hold here
            SE._found.clear()
            SE._standin.clear()
            SE.BROWSER_DIR = audio
        tables = WT_ROOT / subdir
        pack = {p.name.lower(): p for p in tables.rglob("*.wav")} if tables.is_dir() else {}
        r = W.convert_one(orig, doc.get("name") or orig.stem, subdir, tables if tables.is_dir() else None, pack, WT_ROOT)
        if r["error"] or not r["json"]:
            res["error"] = r["error"] or "no result"
            return res
        new = json.loads(r["json"])
        new["name"] = doc.get("name", new["name"])                  # the name it has in the store (an import may have numbered it)
        changed = new.get("state") != doc.get("state")
        _write(path, json.dumps(new) + "\n")
        res["status"] = "updated" if changed else "same"
    except Exception as e:                                       # one bad file must not stop the rest
        res["error"] = f"{type(e).__name__}: {e}"[:300]
    finally:
        res["ms"] = round((time.perf_counter() - t) * 1000)
    return res


def main(argv: list[str]) -> int:
    if argv[:1] == ["--serve"]:
        # The engine's helper: one preset path per line on stdin, one result per line on stdout, until stdin closes. Python and numpy start once
        # (about 1.5 s on the Move) for every preset an update leaves stale. A first line {"ready": stamp} says the converter is up. The answers have
        # stdout to themselves: anything the converter prints goes to stderr, so it is never read as an answer.
        out = os.fdopen(os.dup(1), "w", buffering=1)
        os.dup2(2, 1)                                # file descriptor 1 too, for anything that writes there below Python
        sys.stdout = sys.stderr

        def answer(obj) -> None:
            out.write(json.dumps(obj) + "\n")
            out.flush()

        answer({"ready": W.CONV})
        for line in sys.stdin:
            p = line.strip()
            if p:
                answer(reconvert(Path(p)))
        return 0
    for a in argv:
        print(json.dumps(reconvert(Path(a))), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
