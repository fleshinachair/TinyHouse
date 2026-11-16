#!/usr/bin/env python3
"""The effects block's Filter FX (the engine's fxf_* stage) against Serum's Filter FX at a static cutoff, to see where the converter's cutoff mapping for it is off.
A saw through one Filter FX model at Reso 10 (no voice filter, no modulation); the frequency where the transfer first falls 10 dB below the unfiltered saw is read
in both (interpolated between harmonics), and the engine's cutoff pot is searched until it agrees. Prints, per cutoff, the pot that matches and how far the
converter's formula (deephouse2move.apply_filter2) is from it.   Usage: probe_fxf_static.py [model] [norms,...]   Output: calibration/probe_fxf_static_<model>.json"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import deephouse2move as d2m
import probe_vfilter as V
import test_render_calibration as H
import fx_fingerprint as FP

SR = H.SR
MODEL = sys.argv[1] if len(sys.argv) > 1 else "MgL18"
NORMS = [float(x) for x in (sys.argv[2] if len(sys.argv) > 2 else "0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9").split(",")]
NH, WIN = 60, 0.06


def harm(x, f0, t0=0.6):
    m = x[0, int(t0 * SR):int((t0 + WIN) * SR)]
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)), 1 << 15))
    f = np.fft.rfftfreq(1 << 15, 1 / SR)
    return np.array([20 * np.log10(sp[max(np.searchsorted(f, k * f0) - 3, 0):np.searchsorted(f, k * f0) + 4].max() + 1e-12) for k in range(1, NH + 1)])


def f10(h, base, f0):
    """Where the transfer first falls 10 dB under the unfiltered saw, in Hz, interpolated between the harmonics either side (None if it never does)."""
    t = h - base
    below = np.where(t < -10)[0]
    if below.size == 0:
        return None
    k = int(below[0])
    if k == 0:
        return f0
    a, b = t[k - 1], t[k]
    return f0 * (k + (a + 10) / max(a - b, 1e-6))


def main():
    _, base_t = H.load_preset("Keys - Loop")
    tree0 = H.init_tree(base_t)
    _, oskar = H.load_preset("Bass - oskar med k")
    node = next(n for n in FP.rack0(oskar) if FP.kind_of(n) == "Filter")
    emodel = os.environ.get("FXF_ENGINE") or ("MgL24" if MODEL == "default" else MODEL)      # the model whose engine type stands in (apply_filter2 maps the default type to MgL24)
    fkey = d2m.filter_model_key(emodel, None)
    idx, _ = d2m.FILTER_MAP.get(emodel, (None, False))
    if fkey in d2m.FILTER_MODELS:
        idx = int(d2m.FILTER_MODELS[fkey]["type"])
    pool = V.SerumPool()
    _, m0 = H.tablor_render(tree0, [48], 0.3, 0.2)
    out = {}
    for c in NORMS:
        hz = 8.18 * 2697.0 ** c
        note = int(min(max(round(69 + 12 * np.log2(hz / 15 / 440)), 24), 84))
        V.NOTE = note
        f0 = 440 * 2 ** ((note - 69) / 12)
        base_s = harm(pool.render(tree0), f0)
        t = copy.deepcopy(tree0)
        nd = copy.deepcopy(node)
        nd["FXFilter"]["plainParams"] = {"kParamFreq": c, "kParamReso": 10.0} | ({} if MODEL == "default" else {"kParamType": MODEL})
        t["FXRack0"]["FX"] = [nd]
        hs = harm(pool.render(t), f0)
        fs = f10(hs, base_s, f0)

        def engine(pot):
            m = copy.deepcopy(m0)
            m["params"].update({"fxf_on": 1, "fxf_type": idx, "fxf_freq": int(pot), "fxf_env": 64, "fxf_res": d2m.filter_res_pot(emodel, 10.0)})
            return harm(H.run_cli(d2m.blob(m), [note], 1.4, 1.3), f0)
        m_off = copy.deepcopy(m0)
        base_e = harm(H.run_cli(d2m.blob(m_off), [note], 1.4, 1.3), f0)
        cur = c * d2m.CUTOFF_NOTES * d2m.CAL["cutoff_scale"] + d2m.CAL["cutoff_offset"] - 8 + d2m.filter_cutoff_delta(fkey, c)
        best, rows = None, []
        for pot in range(int(max(cur - 14, 0)), int(min(cur + 15, 127)) + 1, 2):
            fe = f10(engine(pot), base_e, f0)
            rows.append((pot, fe))
        good = [(p, f) for p, f in rows if f is not None]
        if fs is None or len(good) < 2:
            print(f"norm {c}: Serum {fs} engine {good[:2]}", flush=True)
            continue
        # the pot whose f10 equals Serum's, interpolated in log frequency
        lp = np.log2([f for _, f in good]); pp = np.array([p for p, _ in good], float)
        need = float(np.interp(np.log2(fs), lp, pp))
        he = engine(round(need))
        k = np.where(hs - base_s > -40)[0]                       # the harmonics Serum leaves audible
        rms = float(np.sqrt(np.mean(((hs - base_s) - (he - base_e))[k] ** 2))) if k.size else 0.0
        out[str(c)] = {"rms_db": rms, "note": note, "serum_f10": fs, "pot": need, "formula": float(cur), "correction": need - float(cur)}
        print(f"norm {c:.2f} (note {note}) Serum f10 {fs:7.0f} Hz  engine pot {need:6.1f}  formula {cur:6.1f}  correction {need - cur:+5.1f} st  shape rms {rms:4.1f} dB", flush=True)
    Path(f"calibration/probe_fxf_static_{MODEL}{"_as_" + emodel if emodel != MODEL else ""}.json").write_text(json.dumps(out, indent=1))
    os._exit(0)


main()
