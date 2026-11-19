#!/usr/bin/env python3
"""Serum 2 presets -> TinyHouse presets, headless: the engine behind the remote UI's Import.

The browser runs this file under Pyodide (the page loads it from the module's bundle), a PC can run it directly. It is the converter's own
mapping (deephouse2move.map_to_tablor) with what a user's machine does not have taken out:
  - no Serum: nothing is measured, so no per-preset fit, no loudness match and no reverb / width fingerprint (they need Serum and the
    native engine); the closed-form laws and the measured tables the converter already carries are used;
  - nothing is dropped: a preset with a gap is still written, and the gap is listed in its report entry so the page can say what is missing.

  import_web.add_files({"Pads/Warm.SerumPreset": bytes, "Tables/Analog/Saw.wav": bytes, "Pack.SerumPack": bytes, ...})
  r = import_web.convert(bank="Imports")           # every preset added so far, or only=[names]
  r["presets"][i]  {name, json, tier, gaps, approx, not_carried, missing_tables, fx, error}
  r["files"]       {path under /data/UserData: base64}   wavetables written by this call, to be uploaded beside the presets

Files named *.SerumPreset are presets; *.wav are wavetables (looked up by the path Serum stored, then by file name); *.SerumPack, *.vitalbank (a Vital bank: a zip of .vital presets) and *.zip are
opened and their presets and wavetables taken the same way. *.vital (Vital presets: plain JSON that carries its own wavetables and sample) are presets too,
converted by vital_import.py (GPL-3.0-or-later: when it is in the bundle, so is the licence text).
"""
from __future__ import annotations

import base64
import io
import json
import re
import tempfile
import zipfile
from pathlib import Path

import deephouse2move as d2m
import sample_export as SE
from serum2_preset_loader.codec import decode_preset_file

try:                                                  # the Vital path is GPL-3.0-or-later and needs numpy: a bundle without it still imports Serum presets
    import vital_import as VI
except Exception:                                    # pragma: no cover
    VI = None

# Samples: the page decodes the audio files the presets name and writes them (as "<name>.dec", see sample_export) under /smp in Pyodide's file system, in the layout of
# Serum's own folder. Only where Python has no soundfile (Pyodide); a PC run reads Serum's folder itself.
SMP = Path("/smp")
if SE.sf is None:
    SE.BROWSER_DIR = SMP

d2m.APPLY_FILTER_FITS = d2m.APPLY_WIDTH_FITS = d2m.APPLY_CHAIN_FITS = d2m.APPLY_TUNE_FITS = d2m.APPLY_STEREO_FITS = d2m.APPLY_LEVEL_FITS = False

_root: Path | None = None
_sent: set[str] = set()
_sent_samples: set[str] = set()
MAX_NAME = 40


def _work() -> Path:
    global _root
    if _root is None:
        _root = Path(tempfile.mkdtemp(prefix="th_import_"))
        for d in ("presets", "tables", "Wavetables"):
            (_root / d).mkdir()
    return _root


def reset() -> None:
    """Forget everything added so far."""
    global _root
    if SE.BROWSER_DIR is not None:
        import shutil
        shutil.rmtree(SMP, ignore_errors=True)
        SE._found.clear()
        SE._standin.clear()
        _sent_samples.clear()
    if _root is not None:
        import shutil
        shutil.rmtree(_root, ignore_errors=True)
    _root = None
    _sent.clear()


def _safe_rel(p: str) -> str:
    parts = [x for x in p.replace("\\", "/").split("/") if x not in ("", ".", "..")]
    return "/".join(parts)


def _store(rel: str, data: bytes) -> None:
    rel = _safe_rel(rel)
    low = rel.lower()
    w = _work()
    if low.endswith(".serumpreset"):
        (w / "presets" / (Path(rel).name[:-len(".SerumPreset")] + ".SerumPreset")).write_bytes(data)      # Pyodide's file system is case-sensitive and the globs below look for this spelling
    elif low.endswith(".vital"):
        (w / "presets" / (Path(rel).name[:-len(".vital")] + ".vital")).write_bytes(data)
    elif low.endswith(".wav"):
        for pre in ("s2 tables/", "tables/"):          # the path Serum stores starts under Tables; a folder pick adds its own prefix
            i = low.find("/" + pre)
            if low.startswith(pre):
                rel = rel[len(pre):]
                break
            if i >= 0:
                rel = rel[i + 1 + len(pre):]
                break
        dest = w / "tables" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    elif low.endswith((".serumpack", ".vitalbank", ".zip")):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                nl = n.lower()
                if n.endswith("/"):
                    continue
                if nl.endswith(PACK_AUDIO):
                    segs = _safe_rel(n).split("/")
                    k = next((i for i, s in enumerate(segs[:-1]) if s.lower() in ("samples", "multisamples", "impulses")), None)
                    if k is not None:                      # the pack's own Samples / Multisamples / Impulses: kept for the page to decode (a sample is not a wavetable)
                        dest = _work() / "packaudio" / "/".join(segs[k:])
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(z.read(n))
                        continue
                if nl.endswith((".serumpreset", ".vital", ".wav")):
                    _store(n, z.read(n))


