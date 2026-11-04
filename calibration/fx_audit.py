#!/usr/bin/env python3
"""Audit of the effects: every effect in every preset, in Serum and in the engine, one at a time.

  fx_audit.py [--kinds Delay,Reverb,...] [--limit N] [--set DIR] [--out report.json]

For each effect node the converter carries (the loudest of its kind in a preset's master rack, macros folded in), a noise source is put through
that one effect in Serum with the preset's stored settings, and through the matching engine stage with the settings the converter wrote
(read from the preset's state in --set, default calibration/g15_final). Compared:
  steady noise   gain of the effect in nine bands against the same noise without it (dB), left/right correlation, side/mid
  a short burst  the tail: the time and level of the first echoes, and how long the tail takes to fall 30 dB
A mismatch past the tolerances below is listed, worst first. The report is also the regression test of every rule fxmap.py applies: run it after
changing any effect mapping. Serum renders are cached in calibration/fx_audit_serum.json (a plugin goes silent after 1200 s of audio, so the
plugin is replaced before that).
"""
import argparse
import copy
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import fxmap
import fxmetrics as FM
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file

d2m, SR = H.d2m, H.SR
CACHE = ROOT / "calibration/fx_audit_serum.json"
TYPE = {"Distortion": 0, "Flanger": 1, "Phaser": 2, "Chorus": 3, "Delay": 4, "Comp": 5, "Reverb": 6, "EQ": 7, "Filter": 8, "HyperD": 9}
GROUP = {"Delay": r"dly_", "Reverb": r"rev_|fx_space", "Chorus": r"chor_", "Flanger": r"flan_", "Phaser": r"phas_", "EQ": r"eq\d_|eq_on", "HyperD": r"fx_wide|fx_wrate|fx_wdepth|wide_on"}
TOL = {"band_db": 3.0, "corr": 0.35, "side_db": 4.0, "echo_t": 0.12, "echo_db": 4.0, "rt30": 0.35, "rt60": 0.4}
GRID = np.array([63, 125, 250, 500, 1000, 2000, 4000, 8000, 12000])


# ------------------------------------------------------------------ measurements (the same function on both systems' audio)
def band_gain(dry, wet):
    out = []
    for c in range(2):
        x0, x1 = dry[c, int(0.4 * SR):], wet[c, int(0.4 * SR):]
        f, p0 = P.psd(np.stack([x0, x0]), lo=0)
        _, p1 = P.psd(np.stack([x1, x1]), lo=0)
        row = []
        for g in GRID:
            k = (f > g / 1.26) & (f < g * 1.26)
            row.append(10 * np.log10(p1[k].sum() / p0[k].sum()))
        out.append(row)
    return np.mean(out, axis=0)


def stereo(a):
    x = a[:, int(0.4 * SR):]
    corr = float(np.corrcoef(x[0], x[1])[0, 1]) if x[0].std() > 0 and x[1].std() > 0 else 1.0
    m, s = (x[0] + x[1]) / 2, (x[0] - x[1]) / 2
    return corr, float(10 * np.log10((s ** 2).sum() / ((m ** 2).sum() + 1e-12) + 1e-12))


def tail_features(a):
    """Echoes and decay of a short noise burst: [(time s, level dB re the burst)] for the first echoes after 0.15 s, and the time to fall 30 dB."""
    x = a.mean(axis=0)
    w = int(0.01 * SR)
    env = 10 * np.log10(np.array([(x[i:i + w] ** 2).mean() for i in range(0, len(x) - w, w)]) + 1e-14)
    pk = env[:15].max()
    e = env - pk
    echoes = []
    for i in range(16, len(e) - 2):
        if e[i] > e[i - 1] and e[i] >= e[i + 1] and e[i] > -35 and e[i] > e[max(i - 8, 0):i].min() + 3:
            echoes.append((round(i * 0.01, 2), round(float(e[i]), 1)))
        if len(echoes) >= 3:
            break
    after = np.where(e[16:] < -30)[0]
    rt = float((after[0] + 16) * 0.01) if after.size else float(len(e) * 0.01)
    return echoes, rt


