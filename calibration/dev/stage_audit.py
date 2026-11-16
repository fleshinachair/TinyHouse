#!/usr/bin/env python3
"""Stage audit on the real voice: Serum with only the preset's master Distortion and Comp nodes in its racks against the engine with only its distortion and
compressor stage on (every other effect off), both playing the preset's first sounding note. The fx audit puts noise through one stage; this puts the
preset's own sound through them, which is where a distortion's colour and a compressor's gain reduction are heard.
  stage_audit.py [--kinds Distortion,Comp] [--set DIR] [names...]     writes calibration/stage_audit.json, prints the worst first
Per preset: the loudness change the stage makes in Serum and in the engine (dB re the same render without it), and the octave-band change of the
spectrum (dB, with the all-over gain taken out) in both."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fxmap
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file

d2m, SR = H.d2m, H.SR
EDGES = [20, 40, 80, 160, 320, 640, 1250, 2500, 5000, 10000, 20000]
NS, NE = 3, 4
OFF = ("dly_on", "rev_on", "wide_on", "eq_on", "chor_on", "flan_on", "phas_on")


def spec(x):
    x = x.mean(axis=0)[int(0.1 * SR):int(1.2 * SR)]
    s = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return np.array([10 * np.log10(s[(f >= a) & (f < b)].sum() + 1e-12) for a, b in zip(EDGES[:-1], EDGES[1:])]), 10 * np.log10(s.sum() + 1e-12)


def avg_spec(renders):
    """Octave-band levels and total level of the mean power over several renders (one render's level scatters by a few dB with the random unison phases)."""
    sp = [spec(r) for r in renders]
    b = 10 * np.log10(np.mean([10 ** (x[0] / 10) for x in sp], axis=0))
    return b, float(10 * np.log10(np.mean([10 ** (x[1] / 10) for x in sp])))


def setk(s, k, v):
    return re.sub(rf"{k}=[^;]*", f"{k}={v}", s) if f"{k}=" in s else s + f"{k}={v};"


def disable(node):
    sub = node[f"FX{fxmap.kind_of(node)}"]
    if not isinstance(sub.get("plainParams"), dict):
        sub["plainParams"] = {}
    sub["plainParams"]["kParamEnable"] = 0.0


def serum_tree(t, h, kinds):
    """The preset with only the master-rack nodes of these kinds the converter reads (macros folded into the stored values) enabled; the others stay in their
    places, disabled (the mod slots refer to a module by its position)."""
    ph = d2m.physical(t, h)
    nodes = fxmap.fold_macro_knobs(fxmap.fold_macro_wets(ph["fx_nodes"], ph["mods"], ph["macros"]), ph["mods"], ph["macros"])
    inside = fxmap.branch_nodes(nodes)
    tt = copy.deepcopy(t)
    raw = tt["FXRack0"]["FX"]
    assert len(raw) == len(nodes), "the converter's node list is not rack 0"
    n_keep = 0
    for i, n in enumerate(nodes):
        k = fxmap.kind_of(n)
        pp = fxmap.plain(n)
        on = (k in kinds and i not in inside and fxmap.enabled(n) and pp.get("kParamWet", 100.0) >= 1.0 and not (k == "Distortion" and pp.get("kParamMode") == "kDownsample"))
        if on:
            node = copy.deepcopy(n)
            if k == "Distortion":
                if not isinstance(node["FXDistortion"].get("plainParams"), dict):
                    node["FXDistortion"]["plainParams"] = {}
                node["FXDistortion"]["plainParams"]["kParamPrePost"] = 0.0      # the Distortion's own filter is carried in the EQ stage, which is off here
            raw[i] = node
            n_keep += 1
        else:
            disable(raw[i])
    for k in ("FXRack1", "FXRack2"):
        for n in tt[k]["FX"]:
            disable(n)
    for k in list(tt):
        dest = str(tt[k].get("destModuleTypeString", "")) if k.startswith("ModSlot") else ""
        if dest.startswith("FX") and not (dest == "FXFilter" and "Filter" in kinds):         # the Filter's own envelope routes are what it is audited for
            tt[k] = {"plainParams": "default"}
    return tt, n_keep


def engine_state(state, kinds):
    s = state
    for k in OFF:
        s = setk(s, k, 0)
    if "Distortion" not in kinds:
        s = setk(setk(s, "dist_drive", 0), "dist_cal", 0)
    if "Filter" not in kinds:
        s = setk(s, "fxf_on", 0)
    if "Comp" not in kinds:
        s = setk(s, "comp_on", 0); s = setk(s, "comp_mix", 0)
    return s


def main():
    args = sys.argv[1:]
    kinds = ["Distortion", "Comp"]
    sdir = ROOT / "calibration/g15_final"
    names = []
    i = 0
    while i < len(args):
        if args[i] == "--kinds":
            kinds = args[i + 1].split(","); i += 2
        elif args[i] == "--set":
            sdir = Path(args[i + 1]); i += 2
        else:
            names.append(args[i]); i += 1
    src = {d2m.ascii_name(p.stem): p for p in H.PRESET_DIR.glob("*.SerumPreset")}
    serum = H.Serum()
    rows = {}
    for n in sorted(names or src):
        f = sdir / f"{n}.json"
        if not f.exists():
            continue
        h, t = decode_preset_file(src[n])
        if not H.usable(t):
            continue
        tt, nk = serum_tree(t, h, kinds)
        if not nk:
            continue
        notes = H.sounding_notes(t, n)[:1]
        t0, _ = serum_tree(t, h, [])
        state = json.loads(f.read_text(encoding="utf-8"))["state"]
        try:
            a1 = [serum.render(tt, notes, 1.5, 1.2) for _ in range(NS)]
            a0 = [serum.render(t0, notes, 1.5, 1.2) for _ in range(NS)]
        except Exception as err:
            print("  skipped", n, str(err)[:60], flush=True)
            serum = H.Serum()
            continue
        e1 = [H.run_cli(setk(engine_state(state, kinds), "volume", 30), notes, 1.5, 1.2) for _ in range(NE)]
        e0 = [H.run_cli(setk(engine_state(state, []), "volume", 30), notes, 1.5, 1.2) for _ in range(NE)]
        (sb1, sl1), (sb0, sl0), (eb1, el1), (eb0, el0) = avg_spec(a1), avg_spec(a0), avg_spec(e1), avg_spec(e0)
        ds, de = sl1 - sl0, el1 - el0
        shape_s, shape_e = (sb1 - sb0) - ds, (eb1 - eb0) - de
        d = shape_e - shape_s
        mid = d[2:8]
        rows[n] = {"serum_db": round(float(ds), 1), "engine_db": round(float(de), 1), "serum_shape": shape_s.round(1).tolist(), "engine_shape": shape_e.round(1).tolist(), "diff": d.round(1).tolist(), "nodes": nk}
        print("%-28s level %+5.1f vs %+5.1f   shape worst %+5.1f  rms %4.1f  | %s" % (n[:28], ds, de, mid[np.argmax(abs(mid))], np.sqrt(np.mean(mid ** 2)), " ".join("%+4.0f" % x for x in d)), flush=True)
    Path("calibration/stage_audit.json").write_text(json.dumps(rows, indent=1))
    print("\nworst level mismatch:")
    for n in sorted(rows, key=lambda n: -abs(rows[n]["serum_db"] - rows[n]["engine_db"]))[:15]:
        r = rows[n]
        print("  %-28s serum %+5.1f engine %+5.1f" % (n, r["serum_db"], r["engine_db"]))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