PACK_AUDIO = (".wav", ".flac", ".aif", ".aiff", ".ogg", ".mp3", ".sfz")


def pack_audio_json() -> str:
    """The sample, multisample and impulse files that came inside packs: [[path under the pack's root, e.g. "Samples/Packs/X/a.wav", where it is in Pyodide's file system]]."""
    base = _work() / "packaudio"
    return json.dumps([[f.relative_to(base).as_posix(), f.as_posix()] for f in sorted(base.rglob("*")) if f.is_file()] if base.is_dir() else [])


def _preset_files() -> list[Path]:
    """The presets added so far, Serum's and Vital's, by name."""
    d = _work() / "presets"
    return sorted(list(d.glob("*.SerumPreset")) + list(d.glob("*.vital")), key=lambda p: (p.stem.lower(), p.suffix))


def add_files(files) -> int:
    """Add files (a dict path -> bytes, as the page reads them). Returns how many presets are now waiting."""
    for rel, data in dict(files).items():
        _store(str(rel), bytes(data))
    return len(_preset_files())


def add_dir(path: str) -> int:
    """Add (and remove) every file under a folder: the page writes what the user picked into Pyodide's file system, which moves bytes without a copy through the proxy layer."""
    root = Path(path)
    for f in sorted(root.rglob("*")):
        if f.is_file():
            _store(f.relative_to(root).as_posix(), f.read_bytes())
            f.unlink()
    return len(_preset_files())


def sample_needs(tree: dict) -> list[dict]:
    """The sample files a preset's oscillators name: [{"kind": "sample" | "multi" | "noise", "rel": path under Serum's Samples / Multisamples}]."""
    out = []
    for i in range(3):
        info = SE.sample_osc(tree, i) or SE.granular_osc(tree, i)       # a Granular oscillator plays a file of its own, asked for the same way
        if info and info.get("file") and not info.get("embedded"):       # audio stored in the preset is never asked of the folder
            out.append({"kind": "multi" if info["multi"] else "sample", "rel": str(info["file"]).replace("\\", "/")})
    for r in ("FXRack0", "FXRack1", "FXRack2"):                # a Convolution reverb's impulse response file (one stored in the preset is never asked of the folder)
        rack = tree.get(r)
        for node in (rack.get("FX", []) if isinstance(rack, dict) else []):
            c = node.get("FXConv") if isinstance(node, dict) else None
            if isinstance(c, dict) and c.get("relativePathToIR") and not c.get("embeddedIR"):
                out.append({"kind": "ir", "rel": str(c["relativePathToIR"]).replace("\\", "/")})
    nz = SE.noise_sample(tree)
    if nz is not None:                                         # a one-shot click or hit
        out.append({"kind": "noise", "rel": str(nz["file"]).replace("\\", "/")})
    else:                                                      # a sample that is not plain white / pink noise is imitated from its own spectrum
        osc3 = tree.get("Oscillator3") if isinstance(tree.get("Oscillator3"), dict) else {}
        node = osc3.get("NoiseOsc3")
        rel = node.get("relativePathToNoiseSample") if isinstance(node, dict) else None
        on = isinstance(osc3.get("plainParams"), dict) and bool(osc3["plainParams"].get("kParamEnable", 0))       # a noise slot that is off (stored as defaults) never sounds: its sample is not asked for
        if rel and on and d2m.noise_type_for(str(rel))[1]:
            out.append({"kind": "noise", "rel": str(rel).replace("\\", "/")})
    return out


