#!/usr/bin/env python3
"""The convolution reverb end to end: a noise burst through one Conv node (and nothing else) rendered by Serum and by the engine from the converter's own mapping (fxmap.conv_plan, the
IR set, the engine's convolver), compared in octave groups over time. Prints, for each case, the engine's level minus Serum's in dB for each window and band, and the overall level gap.

  python calibration/dev/probe_conv_engine.py [relative IR path] [Wet Size Decay IpTrim]      (defaults: Baroque Ballroom, 35 39.1 12.27 -9.3)
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, json, os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np, soundfile as sf
import deephouse2move as d2m
import sample_export as SE
import serum_paths
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file

SR = H.SR
BANDS = ((125, 500), (500, 2000), (2000, 4000), (4000, 8000), (8000, 16000))
WINS = ((0.0, 0.3), (0.3, 0.6), (0.6, 1.0), (1.0, 1.4), (1.4, 1.8), (1.8, 2.2), (2.2, 2.6), (2.6, 3.0), (3.0, 4.0))


def prof(x):
    m = x.mean(0) if x.ndim == 2 else x
    out = []
    for a, b in WINS:
        seg = m[int(a * SR):int(b * SR)]
        sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
        f = np.fft.rfftfreq(len(seg), 1 / SR)
        out.append([10 * np.log10(sp[(f >= lo) & (f < hi)].sum() + 1e-20) for lo, hi in BANDS])
    return np.array(out)


def main():
    rel = sys.argv[1] if len(sys.argv) > 1 else "Factory/Medium/Baroque Ballroom.flac"
    wet, size, decay, trim = (float(v) for v in sys.argv[2:6]) if len(sys.argv) >= 6 else (35.0, 39.1, 12.27, -9.3)
    damping = float(sys.argv[6]) if len(sys.argv) >= 7 else None
    header, tree = decode_preset_file(ROOT / "Spectral_Strings_4.serumpreset")
    node = next(n for n in tree["FXRack0"]["FX"] if "FXConv" in n)
    x, sr = sf.read(str(Path(serum_paths.SERUM_ROOT) / "Impulses" / rel), dtype="float64")
    x = x[:, None] if x.ndim == 1 else x
    c = node["FXConv"]
    c.pop("embeddedIR", None)
    pp = {"kParamWet": wet, "kParamSize": size, "kParamIpTrim": trim}
    if decay < 35:
        pp["kParamDecay"] = decay
    if damping is not None:
        pp["kParamDamping"] = damping
    c.update(relativePathToIR=rel, numFrames=int(len(x)), numChannels=int(x.shape[1]), sampleRate=int(sr), plainParams=pp)
    t = copy.deepcopy(tree)
    for r in ("FXRack0", "FXRack1", "FXRack2"):
        t[r]["FX"] = []
    t["FXRack0"]["FX"] = [node]
    for i in (0, 1, 2):
        t[f"Oscillator{i}"]["plainParams"] = {"kParamEnable": 0.0, "kParamVolume": 0.0}
    t["Oscillator3"]["plainParams"] = {"kParamEnable": 1.0, "kParamVolume": 0.1}
    t["VoiceFilter0"]["plainParams"] = {"kParamEnable": 0.0}
    for i in range(64):
        t[f"ModSlot{i}"] = {"plainParams": "default"}
    t["Env0"] = {"plainParams": {"kParamAttack": 0.0, "kParamDecay": 0.3, "kParamSustain": 0.0, "kParamRelease": 0.0}}
    S = H.Serum()
    ref = S.render(t, [60], 6.0, 0.4)
    td = Path(tempfile.mkdtemp()); (td / "tables").mkdir(); (td / "Wavetables").mkdir()
    SE.BROWSER_DIR = td
    v = d2m.to_vital(d2m.physical(t, header)); v["preset_name"] = "probe_conv"; v["serum2move"]["name"] = "probe_conv"
    m = d2m.map_to_tablor(json.loads(json.dumps(v)), {}, H.SERUM_TABLES, H.WT_OUT, "Imports")
    print("conv params:", {k: m["params"].get(k) for k in ("conv_set", "conv_mix", "rev_mix")}, [n for n in m.get("notes", []) if "onvol" in n], flush=True)
    # the engine: the same noise (its own noise source) through the converter's mapping; the rest of the chain at the converter's defaults
    state = d2m.blob(m)
    eng = H.run_cli(state, [60], 6.0, 0.4)
    pr, pe = prof(ref), prof(eng)
    if os.environ.get('ABS'):
        import re
        p0 = prof(H.run_cli(re.sub(r'conv_mix=\d+', 'conv_mix=0', state), [60], 6.0, 0.4))
        for (a, b), x0, x1 in zip(WINS, p0, pe):
            print(f'engine abs {a:3.1f}-{b:3.1f}s  conv off ' + ' '.join(f'{v:7.1f}' for v in x0) + '   conv on ' + ' '.join(f'{v:7.1f}' for v in x1))
    print(f"{rel}  Wet {wet:g} Size {size:g} Decay {decay:g} IpTrim {trim:g} Damping {damping}")
    print("window      serum band dB (125-500 500-2k 2-4k 4-8k 8-16k)      engine - serum")
    for (a, b), s_, e_ in zip(WINS, pr, pe):
        print(f"{a:3.1f}-{b:3.1f}s  " + " ".join(f"{v:6.1f}" for v in s_) + "      " + " ".join(f"{d:+6.1f}" for d in e_ - s_))
    tot = lambda p: 10 * np.log10(np.sum(10 ** (p[1:4] / 10)))
    print(f"level (0.3-2 s, all bands): engine - serum = {tot(pe) - tot(pr):+.1f} dB")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