# ------------------------------------------------------------------ the two systems
class SerumSide:
    def __init__(self):
        self.s = self.base = None
        self.cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
        self.dry = None

    def _plugin(self):
        if self.s is None or self.s.audio_seconds > 800:
            self.s, self.base = P.start()
            self.dry = None
        return self.s

    def render(self, node, hold, dur):
        s = self._plugin()
        t = copy.deepcopy(self.base)
        t["FXRack0"]["FX"] = node if isinstance(node, list) else ([node] if node else [])
        return s.render(t, [48], dur, hold)

    def measure(self, kind, node):
        key = kind + "|" + json.dumps(node, sort_keys=True, default=str)
        if key in self.cache and "rt60" in self.cache[key]:
            return self.cache[key]
        dry = [self.render(None, 2.4, 2.5) for _ in range(2)]
        wet = [self.render(node, 2.4, 2.5) for _ in range(2)]
        burst = self.render(node, 0.12, 5.0)
        e, rt = tail_features(burst)
        corr, side = stereo(wet[0]); dcorr, dside = stereo(dry[0])
        rec = {"bands": band_gain(dry[0], wet[0]).tolist(), "bands2": band_gain(dry[1], wet[1]).tolist(), "corr": corr, "side": side, "dcorr": dcorr, "dside": dside, "echoes": e, "rt30": rt,
               "rt60": FM.rt60(burst)}
        self.cache[key] = rec
        CACHE.write_text(json.dumps(self.cache))
        return rec


def engine_state(state, kind, noise_extra=None):
    """The noise patch plus only this kind's keys from the preset's state."""
    keep = re.compile(GROUP[kind])
    kv = dict(x.split("=", 1) for x in state.split(";")[1:] if "=" in x)
    extra = {k: v for k, v in kv.items() if keep.match(k)}
    extra["fx_on"] = 1
    extra.update(noise_extra or {})
    return fxmap.noise_patch({k: v for k, v in extra.items()})


def engine_measure(state, kind):
    blob = engine_state(state, kind)
    off = engine_state(state, kind, {k: 0 for k in ("dly_on", "rev_on", "wide_on", "eq_on", "chor_on", "flan_on", "phas_on")})
    dry = [H.run_cli(off, [48], 2.5, 2.4) for _ in range(2)]
    wet = [H.run_cli(blob, [48], 2.5, 2.4) for _ in range(2)]
    burst = H.run_cli(blob, [48], 5.0, 0.12)
    e, rt = tail_features(burst)
    corr, side = stereo(wet[0]); dcorr, dside = stereo(dry[0])
    return {"bands": band_gain(dry[0], wet[0]).tolist(), "bands2": band_gain(dry[1], wet[1]).tolist(), "corr": corr, "side": side, "dcorr": dcorr, "dside": dside, "echoes": e, "rt30": rt, "rt60": FM.rt60(burst)}


# ------------------------------------------------------------------ comparison
def compare(kind, s, e):
    """List of (what, serum, engine, severity) past tolerance; severity is the excess over the tolerance in its units."""
    out = []
    b0, b1 = np.array(s["bands"]), np.array(e["bands"])
    noise = np.abs(np.array(s["bands"]) - np.array(s["bands2"])).mean() / 2        # noise-to-noise scatter of the measure
    # an all-over gain is not compared for the EQ stage (it carries the Filter FX, whose Drive is a gain; the preset's loudness is fitted afterwards)
    off = float(np.mean(b0 - b1)) if kind == "EQ" else 0.0
    rms = float(np.sqrt(np.mean((b0 - b1 - off) ** 2)))
    if rms > TOL["band_db"] + noise:
        out.append(("band gain rms dB", round(float(np.mean(b0)), 1), round(float(np.mean(b1)), 1), (rms - TOL["band_db"]) / TOL["band_db"], round(rms, 1)))
    # the stereo CHANGE the effect makes (the two noise sources differ in their own width)
    sc, ec = s["corr"] - s["dcorr"], e["corr"] - e["dcorr"]
    if abs(sc - ec) > TOL["corr"]:
        out.append(("L/R correlation change", round(sc, 2), round(ec, 2), abs(sc - ec) / TOL["corr"] - 1, None))
    # (side/mid is not compared: Serum's noise source has a random L/R level imbalance per render, so a correlation of 1.0 still shows -12..-30 dB of side)
    if kind in ("Delay", "Reverb"):
        se, ee = s["echoes"], e["echoes"]
        if se and ee:
            if abs(se[0][0] - ee[0][0]) > max(TOL["echo_t"] * se[0][0], 0.03):
                out.append(("first echo time s", se[0][0], ee[0][0], abs(se[0][0] - ee[0][0]) / (TOL["echo_t"] * se[0][0]) - 1, None))
            if abs(se[0][1] - ee[0][1]) > TOL["echo_db"]:
                out.append(("first echo level dB", se[0][1], ee[0][1], abs(se[0][1] - ee[0][1]) / TOL["echo_db"] - 1, None))
        elif bool(se) != bool(ee) and (se or ee)[0][1] > -20:
            out.append(("echoes", se[:2], ee[:2], 1.0, None))
        # the time for the tail to fall 30 dB depends on how loud it starts (the wet), so the decay is compared by its slope (fxmetrics.rt60)
        r0, r1 = s.get("rt60", float("nan")), e.get("rt60", float("nan"))
        if kind == "Reverb" and np.isfinite(r0) and np.isfinite(r1) and abs(r0 - r1) > max(TOL["rt60"] * r0, 0.15):
            out.append(("tail decay rt60 s", round(r0, 2), round(r1, 2), abs(r0 - r1) / (TOL["rt60"] * r0) - 1, None))
    return out


