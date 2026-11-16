#!/usr/bin/env python3
"""Serum against the engine on presets with FX buses: the full chain (effects racks as stored) and the engine's conversion of it, with the bus sends carried
(deephouse2move.CARRY_BUSES on) and without, the first sounding note at the converter's closed-form volume (no loudness fit). Prints the level difference (engine - Serum,
dB) and the octave-band differences for each, so what the buses change is visible against Serum.

  bus_audit.py DIR [name-substring ...] [--max N] [--serial]     (--serial: instead the presets with Filter 1 in series into Filter 2)

Presets without a send to a bus (and without a None / Direct source route or a filter-output route) are skipped. Output: printed, and calibration/bus_audit.json."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, json, math, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import import_pack as IP
import test_render_calibration as H
import voice_survey as VS
from serum2_preset_loader.codec import decode_preset_file
d2m, SR = H.d2m, H.SR


def level_db(xs):
    return 10 * math.log10(np.mean([(x.mean(axis=0)[int(0.15 * SR):int(1.2 * SR)] ** 2).mean() for x in xs]) + 1e-20)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mx = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 10 ** 6
    src = Path(args[0])
    subs = [a.lower() for a in args[1:] if not a.isdigit()]
    files = [p for p in sorted(src.rglob("*.SerumPreset")) if not subs or any(s in p.stem.lower() for s in subs)]
    meas, serum, rows = [], H.Serum(), {}
    for p in files:
        if len(rows) >= mx:
            break
        try:
            header, tree = decode_preset_file(p)
        except Exception:
            continue
        ph = d2m.physical(tree, header)
        if "--serial" in sys.argv:
            if not (ph["filter_dest"].get(5) == 3 and ph["filter2_enabled"] and ph["filter"]["enabled"]) or not H.usable(tree):
                continue
        elif not (any(any(v) for v in ph["bus"].values()) or any(ph["route_none"].values()) or any(ph["route_out"].values()) or any(ph["filter_dest"].values())) or not H.usable(tree):
            continue
        name = d2m.ascii_name(p.stem)[:40]
        notes = H.sounding_notes(tree, name)[:1]
        try:
            IP.measure_fingerprints(name, tree, meas)
            a = [serum.render(tree, notes, 1.5, 1.2) for _ in range(2)]
        except Exception as err:
            print("  skipped", name, str(err)[:60], flush=True); serum = H.Serum(); continue
        sb = np.mean([VS.bands(x) for x in a], axis=0)
        sl = level_db(a)
        row = {"serum_db": round(sl, 1)}
        for label, on in (("off", False), ("on", True)):
            d2m.CARRY_BUSES = on
            v = d2m.to_vital(ph); v["preset_name"] = name; v["serum2move"]["name"] = name
            try:
                m = d2m.map_to_tablor(v, {}, H.SERUM_TABLES, H.WT_OUT, "Bus audit")
            except Exception as err:
                print("  cannot convert", name, repr(err)[:60], flush=True); row = None; break
            e = [H.run_cli(d2m.blob(m), notes, 1.5, 1.2) for _ in range(3)]
            eb = np.mean([VS.bands(x) for x in e], axis=0)
            row[label] = {"diff_db": round(level_db(e) - sl, 1), "bands": [round(float(x), 1) for x in ((eb - eb.max()) - (sb - sb.max()))]}
            if on:
                row["notes"] = [n for n in m["notes"] if "bus" in n.lower()]
        if row is None or "on" not in row:
            continue
        rows[name] = row
        print("%-30s serum %+6.1f | engine - serum: buses off %+5.1f  on %+5.1f dB | band err off %4.1f on %4.1f dB rms" % (
            name[:30], sl, row["off"]["diff_db"], row["on"]["diff_db"],
            math.sqrt(np.mean(np.square(row["off"]["bands"]))), math.sqrt(np.mean(np.square(row["on"]["bands"])))), flush=True)
        for n in row["notes"]:
            print("      ", n[:150], flush=True)
    Path("calibration/bus_audit.json").write_text(json.dumps(rows, indent=1))
    if rows:
        for k in ("off", "on"):
            print("%-4s mean |level diff| %.1f dB   mean band rms %.1f dB" % (k, np.mean([abs(r[k]["diff_db"]) for r in rows.values()]),
                  np.mean([math.sqrt(np.mean(np.square(r[k]["bands"]))) for r in rows.values()])))
    sys.stdout.flush(); os._exit(0)


main()
