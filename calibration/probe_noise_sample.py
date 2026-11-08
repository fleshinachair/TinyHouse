#!/usr/bin/env python3
"""Serum's noise slot playing a sample (a hum, a breath, a j106 noise ...): octave band levels and overall level in Serum at oscillator volume 0.75, voice filter off,
against the sample file's own octave spectrum and rms, and against Serum's White noise at the same volume. Output: calibration/probe_noise_sample.json."""
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np, soundfile as sf
import probe_noise_color as N
import sample_export as SE
SR = N.SR
EDGES = [62.5 * 2 ** i for i in range(9)]
def octs(m, sr):
    n = min(len(m), 1 << 18); m = m[:n]
    s = np.abs(np.fft.rfft(m * np.hanning(len(m)))) ** 2; f = np.fft.rfftfreq(len(m), 1 / sr)
    return np.array([10 * np.log10(s[(f >= lo) & (f < lo * 2)].sum() + 1e-20) for lo in EDGES])
SAMPLES = ["Organics/AC hum1.wav", "Organics/AC hum2.wav", "Analog/J 60.wav", "Analog/J106.wav", "Organics/H-Breath.wav", "Analog/AlphaNz.wav", "Analog/MicrKrg Noise.wav"]
if __name__ == "__main__":
    serum, base = N.start()
    res = {}
    def render(node):
        b = copy.deepcopy(base); b["Oscillator3"]["NoiseOsc3"] = node
        xs = [serum.render(b, [48], 2.5, 2.4) for _ in range(2)]
        m = [x.mean(axis=0)[int(0.5 * SR):int(2.0 * SR)] for x in xs]
        return np.mean([octs(v, SR) for v in m], axis=0), 10 * np.log10(np.mean([(v ** 2).mean() for v in m]) + 1e-20)
    o, lv = render({"plainParams": {"kParamNoiseType": "White"}})
    res["White"] = {"oct": o.round(1).tolist(), "level_db": round(float(lv), 1)}
    print("White  level %.1f oct %s" % (lv, np.round(o - o.max()).astype(int)), flush=True)
    for rel in SAMPLES:
        fp = SE.find_sample(rel)
        if not fp: print("no file", rel); continue
        x, sr = sf.read(str(fp)); xm = x if x.ndim == 1 else x.mean(1)
        so = octs(xm, sr); srms = 10 * np.log10((xm ** 2).mean() + 1e-20)
        try: o, lv = render({"relativePathToNoiseSample": rel, "plainParams": {}})
        except Exception as e: print(rel, "failed", str(e)[:60], flush=True); serum, base = N.start(); continue
        res[rel] = {"serum_oct": o.round(1).tolist(), "serum_level_db": round(float(lv), 1), "sample_oct": so.round(1).tolist(), "sample_rms_db": round(float(srms), 1), "frames": len(xm), "sr": sr}
        print("%-34s serum level %.1f (white %.1f) sample rms %.1f dB len %d | serum oct %s | sample oct %s" % (rel[-34:], lv, res["White"]["level_db"], srms, len(xm), np.round(o - o.max()).astype(int), np.round(so - so.max()).astype(int)), flush=True)
    Path("calibration/probe_noise_sample.json").write_text(json.dumps(res, indent=1))
    sys.stdout.flush(); os._exit(0)