def preset_nodes(tree, header):
    """{kind: node}: the node the converter carries for each kind (the loudest, macros folded), with its stored params."""
    ph = d2m.physical(tree, header)
    nodes = fxmap.fold_macro_wets(ph["fx_nodes"], ph["mods"], ph["macros"])
    inside = fxmap.branch_nodes(nodes)
    best, every = {}, []
    # the engine's EQ stage also carries the static low-pass / high-pass Filter effects (fxmap.filter_bands), so Serum gets those with the EQs
    dyn = fxmap.dynamic_filter(nodes, {"mods": ph["mods"], "macros": ph["macros"]})
    eqrack = [copy.deepcopy(n) for i, n in enumerate(nodes) if i not in inside and fxmap.enabled(n) and
              (fxmap.kind_of(n) == "EQ" or (fxmap.kind_of(n) == "Filter" and not (dyn and dyn["index"] == i)))]
    # a Distortion's own filter after the distorting (PrePost 1) is carried in the EQ too: Serum gets it at Drive 0, so only the filter is heard
    for i, n in enumerate(nodes):
        if i not in inside and fxmap.enabled(n) and fxmap.kind_of(n) == "Distortion" and int(round(fxmap.plain(n).get("kParamPrePost", 0.0))) == 1:
            d = copy.deepcopy(n)
            fxmap.plain(d)["kParamDrive"] = 0.0
            eqrack.append(d)
    for i, n in enumerate(nodes):
        k = fxmap.kind_of(n)
        if k not in GROUP or i in inside or not fxmap.enabled(n):
            continue
        wet = fxmap.plain(n).get("kParamWet", 100.0 if k in ("EQ",) else 0.0)
        if k != "EQ" and wet <= 0:
            continue
        if k not in best or wet > best[k][0]:
            best[k] = (wet, n)
        if k == "Reverb":
            every.append(n)
    out = {k: v[1] for k, v in best.items()}
    if len(every) > 1:
        out["Reverb"] = every                    # the converter measures a preset's reverbs together and carries one
    if "EQ" in out or eqrack:
        out["EQ"] = eqrack
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kinds", default="Delay,Reverb,Chorus,Flanger,Phaser,EQ,HyperD")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--set", default="calibration/g15_final")
    ap.add_argument("--out", default="calibration/fx_audit_report.json")
    a = ap.parse_args()
    kinds = a.kinds.split(",")
    ser = SerumSide()
    rows, done = [], 0
    stems = {fxmap.norm_name(d2m.ascii_name(p.stem)): p for p in H.PRESET_DIR.glob("*.SerumPreset")}
    for f in sorted(Path(a.set).glob("*.json")):
        name = f.stem
        p = stems.get(fxmap.norm_name(name))
        if not p:
            continue
        header, tree = decode_preset_file(p)
        state = json.loads(f.read_text(encoding="utf-8"))["state"]
        for kind, node in preset_nodes(tree, header).items():
            if kind not in kinds:
                continue
            if kind not in ("EQ", "Reverb"):
                node = copy.deepcopy(node)
            try:
                s = ser.measure(kind, node)
                e = engine_measure(state, kind)
            except RuntimeError as err:
                print("  skipped", name, kind, str(err)[:60]); continue
            miss = compare(kind, s, e)
            rows.append({"preset": name, "kind": kind, "serum": s, "engine": e, "mismatch": [list(m) for m in miss]})
            worst = max([m[3] for m in miss], default=0)
            print("%-26s %-8s %s" % (name, kind, ("OK" if not miss else "; ".join("%s %s vs %s" % (m[0], m[1], m[2]) for m in miss))[:150]), flush=True)
        done += 1
        if a.limit and done >= a.limit:
            break
    Path(a.out).write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
    bad = [r for r in rows if r["mismatch"]]
    print(f"\n{len(rows)} effects audited, {len(bad)} with a mismatch")
    for kind in kinds:
        ks = [r for r in rows if r["kind"] == kind]
        print(f"  {kind:8s} {len(ks) - len([r for r in ks if r['mismatch']])}/{len(ks)} match")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
