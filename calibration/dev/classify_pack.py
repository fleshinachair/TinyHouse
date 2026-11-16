#!/usr/bin/env python3
"""What stops a folder of Serum presets from importing whole? No Serum renders: the converter's own verdict (unsupported_reasons) on each
preset, its blockers grouped, and what removing each group (alone, then greedily) would free. A missing effect fingerprint does not count: the
importer measures it. Output: calibration/classify_<name>.json and a printed summary."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import json
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file

d2m = H.d2m


def group(reason):
    r = re.sub(r"\d+", "#", reason)
    if "no Serum fingerprint" in r:
        return None
    if "Comp is multiband" in r:
        return "multiband Comp"
    if "warp" in r and "unsupported" in r:
        return "wavetable warp (FM / PD / filter / fold ...)"
    if re.search(r"osc \w is a (Sample|MultiSample|Granular)", r):
        return "sample / granular oscillator"
    if re.search(r"osc \w is a ", r):
        return "other oscillator type"
    if r.startswith("filter model"):
        return "filter model with no engine mapping"
    if "Filter FX not carried" in r or r.startswith("not carried: Filter") or "Filter moved by" in r or "Filter model" in r:
        return "Filter effect (moved by a modulator / split / model)"
    if "FX in the two bus racks" in r:
        return "effects in the bus racks"
    if "Split" in r or "Bode" in r:
        return "Split / SplitMS / Bode"
    if "Distortion" in r:
        return "Distortion mode not carried"
    if "Utils" in r:
        return "Utils not carried"
    if "EQ" in r and "Split" in r:
        return "EQ in a Split branch"
    if "not carried: " in r:
        return "other effect not carried"
    if "wavetable" in r:
        return "wavetable Serum cannot open"
    return "other: " + r[:50]


def main():
    src = Path(sys.argv[1])
    name = sys.argv[2] if len(sys.argv) > 2 else src.name
    wt = Path(tempfile.mkdtemp())
    rows = {}
    files = sorted(src.rglob("*.SerumPreset"))
    for i, p in enumerate(files):
        try:
            header, tree = decode_preset_file(p)
        except Exception:
            rows[str(p)] = ["unreadable"]
            continue
        if not H.usable(tree):
            rows[str(p)] = ["wavetable Serum cannot open"]
            continue
        try:
            v = d2m.to_vital(d2m.physical(tree, header))
            v["preset_name"] = p.stem
            v["serum2move"]["name"] = p.stem
            m = d2m.map_to_tablor(v, {}, H.SERUM_TABLES, wt, "x")
        except Exception as e:
            rows[str(p)] = [f"converter error: {str(e)[:40]}"]
            continue
        rows[str(p)] = sorted({g for g in (group(r) for r in d2m.unsupported_reasons(m)) if g})
        if i % 50 == 0:
            print(f"  {i}/{len(files)}", flush=True)
    n = len(rows)
    free = [p for p, g in rows.items() if not g]
    print(f"\n{name}: {n} presets; importable today (fingerprints measured by the importer): {len(free)} = {100 * len(free) / n:.0f} %")
    cnt = Counter(g for gs in rows.values() for g in gs)
    alone = Counter(g for gs in rows.values() if len(gs) == 1 for g in gs)
    print("\nblocker                                                   presets   alone-blocks")
    for g, c in cnt.most_common():
        print(f"  {g:56s} {c:5d}   {alone[g]:5d}")
    blocked = {p: set(g) for p, g in rows.items() if g}
    remaining, done, share = dict(blocked), [], len(free)
    print("\ngreedy order of fixes (cumulative importable):")
    while remaining:
        c2 = Counter(g for gs in remaining.values() for g in gs)
        best = max(c2, key=lambda g: sum(1 for gs in remaining.values() if gs <= {g} | set(done) and g in gs) * 1000 + c2[g])
        done.append(best)
        remaining = {p: gs for p, gs in remaining.items() if not gs <= set(done)}
        share = n - len(remaining)
        print(f"  + {best:54s} -> {share:4d} = {100 * share / n:3.0f} %")
        if len(done) >= 8:
            break
    Path(f"calibration/classify_{name.replace(' ', '_')}.json").write_text(json.dumps(rows, indent=1))
    os._exit(0)


main()
