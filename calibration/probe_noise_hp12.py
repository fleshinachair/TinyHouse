#!/usr/bin/env python3
"""Serum's 'S2 Noises/HP12 Pink|White Noise' samples in the noise slot, at several Color settings: octave band levels (62.5 Hz .. 16 kHz) at oscillator volume 0.75,
voice filter off. Does Color still act on a sample, and how does it combine with the sample's own 12 dB/octave high-pass? Output: calibration/probe_noise_hp12.json."""
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import probe_noise_color as N
import probe_noise_sample as S
SR = N.SR
if __name__ == "__main__":
    serum, base = N.start()
    res = {}
    cases = [("White", None, None)] + [("S2 Noises/HP12 Pink Noise (Stereo).flac", c, "p") for c in (None, 0.0, 0.3, 0.6, 0.79, 1.0)] + [("S2 Noises/HP12 White Noise (Stereo).flac", c, "w") for c in (None, 0.79)] + [("S2 Noises/Pink Noise (Stereo).flac", c, "q") for c in (None, 0.79)]
    for rel, c, _ in cases:
        b = copy.deepcopy(base)
        pp = {} if c is None else {"kParamColor": c}
        node = {"plainParams": {"kParamNoiseType": "White", **pp}} if rel == "White" else {"relativePathToNoiseSample": rel, "plainParams": pp or "default"}
        b["Oscillator3"]["NoiseOsc3"] = node
        try:
            xs = [serum.render(b, [48], 2.5, 2.4) for _ in range(2)]
        except Exception as e:
            print(rel, c, "failed", str(e)[:50], flush=True); serum, base = N.start(); continue
        m = [x.mean(axis=0)[int(0.5 * SR):int(2.0 * SR)] for x in xs]
        o = np.mean([S.octs(v, SR) for v in m], axis=0); lv = 10 * np.log10(np.mean([(v ** 2).mean() for v in m]) + 1e-20)
        res[f"{rel}|{c}"] = {"oct": o.round(1).tolist(), "level_db": round(float(lv), 1)}
        print("%-44s color %-5s level %6.1f  oct re max %s" % (rel[-44:], c, lv, np.round(o - o.max()).astype(int)), flush=True)
    Path("calibration/probe_noise_hp12.json").write_text(json.dumps(res, indent=1))
    sys.stdout.flush(); os._exit(0)
