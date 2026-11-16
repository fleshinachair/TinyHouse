#!/usr/bin/env python3
"""Give an already shipped (fitted, levelled) Deep House bank its Serum macros without regenerating it.

The shipped presets carry per-preset loudness fits that a fresh conversion cannot reproduce, so this patches only what the macros add: the
`mac` routes and `macn` names, and every parameter the macro pass changed in the converter's own output (the macro positions mac1..mac8, and
the base values of an effect a macro switches on). Everything else in the shipped preset is left as it is.

    python calibration/add_macros.py <dir with the shipped *.json> <out dir> [--bank "Deep House"]

Presets are matched to the pack's Serum presets by name (letters and digits only). Output: printed summary, one JSON per preset in <out dir>."""
import json, os, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import test_render_calibration as H
d2m = H.d2m


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def parse(state):
    assert state.startswith("TBLR2;")
    items, p = [], 6
    while p < len(state):
        eq = state.find("=", p)
        if eq < 0: break
        semi = state.find(";", eq)
        if semi < 0: semi = len(state)
        items.append([state[p:eq], state[eq + 1:semi]])
        p = semi + 1
    return items


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    dst.mkdir(parents=True, exist_ok=True)
    bank = sys.argv[sys.argv.index("--bank") + 1] if "--bank" in sys.argv else "Deep House"
    names = {norm(p.stem): p.stem for p in H.PRESET_DIR.glob("*.SerumPreset")}
    done = skipped = withmac = 0
    for f in sorted(src.glob("*.json")):
        stem = names.get(norm(f.stem))
        if not stem:
            print(f"{f.stem}: no Serum preset of that name, left alone"); skipped += 1; continue
        hd, tree = H.load_preset(stem)
        v = d2m.to_vital(d2m.physical(tree, hd))
        a = (v, {}, H.SERUM_TABLES, H.WT_OUT, bank)
        m_new = d2m.map_to_tablor(*a)
        m_old = d2m.map_to_tablor(*a, _macros=False)
        diff = {k: val for k, val in m_new["params"].items() if m_old["params"].get(k) != val}
        mc = m_new.get("macro")
        d = json.loads(f.read_text(encoding="utf-8"))
        items = [kv for kv in parse(d["state"]) if kv[0] not in ("mac", "macn")]
        idx = {kv[0]: kv for kv in items}
        for k, val in diff.items():
            if k in idx: idx[k][1] = str(val)
            else: items.append([k, str(val)])
        if mc and mc["routes"]:
            items.append(["mac", ",".join(f"{k}:{key}:{t:.1f}" for k, key, t in mc["routes"])])
            items.append(["macn", "|".join(mc["names"])])
            withmac += 1
        d["state"] = "TBLR2;" + ";".join(f"{k}={val}" for k, val in items) + ";"
        (dst / f.name).write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        nr = len(mc["routes"]) if mc else 0
        print(f"{f.stem[:34]:34s} routes {nr:3d}  base keys changed {len(diff) - sum(1 for k in diff if k.startswith('mac')):2d}  [{', '.join(n for n in (mc['names'] if mc else []) if n)}]")
        done += 1
    print(f"{done} presets patched ({withmac} with macros), {skipped} left alone")
    sys.stdout.flush()
    os._exit(0)


main()
