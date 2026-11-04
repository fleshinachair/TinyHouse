#!/usr/bin/env python3
"""Each wavetable oscillator of every preset on its own (no voice filter, no modulation, no effects), Serum against the engine: the octave-band levels of the first
sounding note, each re its own loudest band. A mismatch here is an oscillator-model error (table position, warp, unison, tuning, the table itself), before the filter
and effects can hide or add to it. calibration/osc_survey.json; the worst first printed. [names...] limits it."""
import copy, json, math, os, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import test_render_calibration as H
import voice_survey as VS
from serum2_preset_loader.codec import decode_preset_file
d2m = H.d2m

def setk(s, k, v):
    return re.sub(r"(?<![a-z_0-9])%s=[^;]*;" % k, "%s=%s;" % (k, v), s) if re.search(r"(?<![a-z_0-9])%s=" % k, s) else s + "%s=%s;" % (k, v)

def main():
    src = {d2m.ascii_name(p.stem): p for p in H.PRESET_DIR.glob("*.SerumPreset")}
    names = sys.argv[1:] or sorted(src)
    serum = H.Serum()
    rows = {}
    for n in names:
        h, t = decode_preset_file(src[n])
        if not H.usable(t):
            continue
        ph = d2m.physical(t, h)
        notes = H.sounding_notes(t, n)[:1]
        m = d2m.map_to_tablor(d2m.to_vital(ph) | {"preset_name": n}, {}, H.SERUM_TABLES, H.WT_OUT, "Deep House")
        base = d2m.blob(m)
        slots = {}
        for k in ("wt1", "wt2", "wt3"):
            pass
        for o in ph["oscs"]:
            if not o["enabled"] or o["kind"] != "kOsc_WT":
                continue
            # which engine slot holds this oscillator: the converter keeps the loudest three in order of level; find it from the state's tables
            tab = o["table"].split("/")[-1]
            kv = dict(x.split("=", 1) for x in base.split(";")[1:] if "=" in x)
            tune = round(o["octave"] * 12 + o["semitone"])
            def score(i):
                if tab not in kv.get("wt%d_table" % i, ""):
                    return -1
                return 1 + (int(float(kv.get("wt%d_tune" % i, 0))) == tune) + (abs(float(kv.get("wt%d_pos" % i, 0)) - (o["table_pos"] - 1) / 255 * 127) < 1.5)                     + (int(float(kv.get("wt%d_uni" % i, 1))) == int(o["unison"]))
            best = max((1, 2, 3), key=score)
            slot = best if score(best) > 0 else None
            if slot is None:
                continue
            tt = copy.deepcopy(t)
            for k in ("FXRack0", "FXRack1", "FXRack2"):
                tt[k]["FX"] = []
            for k in list(tt):
                if k.startswith("ModSlot"):
                    tt[k] = {"plainParams": "default"}
            for i in range(5):
                if i != o["id"] and f"Oscillator{i}" in tt:
                    pp = tt[f"Oscillator{i}"]["plainParams"]
                    tt[f"Oscillator{i}"]["plainParams"] = {**(pp if isinstance(pp, dict) else {}), "kParamEnable": 0.0}
            tt["VoiceFilter0"]["plainParams"] = {"kParamEnable": 0.0}
            tt["VoiceFilter1"]["plainParams"] = {"kParamEnable": 0.0}
            pp = tt[f"Oscillator{o['id']}"]["plainParams"]
            if isinstance(pp, dict):
                pp["kParamVolume"] = 0.75
            try:
                a = [serum.render(tt, notes, 1.5, 1.2) for _ in range(2)]
            except Exception as err:
                print("  skipped", n, str(err)[:50], flush=True); serum = H.Serum(); continue
            st = base
            for w in (1, 2, 3):
                st = setk(st, "wt%d_level" % w, 100 if w == slot else 0)
            for key, val in (("sub_level", 0), ("noise_level", 0), ("smp_level", 0), ("flt_freq", 127), ("flt_env", 64), ("flt_res", 0), ("flt_drive", 0), ("flt_gain", 0), ("flt_key", 0),
                             ("fx_on", 0), ("volume", 30), ("me1_dst", 0), ("me2_dst", 0)):
                st = setk(st, key, val)
            for r in range(1, 9):
                st = setk(st, "lr%d_src" % r, 0)
            st = setk(st, "wt%d_post" % slot, 1)
            st = setk(st, "volume", 36)
            e = [H.run_cli(st, notes, 1.5, 1.2) for _ in range(2)]
            b1 = np.mean([VS.bands(x) for x in a], axis=0); b2 = np.mean([VS.bands(x) for x in e], axis=0)
            s1, s2 = b1 - b1.max(), b2 - b2.max()
            d = s2 - s1
            keep = [i for i in range(10) if s1[i] > -45 or s2[i] > -45]
            worst = max((abs(d[i]) for i in keep if 2 <= i <= 8), default=0.0)
            lvl = lambda xs: 10 * math.log10(np.mean([(x.mean(axis=0)[int(0.1 * H.SR):int(1.2 * H.SR)] ** 2).mean() for x in xs]) + 1e-20)
            mst = ph.get("master")
            want = d2m.CAL["master_db0"] + d2m.CAL["master_slope"] * math.log10(max(mst, 1e-4)) if mst is not None else d2m.CAL["master_default_db"]
            g_eng = 20 * math.log10((36 / 127) ** 2 * d2m.MASTER_MAX)
            # the engine at volume 36 against Serum at the preset's master: the master law says the two should differ by g_eng - want when the oscillator levels agree
            level_res = lvl(e) - lvl(a) - (g_eng - want)
            key = f"{n}#{o['id']}"
            rows[key] = {"table": o["table"], "pos": o["table_pos"], "unison": o["unison"], "warp": [(w["mode"], round(w["amount"], 2)) for w in o["warp"] if w["amount"] > 0.02],
                         "serum": s1.round(1).tolist(), "engine": s2.round(1).tolist(), "worst": round(float(worst), 1), "level_res": round(float(level_res), 1), "osc_level": o["level"]}
            print("%-30s osc %d %-28s worst %5.1f  level residual %+5.1f  diff %s" % (n[:30], o["id"], o["table"].split("/")[-1][:28], worst, level_res, " ".join("%+4.0f" % x for x in d)), flush=True)
    Path("calibration/osc_survey.json").write_text(json.dumps(rows, indent=1))
    print("\nworst first:")
    for k in sorted(rows, key=lambda k: -rows[k]["worst"])[:30]:
        r = rows[k]
        print("  %-34s %5.1f  %s pos %.0f uni %d warp %s" % (k, r["worst"], r["table"].split("/")[-1], r["pos"], r["unison"], r["warp"]))
    sys.stdout.flush(); os._exit(0)

main()