def _have_sample(n: dict) -> bool:
    root = "Multisamples" if n["kind"] == "multi" else "Impulses" if n["kind"] == "ir" else "Samples"
    if n["kind"] == "noise":
        return SE.find_sample(n["rel"]) is not None
    return SE._real(SE._root(root) / n["rel"]) is not None


def sfz_audio_json(rels_json: str) -> str:
    """The audio files (paths under Multisamples) the given SFZ files (already added) refer to."""
    out, root = [], SE._root("Multisamples").resolve()
    for rel in json.loads(rels_json):
        f = SE._root("Multisamples") / rel
        if not f.is_file():
            continue
        for r in SE.parse_sfz(f):
            try:
                out.append(Path(r["file"]).resolve().relative_to(root).as_posix())
            except ValueError:
                pass
    return json.dumps(sorted(set(out)))


def new_sample_files_json() -> str:
    """[[path on the Move, path in Pyodide's file system]] for each .tbs sample set written since the last call."""
    out = []
    base = _work() / "Samples"
    if base.is_dir():
        for f in sorted(base.rglob("*.tbs")):
            rel = f.relative_to(base).as_posix()
            if rel not in _sent_samples:
                _sent_samples.add(rel)
                out.append([f"{d2m.MOVE_SAMPLE_ROOT}/{rel}", f.as_posix()])
    return json.dumps(out)


def preset_names() -> list[str]:
    return sorted({p.stem for p in _preset_files()})


def _list(x) -> list:
    return list(x) if x else []


def _plain(critical, notes, fx) -> tuple[list[str], list[str]]:
    """What the preset has that the engine does not carry, in words for a person: (sound gaps, effects left out)."""
    gaps = []
    for x in critical:
        x = re.sub(r"^osc ([ABC]) is a (Sample|MultiSample) oscillator \(.*\)$", lambda m: f"Oscillator {m.group(1)}: its sample was not carried (left silent)", x)
        x = re.sub(r"^osc ([ABC]) is a (\w+) oscillator \(.*\)$", lambda m: f"Oscillator {m.group(1)}: {m.group(2)} oscillators are not imported (left silent)", x)
        x = re.sub(r" \(no FM/RM/fold in Tablor\)", "", x)
        gaps.append(x)
    left = []
    for x in notes:
        if x.startswith("not carried: "):
            left += [f"{k.strip()} effect" for k in x[13:].split(",")]
        elif x.startswith("FX bus") and "not carried" in x and "no Serum fingerprint" not in x:
            left.append(x)
        elif x.startswith("Reverb, delay and width not carried") or "no Serum fingerprint" in x:
            left += [f"{k} effect" for k in ("Reverb" if "Reverb" in fx or "Conv" in fx else "", "Hyper/Dimension" if "HyperD" in fx else "") if k]
        elif x.startswith("Convolution reverb not carried") or x.startswith("Reverb type "):
            left.append(x.replace(" (no measurements for it)", ""))
        elif "not carried" in x:
            left.append(x)
    return gaps, sorted(set(left))


_LEVEL_LAW: dict | None = None


def _level_law(m: dict) -> list:
    """The volume offset of calibration/level_law.json: the converter's starting level runs hot against Serum by a median of 2.7 dB (the per-preset spread cannot be predicted without Serum)."""
    global _LEVEL_LAW
    if _LEVEL_LAW is None:
        f = d2m.REPO / "calibration/level_law.json"
        _LEVEL_LAW = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    off = _LEVEL_LAW.get("offset_db")
    if not off:
        return []
    P = m["params"]
    P["volume"] = max(1, min(127, round(P["volume"] * 10 ** (off / 40.0))))
    return [f"Volume set by a general rule ({off:+.1f} dB), not matched to Serum: adjust by ear"]


VITAL_LEVEL_NOTE = "Volume is not matched to Vital's level (that needs Vital and the native engine): adjust by ear"


def _convert_vital(p: Path, name: str, r: dict, wt_out: Path, subdir: str) -> None:
    """A Vital preset (plain JSON; its wavetables and sample are inside) through vital_import.map_vital into the entry `r`."""
    if VI is None:
        raise RuntimeError("this bundle has no Vital importer")
    d = json.loads(p.read_text(encoding="utf-8"))
    m = VI.map_vital(d, name, wt_out, subdir)
    notes = list(m["notes"])
    silent = [x for x in notes if x.startswith("SILENT")]
    left = [x for x in notes if "not carried" in x and x not in silent]
    r.update(json=json.dumps({"name": name, "module": "tinyhouse", "version": 1, "state": VI.blob(m)}) + "\n",
             tier="vital", gaps=silent, approx=[x for x in notes if x not in silent and x not in left] + [VITAL_LEVEL_NOTE], not_carried=left,
             fx=sorted({x for x in m["carried_fx"] if not x.startswith("osc")}))


