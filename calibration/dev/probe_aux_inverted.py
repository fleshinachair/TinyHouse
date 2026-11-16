"""Serum's kParamAuxInverted on a route's aux source (Lead-style routes whose second source is a macro).

Measured on FX - Office Telephone (Osc A volume knob 0, all of its level from LFO2 -> volume, aux = Macro 3, +80 %), every other
slot removed and Osc B off, rms of one 2 s note:
    inverted    macro 0 / 50 / 100 %:  -29.1 / -41.0 / silent dB
    not inverted                    :  silent / -41.0 / -29.1 dB
so an inverted aux works as 1 - x. deephouse2move.aux_scale applies it before the aux curve (the curve case is not measured).
45 of the 626 factory presets have an inverted aux (9 on a voice-filter cutoff, 9 on an oscillator volume, 8 on the octave).
Usage: python calibration/dev/probe_aux_inverted.py <FX - Office Telephone.SerumPreset>
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import sys, copy, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import midi_ab as M
from serum2_preset_loader.codec import decode_preset_file

t0 = decode_preset_file(sys.argv[1]); t0 = t0[1] if isinstance(t0, tuple) else t0
notes = [(0.0, 1.0, 60, 100)]
for inv in (1, 0):
    for macro in (0, 50, 100):
        t = copy.deepcopy(t0)
        for i in range(64):
            if i != 1 and isinstance(t.get(f"ModSlot{i}"), dict) and "source" in t[f"ModSlot{i}"]:
                t[f"ModSlot{i}"] = {"plainParams": "default"}
        t["ModSlot1"]["plainParams"]["kParamAuxInverted"] = float(inv)
        t["Macro3"]["plainParams"] = {"kParamValue": macro}
        t["Oscillator1"]["plainParams"] = dict(t["Oscillator1"]["plainParams"], kParamEnable=0.0)
        x = np.asarray(M.serum_render(t, notes, 2.0))
        print(f"inverted={inv} macro3={macro:3d}  rms {20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-12):7.1f} dB")
