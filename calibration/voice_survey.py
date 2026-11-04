#!/usr/bin/env python3
"""The sources and voice filters alone, preset by preset: Serum with its effects racks emptied against the engine with fx_on 0 (both for the preset's first sounding
note), the level of each octave band relative to that render's loudest. A preset whose voice section is wrong shows here before any effect can hide or add to it.
  voice_survey.py [--set DIR] [names...]     writes calibration/voice_survey.json, prints the worst first"""
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
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file

d2m, SR = H.d2m, H.SR
EDGES = [20, 40, 80, 160, 320, 640, 1250, 2500, 5000, 10000, 20000]


def bands(x):
    x = x.mean(axis=0)[int(0.1 * SR):int(1.2 * SR)]
    s = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return np.array([10 * np.log10(s[(f >= a) & (f < b)].sum() + 1e-12) for a, b in zip(EDGES[:-1], EDGES[1:])])


def setk(s, k, v):
    return re.sub(rf"{k}=[^;]*", f"{k}={v}", s) if f"{k}=" in s else s + f"{k}={v};"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    src = {d2m.ascii_name(p.stem): p for p in H.PRESET_DIR.glob("*.SerumPreset")}
    names = args or sorted(src)
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
            a = serum.render(tt, notes, 1.5, 1.2)
        except Exception as err:
            print("  skipped", n, str(err)[:60], flush=True)
            serum = H.Serum()
            continue
        v = d2m.to_vital(d2m.physical(t, h)); v["preset_name"] = n; v["serum2move"]["name"] = n
        m = d2m.map_to_tablor(v, {}, H.SERUM_TABLES, H.WT_OUT, "Deep House")
        e = H.run_cli(setk(setk(d2m.blob(m), "fx_on", 0), "volume", 30), notes, 1.5, 1.2)    # a low volume: the converter's closed-form one can clip the engine's output stage
        b1, b2 = bands(a), bands(e)
        d = (b2 - b2.max()) - (b1 - b1.max())
        rows[n] = {"serum": (b1 - b1.max()).round(1).tolist(), "engine": (b2 - b2.max()).round(1).tolist(), "diff": d.round(1).tolist()}
        mid = d[3:9]                                              # 160 Hz .. 5 kHz
        print("%-28s worst %+6.1f  mean(640-5k) %+6.1f   diff %s" % (n, mid[np.argmax(abs(mid))], d[5:9].mean(), " ".join("%+5.0f" % x for x in d)), flush=True)
    Path("calibration/voice_survey.json").write_text(json.dumps(rows, indent=1))
    worst = sorted(rows, key=lambda n: -np.abs(np.array(rows[n]["diff"][3:9])).max())
    print("\nworst first:")
    for n in worst[:25]:
        print("  %-28s max |diff| 160-5k %.1f dB" % (n, np.abs(np.array(rows[n]["diff"][3:9])).max()))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
