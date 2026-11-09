#!/usr/bin/env python3
"""The noise oscillator of each preset on its own (no voice filter, no modulation, no effects), Serum against the engine at the converter's closed-form volume: the octave-band
levels re the loudest band, and the overall level difference (engine minus Serum, dB). A mismatch here is the noise type, colour or level law. [names...] limits it."""
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

def level(x):
    s = x.mean(axis=0)[int(0.1 * H.SR):int(1.2 * H.SR)]
    return 10 * math.log10((s ** 2).mean() + 1e-20)

def main():
    src = {d2m.ascii_name(p.stem): p for p in H.PRESET_DIR.glob("*.SerumPreset")}
    names = sys.argv[1:]
    if not names:
        names = []
        for n, p in sorted(src.items()):
            h, t = decode_preset_file(p)
            try: ph = d2m.physical(t, h)
            except Exception: continue
            if ph["noise"]["enabled"] and ph["noise"]["level"] >= 0.1:
                names.append(n)
    serum = H.Serum()
    rows = {}
    for n in names:
        h, t = decode_preset_file(src[n])
        ph = d2m.physical(t, h)
        if not ph["noise"]["enabled"]:
            continue
        notes = H.sounding_notes(t, n)[:1]
        tt = copy.deepcopy(t)
        for k in ("FXRack0", "FXRack1", "FXRack2"):
            tt[k]["FX"] = []
        for k in list(tt):
            if k.startswith("ModSlot"):
                tt[k] = {"plainParams": "default"}
        for i in range(5):
            if i != 3 and f"Oscillator{i}" in tt:
                pp = tt[f"Oscillator{i}"]["plainParams"]
                tt[f"Oscillator{i}"]["plainParams"] = {**(pp if isinstance(pp, dict) else {}), "kParamEnable": 0.0}
        tt["VoiceFilter0"]["plainParams"] = {"kParamEnable": 0.0}
        tt["VoiceFilter1"]["plainParams"] = {"kParamEnable": 0.0}
        try:
            a = [serum.render(tt, notes, 1.5, 1.2) for _ in range(2)]
        except Exception as err:
            print("  skipped", n, str(err)[:50], flush=True); serum = H.Serum(); continue
        m = d2m.map_to_tablor(d2m.to_vital(ph) | {"preset_name": n}, {}, H.SERUM_TABLES, H.WT_OUT, "Deep House")
        st = d2m.blob(m)
        for key, val in (("wt1_level", 0), ("wt2_level", 0), ("wt3_level", 0), ("sub_level", 0), ("smp_level", 0), ("smp2_level", 0), ("smp3_level", 0), ("flt_freq", 127), ("flt_env", 64), ("flt_res", 0), ("flt_drive", 0),
                         ("flt_gain", 0), ("flt_key", 0), ("fx_on", 0), ("me1_dst", 0), ("me2_dst", 0), ("noise_post", 1), ("volume", 36)):
            st = setk(st, key, val)
        for r in range(1, 9):
            st = setk(st, "lr%d_src" % r, 0)
        e = [H.run_cli(st, notes, 1.5, 1.2) for _ in range(3)]
        b1 = np.mean([VS.bands(x) for x in a], axis=0); b2 = np.mean([VS.bands(x) for x in e], axis=0)
        # the engine's output has the master gain at volume 36; Serum's the preset's master: compare shapes, and the level re the same unit noise via the converter's own law
        d = (b2 - b2.max()) - (b1 - b1.max())
        shape = float(np.sqrt(np.mean(d[2:9] ** 2)))
        rows[n] = {"type": ph["noise"]["type"], "color": ph["noise"]["color"], "level": ph["noise"]["level"], "shape_rms": round(shape, 1), "serum_db": round(level(a[0]), 1), "engine_db": round(level(e[0]), 1),
                   "diff": d.round(1).tolist(), "master": ph.get("master")}
        print("%-28s %-8s col %-5s vol %.2f  shape rms %4.1f  | %s" % (n[:28], ph["noise"]["type"], "-" if ph["noise"]["color"] is None else "%.2f" % ph["noise"]["color"], ph["noise"]["level"], shape,
              " ".join("%+4.0f" % x for x in d)), flush=True)
    Path("calibration/noise_survey.json").write_text(json.dumps(rows, indent=1))
    sys.stdout.flush(); os._exit(0)

main()
