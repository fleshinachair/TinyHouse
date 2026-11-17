#!/usr/bin/env python3
"""Score installed presets against Serum's own render with import_pack's gate metric (level gap, octave-band error), from the cached Serum renders (Serum is loaded only for a
preset with none). For checking whether engine changes since an install moved a bank's levels.
    score_installed.py SERUM_PRESET_DIR INSTALLED_DIR BANK [--only name,name]  ->  one line per preset, worst first at the end"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]
import json, os, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
import numpy as np
import import_pack as IP
import fit_perceptual as F
H = IP.H; d2m = IP.d2m
from serum2_preset_loader.codec import decode_preset_file
src, inst, bank = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
holder = []
def serum():
    if not holder: holder.append(H.Serum())
    return holder[0]
names = {}
for p in sorted(src.rglob("*.SerumPreset")):
    nm = d2m.ascii_name(p.stem)[:40]
    names.setdefault(nm.lower(), p)
rows = []
for f in sorted(inst.glob("*.json")):
    name = f.stem
    if only and name not in only: continue
    p = names.get(name.lower())
    if p is None: print("no source", name); continue
    try:
        header, tree = decode_preset_file(p)
        notes = H.sounding_notes(tree, name)
        refs = H.reference(serum, f"{bank}_{name}_fx", tree, notes, 2, 4.0, 1.0)
        it = {"name": name, "header": header, "tree": tree, "notes": notes, "refs": refs, "end": F.sounding_end(refs)}
        state = json.loads(f.read_text(encoding="utf-8"))["state"]
        gap, berr = IP.quality(state, it)
    except Exception as e:
        print("error", name, repr(e)[:80]); continue
    rows.append((name, gap, berr)); print("%-40s level %+5.1f dB  bands %5.1f" % (name, gap, berr), flush=True)
print("\nworst level:"); [print("  %-40s %+5.1f  %5.1f" % r) for r in sorted(rows, key=lambda r: -abs(r[1]))[:15]]
print("worst bands:", flush=True); [print("  %-40s %+5.1f  %5.1f" % r) for r in sorted(rows, key=lambda r: -r[2])[:10]]
sys.stdout.flush(); os._exit(0)
