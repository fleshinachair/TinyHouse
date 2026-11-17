#!/usr/bin/env python3
"""Bring the sample sets of presets already on the Move up to the converter's current loop handling (loop mode, ping-pong, crossfade; sample_export.py), without
refitting them: each installed preset's source Serum preset is mapped again, its sample sets are exported, and only the smp_set / smp2_set / smp3_set values of the
installed preset are changed (the fit and every other value stay as installed). A preset whose sample slots no longer line up with the source (it was converted by an
older converter) is listed and left alone: it needs a full conversion.

  python converter/refresh_sample_sets.py INSTALLED_DIR OUT_DIR       INSTALLED_DIR: the Move's presets/tinyhouse folder, copied here (banks below it);
                                                            OUT_DIR gets the changed presets (same layout); the new sets are written under out/move/.../Samples
Then ./deploy_samples.sh and the presets from OUT_DIR (deploy_presets.sh with SRC=OUT_DIR/<bank> BANK=<bank>).
"""
import json, os, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
import test_render_calibration as H
import serum_paths
from serum2_preset_loader.codec import decode_preset_file

d2m = H.d2m
KEYS = ("smp_set", "smp2_set", "smp3_set")


def base_of(path: str) -> str:
    """A set's file name without the loop settings the converter adds to it (_L30_60, _P, _X50)."""
    return re.sub(r"(_L\d+_\d+)?(_P)?(_X\d+)?\.tbs$", "", path.rsplit("/", 1)[-1])


_index: dict = {}


def load_source(name: str):
    """The Serum preset an installed preset was made from: the Deep House pack's, or a Factory preset of that name (a file name's stray spaces ignored)."""
    if not _index:
        for base in (H.PRESET_DIR, Path(serum_paths.SERUM_ROOT) / "Presets" / "Factory"):
            for p in base.rglob("*.SerumPreset"):
                _index.setdefault(" ".join(p.stem.split()), p)
    return decode_preset_file(_index[" ".join(name.split())])


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    changed = same = bad = 0
    for f in sorted(src.rglob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        st = d.get("state", "")
        old = {k: (re.search(rf"(?:^|;){k}=([^;]*)", st) or [None, ""])[1] for k in KEYS}
        if not any(old.values()):
            continue
        rel = f.relative_to(src)
        subdir = next(Path(v).parent.name for v in old.values() if v)
        try:
            header, tree = load_source(d["name"])
        except (KeyError, OSError, ValueError) as e:             # no source preset on this machine
            print(f"  skipped (no source) {rel}: {e}"); bad += 1; continue
        m = d2m.map_to_tablor(d2m.wrap_preset(d2m.physical(tree, header)), {}, H.SERUM_TABLES, H.WT_OUT, subdir, _macros=False)
        new = {k: m["params"].get(k, "") for k in KEYS}
        if any(bool(new[k]) != bool(old[k]) or (new[k] and base_of(new[k]) != base_of(old[k])) for k in KEYS):
            print(f"  does not line up with its source, needs a full conversion: {rel}  installed {[base_of(v) for v in old.values() if v]}  now {[base_of(v) for v in new.values() if v]}"); bad += 1
            continue
        if all(new[k] == old[k] for k in KEYS):
            same += 1
            continue
        for k in KEYS:
            if old[k] and new[k] != old[k]:
                st = st.replace(f"{k}={old[k]}", f"{k}={new[k]}", 1) if f";{k}=" in st or st.startswith(f"{k}=") else st
        d["state"] = st
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
        print(f"  changed {rel}: " + ", ".join(f"{k} {Path(old[k]).name} -> {Path(new[k]).name}" for k in KEYS if old[k] and new[k] != old[k]), flush=True)
        changed += 1
    print(f"{changed} presets changed, {same} already current, {bad} left alone")
    sys.stdout.flush(); os._exit(0)


if __name__ == "__main__":
    main()
