#!/usr/bin/env python3
"""What is each effect TinyHouse does not carry worth? Asked of Serum itself.

  fx_contribution.py

For every preset and every effect kind in its Serum racks that the converter does not carry, Serum renders the
preset again with that kind switched off (all its nodes, main and bus racks), and the full-chain loss between
that render and the real full render is the most any implementation of the effect could gain: if switching it
off in Serum barely moves the sound, carrying it in Tablor is not worth much. The loss between Serum's own two
renders of the same preset is the noise floor (unison phases are random) and is subtracted. One more render per
preset has all uncarried kinds off together. Output: calibration/fx_contribution.json, resumable.
Serum is rendered here, one process, 4 s each: the plugin renders about 1200 s, so it stops at 1100 and can be re-run.
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter"))
sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import fit_chain as C
import fit_perceptual as F
import fxmap
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/fx_contribution.log"
log = H.log
OUT = H.OUT / "fx_contribution.json"
CARRIED = {"EQ", "Delay", "Reverb", "HyperD", "Chorus", "Flanger", "Phaser", "Conv"}


def racks(tree):
    return [k for k in ("FXRack0", "FXRack1", "FXRack2") if k in tree]


def kinds_in(tree):
    out = set()
    for k in racks(tree):
        for n in tree[k].get("FX", []) or []:
            kind = fxmap.kind_of(n)
            if kind and fxmap.enabled(n) and kind not in CARRIED:
                out.add(kind)
    return sorted(out)


def without(tree, drop):
    t = copy.deepcopy(tree)
    for k in racks(t):
        for n in t[k].get("FX", []) or []:
            kind = fxmap.kind_of(n)
            if kind in drop:
                body = n.setdefault(f"FX{kind}", {})
                pp = body.get("plainParams")
                body["plainParams"] = dict(pp, kParamEnable=0.0) if isinstance(pp, dict) else {"kParamEnable": 0.0}
    return t


def without_all(tree, kinds):
    """The tree with every enabled node of these kinds switched off, in every rack."""
    return without(tree, set(kinds))


def main():
    holder = []
    serum = lambda: holder[0] if holder else (holder.append(H.Serum()) or holder[0])
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    for n in sorted(p.stem for p in H.PRESET_DIR.glob("*.SerumPreset")):
        if n in data:
            continue
        header, tree = H.load_preset(n)
        if not H.usable(tree):
            continue
        it = C.load_items([n])
        if not it:
            continue
        it = it[0]
        ks = kinds_in(tree)
        f0, f1 = F.features([it["refs"][0]], it["end"]), F.features([it["refs"][1]], it["end"])
        noise = F.loss_of(f0, f1)["loss"]
        rec = {"kinds": ks, "noise": noise, "effect": {}}
        for k in ks + (["ALL"] if len(ks) > 1 else []):
            drop = set(ks) if k == "ALL" else {k}
            r = serum().render(without(tree, drop), it["notes"], C.DUR, C.HOLD)
            l = F.loss_of(f0, F.features([r], it["end"]))
            rec["effect"][k] = {kk: float(vv) for kk, vv in l.items()}
        data[n] = rec
        OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
        log(f"  {n:28s} noise {noise:5.1f}  " + "  ".join(f"{k} {v['loss']:5.1f}" for k, v in rec["effect"].items()))
        if holder and holder[0].audio_seconds > 1100:
            log("render limit reached; run again for the rest")
            break
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
