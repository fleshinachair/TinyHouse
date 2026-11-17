#!/usr/bin/env python3
"""What a main source 23 (a 'constant' aux in probe_aux_source.py) routed to an oscillator's Pan does: a factory preset that uses it, rendered as it is and with the
route's amount set to 0; the stereo balance (side / mid) and the level of each. Output: printed.   probe_src23_pan.py ["preset name"]"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
import numpy as np
import fx_probe as P
import test_render_calibration as H
SR = H.SR
serum, base = P.start()
name = sys.argv[1] if len(sys.argv) > 1 else "EP - Gentle DX Synth Keys"
from serum2_preset_loader.codec import decode_preset_file
FACT = Path("C:/Users/Mat/OneDrive/Documents/Xfer/Serum 2 Presets/Presets/Factory")
_, pre = decode_preset_file(next(FACT.rglob(name + ".SerumPreset")))
idx = [i for i in range(64) if pre.get(f"ModSlot{i}", {}).get("destModuleParamName") == "kParamPan"]
print("pan routes:", [(i, pre[f"ModSlot{i}"].get("source"), pre[f"ModSlot{i}"].get("destModuleTypeString"), pre[f"ModSlot{i}"].get("destModuleID"), pre[f"ModSlot{i}"].get("plainParams")) for i in idx])
for i in idx[:1]:
    print("osc", pre[f"Oscillator{pre[f'ModSlot{i}'].get('destModuleID')}"].get("plainParams"))
def stats(x):
    l, r = x[0, int(0.3 * SR):int(1.2 * SR)], x[1, int(0.3 * SR):int(1.2 * SR)]
    m, s = (l + r) / 2, (l - r) / 2
    return "level %.1f dB  side/mid %.1f dB  L/R %.1f dB  corr %.3f" % (10 * np.log10((m ** 2).mean() + (s ** 2).mean() + 1e-20), 10 * np.log10((s ** 2).mean() / ((m ** 2).mean() + 1e-20) + 1e-20), 10 * np.log10((l ** 2).mean() / ((r ** 2).mean() + 1e-20)), np.corrcoef(l, r)[0, 1])
for note in (48, 60):
    for label, amt in (("as stored", None), ("route 0", 0.0)):
        p = copy.deepcopy(pre)
        if amt is not None:
            for i in idx: p[f"ModSlot{i}"]["plainParams"] = dict(p[f"ModSlot{i}"].get("plainParams", {}), kParamAmount=amt)
        print("note", note, label.ljust(10), stats(serum.render(p, [note], 1.5, 1.2, vel=100)), flush=True)
os._exit(0)
