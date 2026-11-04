#!/usr/bin/env python3
"""Census of a folder of Serum presets: convert each with the converter (no Serum renders), tally what it cannot carry and what it approximates, and the FX / filter /
warp / source usage across the folder. calibration/census_<name>.json; the ranked tallies are printed.   census.py DIR [--name factory] [--limit N]"""
import collections, json, os, sys, traceback
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import fxmap
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file
d2m = H.d2m

def norm(msg):
    import re
    m = re.sub(r"\d+(\.\d+)?", "#", msg)
    return m[:110]

def main():
    a = sys.argv[1:]
    src = Path(a[0]); name = a[a.index("--name") + 1] if "--name" in a else "census"
    limit = int(a[a.index("--limit") + 1]) if "--limit" in a else 0
    files = sorted(src.rglob("*.SerumPreset"))
    if limit: files = files[:limit]
    tally = collections.Counter(); crit = collections.Counter(); appr = collections.Counter(); tiers = collections.Counter(); fxk = collections.Counter(); filt = collections.Counter()
    warps = collections.Counter(); srcs = collections.Counter(); osck = collections.Counter(); bus = 0; errors = []; rows = {}
    for p in files:
        try:
            h, t = decode_preset_file(p)
            ph = d2m.physical(t, h)
            nm = str(p.relative_to(src))
            v = d2m.to_vital(ph); v["preset_name"] = p.stem; v["serum2move"]["name"] = p.stem
            m = d2m.map_to_tablor(v, {}, H.SERUM_TABLES, ROOT / "out/census_wt", "Census")
        except Exception as e:
            errors.append((str(p.name), repr(e)[:100])); tiers["error"] += 1; continue
        tiers[m["tier"]] += 1
        why = d2m.unsupported_reasons(m)
        for x in m["critical"]: crit[norm(x)] += 1
        for x in m["approx"]: appr[norm(x)] += 1
        for x in m["notes"]:
            if "not carried" in x or "not" in x[:30]: tally[norm(x)] += 1
        for x in ph["fx"] if isinstance(ph.get("fx"), list) else []: fxk[str(x)] += 1
        for o in ph["oscs"]:
            if o["enabled"]:
                osck[o["kind"]] += 1
                for w in o["warp"]:
                    if w["amount"] >= 0.02: warps[w["mode"]] += 1
        f = ph["filter"]
        if f["enabled"]: filt[f["model"]] += 1
        if ph["filter2_enabled"]: filt["F2:" + ph["filter2"]["model"]] += 1
        for mm in ph["mods"]:
            if 16 <= mm["source"] <= 24 or mm["source"] == 1: srcs[mm["source"]] += 1
        bus += int(bool(ph.get("bus_fx")))
        rows[nm] = {"tier": m["tier"], "why": why[:6], "bus_fx": ph.get("bus_fx", 0), "path": str(p)}
    out = {"n": len(files), "tiers": dict(tiers), "critical": crit.most_common(40), "approx": appr.most_common(30), "notes": tally.most_common(30), "fx": fxk.most_common(),
           "filters": filt.most_common(), "warps": warps.most_common(), "osc_kinds": osck.most_common(), "sources": srcs.most_common(), "bus_presets": bus, "errors": errors[:20], "rows": rows}
    Path(f"calibration/census_{name}.json").write_text(json.dumps(out, indent=1))
    print("presets", len(files), "tiers", dict(tiers), "bus-FX presets", bus, "errors", len(errors))
    for title, items in (("CRITICAL", crit), ("APPROX", appr), ("NOT CARRIED NOTES", tally), ("FX in racks", fxk), ("filters", filt), ("warps used", warps), ("osc kinds", osck)):
        print("\n==", title)
        for k, v in items.most_common(22): print("  %4d  %s" % (v, k))
    for e in errors[:10]: print("ERR", e)
    sys.stdout.flush(); os._exit(0)
main()