def convert(bank: str = "Imports", only=None) -> dict:
    w = _work()
    tables_dir, wt_out = w / "tables", w / "Wavetables"
    pack = {p.name.lower(): p for p in tables_dir.rglob("*.wav")}
    subdir = d2m.ascii_name(bank) or "Imports"
    want = set(only) if only is not None else None
    out, used = [], set()
    for p in _preset_files():
        if want is not None and p.stem not in want:
            continue
        base = d2m.ascii_name(p.stem)[:MAX_NAME].rstrip() or "Preset"
        name, n = base, 2
        while name.lower() in used:
            name, n = f"{base[:MAX_NAME - 3]} {n}", n + 1
        used.add(name.lower())
        r = {"file": p.stem, "name": name, "json": None, "tier": "", "gaps": [], "approx": [], "not_carried": [], "missing_tables": [], "fx": [], "samples": [], "missing_samples": [], "error": ""}
        try:
            if p.suffix == ".vital":
                _convert_vital(p, name, r, wt_out, subdir)
                out.append(r)
                continue
            header, tree = decode_preset_file(p)
            needs = sample_needs(tree)
            r["samples"] = [n for n in needs]
            r["missing_samples"] = [n["rel"] for n in needs if not _have_sample(n)]
            v = d2m.wrap_preset(d2m.physical(tree, header))
            v["preset_name"] = name
            v["serum2move"]["name"] = name
            v = json.loads(json.dumps(v))
            m = d2m.map_to_tablor(v, pack, tables_dir if any(tables_dir.iterdir()) else None, wt_out, subdir)
            m["notes"] += _level_law(m)
            gaps, miss = _plain(m["critical"], m["notes"], m["fx"])
            rule = [("Reverb" if x.startswith("Reverb") else "Width") + " set from Serum's settings by a general rule (not matched by ear)" for x in m["notes"] if x.startswith(("Reverb carried by rule", "Width carried by rule"))]
            rule += ["Volume set by a general rule, not matched to Serum: adjust by ear" for x in m["notes"] if x.startswith("Volume set by a general rule")]
            r.update(json=json.dumps({"name": name, "module": "tinyhouse", "version": 1, "state": d2m.blob(m)}) + "\n",
                     tier=m["tier"], gaps=gaps, approx=_list(m["approx"]) + rule, not_carried=miss,
                     missing_tables=_list(m["missing_tables"]), fx=sorted(set(m["fx"])))
        except Exception as e:                       # one bad file must not stop a bank
            r["error"] = f"{type(e).__name__}: {e}"[:200]
        out.append(r)
    files = {}
    for f in sorted(wt_out.rglob("*")):
        if f.is_file():
            rel = f.relative_to(wt_out).as_posix()
            if rel not in _sent:
                _sent.add(rel)
                files[f"{d2m.MOVE_WT_ROOT}/{rel}"] = base64.b64encode(f.read_bytes()).decode("ascii")
    return {"bank": bank, "subdir": subdir, "presets": out, "files": files}


def convert_json(bank: str = "Imports", only_json: str = "") -> str:
    """convert(), as one JSON string: what the page reads back across the Pyodide boundary."""
    return json.dumps(convert(bank, json.loads(only_json) if only_json else None))


def names_json() -> str:
    return json.dumps(preset_names())


if __name__ == "__main__":
    import sys
    srcs = [Path(a) for a in sys.argv[1:]]
    for s in srcs:
        for f in ([s] if s.is_file() else sorted(s.rglob("*"))):
            if f.is_file() and f.suffix.lower() in (".serumpreset", ".vital", ".wav", ".serumpack", ".vitalbank", ".zip"):
                add_files({str(f.relative_to(s.parent)): f.read_bytes()})
    res = convert("Imports")
    for r in res["presets"]:
        print(r["tier"] or "-", r["name"], "|", "; ".join(r["gaps"] + r["not_carried"]) or "complete", "|", r["error"])
    print(len(res["presets"]), "presets,", len(res["files"]), "wavetables")
