#!/usr/bin/env python3
"""The voice level, preset by preset: Serum with its effects racks emptied against the engine with fx_on 0 at the volume the converter's closed-form master law
gives (no loudness fit), the first sounding note, the rms of the sustained part in dB. The master law is only right if the engine's voice reaches the same level
as Serum's from the same settings; this is how far off each preset is. Everything level-dependent before the master gain (the compressor and distortion
thresholds) inherits the error. Output: calibration/voice_level.json, the worst first printed."""
import copy, json, math, os, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file
d2m, SR = H.d2m, H.SR

def rms_db(x):
    s = x.mean(axis=0)[int(0.1 * SR):int(1.2 * SR)]
    return 20 * math.log10(math.sqrt((s ** 2).mean()) + 1e-12)

def setk(s, k, v):
    return re.sub(rf"{k}=[^;]*", f"{k}={v}", s) if f"{k}=" in s else s + f"{k}={v};"

def main():
    src = {d2m.ascii_name(p.stem): p for p in H.PRESET_DIR.glob("*.SerumPreset")}
    names = sys.argv[1:] or sorted(src)
    serum = H.Serum()
    rows = {}
    for n in names:
        h, t = decode_preset_file(src[n])
        if not H.usable(t):
            continue
        notes = H.sounding_notes(t, n)[:1]
        tt = copy.deepcopy(t)
        for k in ("FXRack0", "FXRack1", "FXRack2"):
            tt[k]["FX"] = []
        for k in list(tt):
            if k.startswith("ModSlot") and str(tt[k].get("destModuleTypeString", "")).startswith("FX"):
                tt[k] = {"plainParams": "default"}
        try:
            a = [serum.render(tt, notes, 1.5, 1.2) for _ in range(2)]
        except Exception as err:
            print("  skipped", n, str(err)[:60], flush=True); serum = H.Serum(); continue
        ph = d2m.physical(t, h)
        v = d2m.to_vital(ph); v["preset_name"] = n; v["serum2move"]["name"] = n
        m = d2m.map_to_tablor(v, {}, H.SERUM_TABLES, H.WT_OUT, "Deep House")
        mast = ph.get("master")
        want = d2m.CAL["master_db0"] + d2m.CAL["master_slope"] * math.log10(max(mast, 1e-4)) if mast is not None else d2m.CAL["master_default_db"]
        vol = d2m.pot_volume(want)
        st = setk(setk(d2m.blob(m), "fx_on", 0), "volume", vol)
        e = [H.run_cli(st, notes, 1.5, 1.2) for _ in range(3)]
        sl = 10 * math.log10(np.mean([10 ** (rms_db(x) / 10) for x in a]))
        el = 10 * math.log10(np.mean([10 ** (rms_db(x) / 10) for x in e]))
        rows[n] = {"serum_db": round(sl, 1), "engine_db": round(el, 1), "diff": round(el - sl, 1), "volume": vol, "master": mast}
        print("%-28s serum %+6.1f engine %+6.1f  engine - serum %+6.1f  (volume %d)" % (n[:28], sl, el, el - sl, vol), flush=True)
    Path("calibration/voice_level.json").write_text(json.dumps(rows, indent=1))
    d = np.array([r["diff"] for r in rows.values()])
    print("\nmean %+.1f dB  std %.1f dB  |diff| median %.1f" % (d.mean(), d.std(), np.median(abs(d))))
    sys.stdout.flush(); os._exit(0)

main()
