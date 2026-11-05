#!/usr/bin/env python3
"""Serum's Reverb kParamFreq against the engine's reverb low cut (rev_lo).  A held saw (Keys - Loop, init), one kHall reverb at Wet 100, kParamFreq swept;
octave-band level (voice_survey.bands: 20-40, 40-80, ... Hz) against Freq 20.  Result: Freq is a low cut in Hz, absent = none, steep (Hall at 74 Hz: -23 / -16 / -8.5 dB in the three lowest bands, 45 Hz -8 / -4 / -3, 30 Hz nothing), far more than
the engine's one-pole rev_lo gives (it also takes 2-10 dB off the mids, which a high pass does not).  NOT adopted: a three-pole rev_lo with the corner at 0.85 x Hz
fixed the sub-bass of Self Control (40-80 Hz +11 -> +4 dB re body) but was neutral across the 20 presets with a reverb Freq of 30 Hz or more (mean octave-band rms
9.27 -> 9.22), because it also cut sub-bass the engine was already short of (Bass - Rivo, Keys - BUNT).   probe_reverb_lowcut.py"""
import copy
import math
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import probe_vfilter as V
import test_render_calibration as H
import voice_survey as VS
from serum2_preset_loader.codec import decode_preset_file

d2m = H.d2m


def setk(s, k, v):
    return re.sub(rf"(?<![\w.]){re.escape(k)}=[^;]*", f"{k}={v}", s) if re.search(rf"(?<![\w.]){re.escape(k)}=", s) else s + f"{k}={v};"


def main():
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t)
    _, ref = decode_preset_file(next(H.PRESET_DIR.rglob("Lead - Self Control.SerumPreset")))
    node = next(n for n in ref["FXRack0"]["FX"] if "FXReverb" in n and n["FXReverb"]["plainParams"].get("kParamType") == "kVintage")
    pool = V.SerumPool()

    def serum(hz):
        t = copy.deepcopy(tree0)
        nd = copy.deepcopy(node)
        nd["FXReverb"]["plainParams"] = {"kParamWet": 100.0, "kParamType": "kHall", "kParamFreq": hz}
        t["FXRack0"]["FX"] = [nd]
        return VS.bands(pool.render(t))

    _, m = H.tablor_render(tree0, [48], 0.3, 0.2)
    blob = setk(setk(setk(d2m.blob(m), "rev_mix", 127), "rev_on", 1), "fx_on", 1)

    def engine(hz):
        pot = 0 if hz is None else round(127 * math.log(hz / 20) / math.log(1000))
        return VS.bands(H.run_cli(setk(blob, "rev_lo", pot), [48], 1.5, 1.2))

    e0, b20 = engine(None), serum(20.0)
    for hz in (30, 45, 74, 95, 150):
        print(f"Freq {hz:4d}  serum  " + " ".join(f"{v:+5.1f}" for v in (serum(float(hz)) - b20)[:7]), flush=True)
        print(f"           engine " + " ".join(f"{v:+5.1f}" for v in (engine(0.85 * hz) - e0)[:7]) + "   (corner 0.85 x Hz)", flush=True)
    os._exit(0)


main()
