#!/usr/bin/env python3
"""Tally the audible (>=25%) mod routes the converter cannot carry: source x destination, over a folder of Serum presets.   dropped_routes.py DIR"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import collections, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration")); os.chdir(ROOT)
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file
d2m = H.d2m
orig = d2m.map_lfos; rec = []
def wrap(ph, P, other, slot_of, appr, note, *more):
    out = orig(ph, P, other, slot_of, appr, note, *more); rec.append(out); return out
d2m.map_lfos = wrap
tally = collections.Counter(); who = collections.defaultdict(set)
for p in sorted(Path(sys.argv[1]).rglob("*.SerumPreset")):
    rec.clear()
    try:
        h, t = decode_preset_file(p); ph = d2m.physical(t, h)
        v = d2m.wrap_preset(ph); v["preset_name"] = p.stem; v["serum2move"]["name"] = p.stem
        d2m.map_to_tablor(v, {}, H.SERUM_TABLES, ROOT / "out/census_wt", "Census")
    except Exception as e:
        continue
    for o in (rec[-1] if rec else []):
        if abs(o[2]) >= 0.25 and o[1][0] in ("VoiceFilter", "WTOsc", "Oscillator"):
            k = (o[0], o[1][0], o[1][1], "bip" if (len(o) > 3 and o[3]) else "uni"); tally[k] += 1; who[k].add(p.stem + f" {o[2]:+.2f}")
for k, n in tally.most_common(): print(n, k, sorted(who[k])[:12])
