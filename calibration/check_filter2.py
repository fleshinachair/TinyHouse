#!/usr/bin/env python3
"""The second voice filter and the balance, end to end: a synthetic Serum patch (one saw through Filter 1 and/or Filter 2 at a given balance) is rendered
in Serum and, converted by deephouse2move.py, in the engine, and the level of each harmonic is compared (the engine's level is set by the unfiltered saw
of the same patch, so only the filtering differs).  check_filter2.py    prints per case the harmonic-by-harmonic difference (dB)."""
import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import deephouse2move as d2m
import test_render_calibration as H

SR = H.SR
NOTE = 48
F0 = 440 * 2 ** ((NOTE - 69) / 12)
NH = 40


def harm(x):
    m = x[0, int(0.5 * SR):int(1.3 * SR)]
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)), 1 << 16))
    f = np.fft.rfftfreq(1 << 16, 1 / SR)
    return np.array([20 * np.log10(sp[max(np.searchsorted(f, k * F0) - 4, 0):np.searchsorted(f, k * F0) + 5].max() + 1e-12) for k in range(1, NH + 1)])


def engine_render(tree, name):
    v = d2m.to_vital(d2m.physical(tree, {"presetName": name, "tags": []}))
    v["preset_name"] = name; v["serum2move"]["name"] = name
    m = d2m.map_to_tablor(v, {}, H.SERUM_TABLES, H.WT_OUT, "Deep House")
    m["params"]["volume"] = 28
    m["params"]["fx_on"] = 0
    return H.run_cli(d2m.blob(m), [NOTE], 1.6, 1.4), m


def main():
    _, base_tree = H.load_preset("Keys - Loop")
    serum = H.Serum()

    def patch(f1, f2, balance=None):
        t = H.init_tree(base_tree)
        t["VoiceFilter0"]["plainParams"] = f1
        if f2 is not None:
            t["VoiceFilter1"]["plainParams"] = f2
        rs = {"kParamRoutingDest": "kRoutingDestFilter"}
        if balance is not None:
            rs["kParamFilterBalance"] = balance
        t["RoutingSlot0"] = {"plainParams": rs}
        return t

    off = {"kParamEnable": 0.0}
    ref_s = harm(serum.render(patch(off, None), [NOTE], 1.6, 1.4))
    ref_e = harm(engine_render(patch(off, None), "f2ref")[0])
    gain = np.mean((ref_s - ref_e)[:6])
    print("unfiltered saw: engine level offset %.1f dB (taken off every case)" % gain)
    cases = {
        "F2 only, MgL24 .5": patch(off, {"kParamEnable": 1.0, "kParamFreq": 0.5, "kParamType": "MgL24"}, 100.0),
        "F2 only, MgL12 .4 reso 30": patch(off, {"kParamEnable": 1.0, "kParamFreq": 0.4, "kParamType": "MgL12", "kParamReso": 30.0}, 100.0),
        "F2 only, unwritten type .5": patch(off, {"kParamEnable": 1.0, "kParamFreq": 0.5}, 100.0),
        "F2 only, H24 .5": patch(off, {"kParamEnable": 1.0, "kParamFreq": 0.5, "kParamType": "H24"}, 100.0),
        "both 50/50: F1 MgL18 .6, F2 MgL24 .4": patch({"kParamEnable": 1.0, "kParamFreq": 0.6, "kParamType": "MgL18"}, {"kParamEnable": 1.0, "kParamFreq": 0.4, "kParamType": "MgL24"}, 0.0),
        "75% F2: F1 MgL24 .5, F2 MgL12 .7": patch({"kParamEnable": 1.0, "kParamFreq": 0.5, "kParamType": "MgL24"}, {"kParamEnable": 1.0, "kParamFreq": 0.7, "kParamType": "MgL12"}, 50.0),
        "F2 key track 1, MgL24 .4": patch(off, {"kParamEnable": 1.0, "kParamFreq": 0.4, "kParamType": "MgL24", "kParamKeyTrack": 1.0}, 100.0),
        "F2 drive 50, MgL24 .5": patch(off, {"kParamEnable": 1.0, "kParamFreq": 0.5, "kParamType": "MgL24", "kParamDrive": 50.0}, 100.0),
    }
    for name, t in cases.items():
        hs = harm(serum.render(t, [NOTE], 1.6, 1.4))
        he, _m = engine_render(t, "f2case")
        he = harm(he) + gain
        keep = (hs > hs.max() - 60) | (he > he.max() - 60)
        d = (he - hs)[keep]
        print("%-40s harmonics %2d  mean diff %+5.1f dB  rms %4.1f  worst %+5.1f at h%d" % (name, keep.sum(), d.mean(), np.sqrt((d ** 2).mean()), d[np.argmax(abs(d))], np.where(keep)[0][np.argmax(abs(d))] + 1), flush=True)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
