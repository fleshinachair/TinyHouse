#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
# (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
"""Vital's oscillator-to-oscillator FM (type 7, FM by oscillator A) on two sine tables: osc 1 (carrier, a sine) phase-modulated by osc 2 (a sine at +12 st, level 0).

    python calibration/vital_fm_probe.py [--engine]

Vital phase-modulates the carrier by 6 cycles x amount x the modulator (synth_oscillator.cpp), so the harmonics of the carrier follow Bessel functions of 2 pi 6 amount. The probe prints the levels of harmonics
1, 3, 5, 7 (re the plain carrier) from the plugin; with --engine the same preset goes through the converter and the engine.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "calibration"))
import vital_render as VR                                           # noqa: E402
import vital_route_probe as RP                                      # noqa: E402

SR, NOTE = 44100, 60
F0 = 440.0 * 2 ** ((NOTE - 69) / 12) / 2                 # the probe preset sounds an octave below the MIDI note


def preset(amount: float, typ: int = 10, trans: float = 24.0, mlevel: float = 0.0, mamp: float = 1.0) -> bytes:
    n = np.arange(2048)
    sine = np.sin(2 * np.pi * n / 2048).astype("<f4")
    d = RP.base_preset()
    s = d["settings"]
    for o, amp in ((1, 1.0), (2, mamp)):
        kf = s["wavetables"][o - 1]["groups"][0]["components"][0]["keyframes"]
        b64 = base64.b64encode((sine * amp).astype("<f4").tobytes()).decode()
        kf[:] = [{"position": 0, "wave_data": b64}, {"position": 255, "wave_data": b64}]
        s[f"osc_{o}_on"], s[f"osc_{o}_transpose"], s[f"osc_{o}_tune"] = 1.0, 0.0, 0.0
        s[f"osc_{o}_wave_frame"], s[f"osc_{o}_random_phase"] = 0.0, 0.0
        s[f"osc_{o}_distortion_type"], s[f"osc_{o}_spectral_morph_type"] = 0.0, 0.0
        s[f"osc_{o}_unison_voices"] = 1.0
    s["osc_1_level"], s["osc_2_level"] = 1.0, mlevel
    s["osc_2_transpose"] = trans
    s["osc_1_distortion_type"], s["osc_1_distortion_amount"] = float(typ), float(amount)
    s["filter_1_on"] = 0.0
    return json.dumps(d).encode()


def harmonics(x: np.ndarray, ks, f0: float = F0) -> dict:
    t = np.arange(32768) / SR
    seg = x[int(0.2 * SR):int(0.2 * SR) + 32768] * np.hanning(32768)
    return {k: 2 * abs((seg * np.exp(-2j * np.pi * k * f0 * t)).sum()) / np.hanning(32768).sum() for k in ks}


def engine_render(amt: float, typ: int, trans: float, mlevel: float, root: Path, ref_scale: float = 1.0) -> np.ndarray:
    import subprocess
    import tempfile
    sys.path.insert(0, str(ROOT / "converter"))
    import vital_import as VI
    m = VI.map_vital(json.loads(preset(amt, typ, trans, mlevel)), "rm", root / "move/data/UserData/UserLibrary/Wavetables", "Probe")
    m["params"]["volume"] = 32
    tmp = Path(tempfile.mkdtemp())
    (tmp / "b.txt").write_text(VI.blob(m), encoding="utf-8", newline="\n")
    subprocess.run([str(ROOT / "calibration/tablor_cli.exe"), str(root / "move"), str(tmp / "b.txt"), str(tmp / "o.raw"), "1.6", "1.2", "100", str(NOTE), "1"], check=True, capture_output=False)
    return np.frombuffer((tmp / "o.raw").read_bytes(), "<f4").reshape(-1, 2).T.copy()[0]


HARM = (1, 3, 5, 7, 9)


def show(label, x, f0, ref):
    h = harmonics(x, HARM, f0)
    print(f"{label}: " + "  ".join(f"h{k} {20 * np.log10(h[k] / ref + 1e-9):6.1f} dB" for k in HARM))


def engine_main(a) -> None:
    import tempfile
    root = Path(tempfile.mkdtemp())
    f0 = 440.0 * 2 ** ((NOTE - 69) / 12)
    ref = harmonics(engine_render(0.0, 0, a.trans, a.mlevel, root), (1,), f0)[1]
    for amt in a.amounts:
        show(f"engine amount {amt:4.2f}", engine_render(amt, 7, a.trans, a.mlevel, root), f0, ref)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--amounts", type=float, nargs="*", default=[0.02, 0.05, 0.1, 0.2, 0.4])
    ap.add_argument("--mlevel", type=float, default=0.0)
    ap.add_argument("--trans", type=float, default=12.0)
    ap.add_argument("--engine", action="store_true", help="measure the converted preset through the engine instead of the plugin")
    a = ap.parse_args()
    if a.engine:
        engine_main(a)
        return
    host = VR.VitalHost()
    ref = harmonics(np.asarray(host.render(preset(0.0, 0, a.trans, a.mlevel), ((NOTE, 100, 0.0, 1.2),), 1.6))[0], (1,))[1]
    for amt in a.amounts:
        show(f"vital  amount {amt:4.2f}", np.asarray(host.render(preset(amt, 7, a.trans, a.mlevel), ((NOTE, 100, 0.0, 1.2),), 1.6))[0], F0, ref)


if __name__ == "__main__":
    main()
