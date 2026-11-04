#!/usr/bin/env python3
"""How far a modulator moves Serum's voice-filter cutoff, in the cutoff knob's own units.

A held saw through MgL24 at a base cutoff; a modulator held at a constant value (an envelope at sustain 1, or an LFO on a flat
curve) routed to the cutoff with an amount, unipolar or bipolar. The harmonic transfer of the result is matched against static
cutoffs (cached renders at a fine grid) to read off the cutoff knob the modulation amounts to, and so the move per unit of amount.
Output: calibration/probe_vcutoff_mod.json.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import probe_lfo as P
import probe_lfo_level as Q
import probe_vfilter as V
import test_render_calibration as H

CACHE = ROOT / "calibration/probe_vcutoff_static.json"
GRID = [round(0.05 * i, 2) for i in range(1, 20)]


def main():
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t)
    pool = V.SerumPool()
    base = np.array(json.loads((ROOT / "calibration/probe_vfilter_serum.json").read_text())["base"])
    static = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    for c in GRID:
        if str(c) not in static:
            static[str(c)] = V.harmonics(pool.render(H.with_filter(tree0, c, 0.0, "MgL24"))).tolist()
            CACHE.write_text(json.dumps(static))
    tr = {c: V.transfer(np.array(static[str(c)]), base) for c in GRID}
    ok = (np.arange(1, V.NH + 1) * V.F0 < 16000) & (base > base.max() - 70)

    def equivalent(h):
        t = V.transfer(h, base)
        errs = np.array([np.sqrt(((tr[c][ok] - t[ok]) ** 2).mean()) for c in GRID])
        i = int(np.argmin(errs))
        if 0 < i < len(GRID) - 1:                                  # parabola through the best three
            a, b, c3 = errs[i - 1], errs[i], errs[i + 1]
            d = (a - c3) / (2 * (a - 2 * b + c3) + 1e-12)
            return GRID[i] + d * 0.05, float(errs[i])
        return GRID[i], float(errs[i])
    out = {}
    print("source          base amount pol   -> equivalent cutoff   moved   per unit amount")
    for src in ("env", "lfo"):
        for base_c in (0.3, 0.5):
            for amt in (10, 25):
                for bip in (0, 1):
                    t = H.with_filter(tree0, base_c, 0.0, "MgL24")
                    pp = {"kParamAmount": amt}
                    if bip:
                        pp["kParamBipolar"] = 1.0
                    if src == "env":
                        t["Env1"]["plainParams"] = {"kParamAttack": 0.0, "kParamHold": 0.0, "kParamDecay": 5.0, "kParamSustain": 1.0}
                        sid = 2
                    else:
                        t["LFO0"] = {"curveData": Q.flat(1.0), "pathData": {}, "curveDisplayName": "Custom",
                                     "plainParams": {"kParamRate": 1.0, "kParamBeatSync": 0.0}}
                        sid = 6
                    t["ModSlot0"] = {"destModuleID": 0, "destModuleParamID": 3, "destModuleParamName": "kParamFreq", "destModuleTypeString": "VoiceFilter",
                                     "plainParams": pp, "source": [sid, 0]}
                    eq, err = equivalent(V.harmonics(pool.render(t)))
                    moved = eq - base_c
                    out[f"{src}|{base_c}|{amt}|{bip}"] = {"equivalent": eq, "fit_rms_db": err, "moved": moved, "per_unit": moved / (amt / 100)}
                    print(f"{src:4s} {base_c:5.2f} {amt:4d}   {'bi ' if bip else 'uni'}  -> {eq:6.3f} (fit rms {err:4.1f} dB)  {moved:+.3f}  {moved / (amt / 100):+.3f}", flush=True)
    (ROOT / "calibration/probe_vcutoff_mod.json").write_text(json.dumps(out, indent=1))
    os._exit(0)


if __name__ == "__main__":
    main()
