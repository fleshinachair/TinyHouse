#!/usr/bin/env python3
"""What do Serum's Chorus / Flanger / Phaser / HyperDimension add that the engine's width stage does not?

For every preset with one of them in the main rack, a held chord is rendered by Serum with the full chain and with those
effects switched off, and by the installed TinyHouse preset. Two measures the third-octave loss cannot see:
  fine-structure motion  the std over time of the spectrum's log-magnitude per FFT bin (4096 samples, hop 2048, level
                         per frame removed so the envelope does not count), averaged over the bins between 300 Hz and
                         8 kHz that carry sound: swept notches and phase movement make it large, a steady sound is small
  decorrelation          1 - |L/R correlation| of the held part
The effect's worth is Serum-full minus Serum-without; "kept" is how much of that TinyHouse shows (its value minus Serum-without).
Output: calibration/modfx_worth.json
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fxmap
import fx_contribution as FC
import test_render_calibration as H

SR = H.SR
KINDS = {"Chorus", "Flanger", "Phaser", "HyperD"}


def stats(x):
    a = x[:, int(0.5 * SR):int(3.0 * SR)]
    mono = (a[0] + a[1]) / 2
    n, hop = 4096, 2048
    win = np.hanning(n)
    fr = np.array([20 * np.log10(np.abs(np.fft.rfft(mono[i:i + n] * win)) + 1e-9) for i in range(0, len(mono) - n, hop)])
    fr = fr - fr.mean(axis=1, keepdims=True)                      # the level of each frame removed
    f = np.fft.rfftfreq(n, 1 / SR)
    keep = (f > 300) & (f < 8000) & (fr.mean(axis=0) > -60)
    motion = float(fr[:, keep].std(axis=0).mean()) if keep.any() else 0.0
    corr = float(np.corrcoef(a[0], a[1])[0, 1]) if a.std() > 0 else 1.0
    return motion, 1 - abs(corr)


def main():
    holder = []
    serum = lambda: holder[0] if holder else (holder.append(H.Serum()) or holder[0])
    src = {H.d2m.ascii_name(p.stem): p.stem for p in H.PRESET_DIR.glob("*.SerumPreset")}
    out = {}
    for f in sorted(Path("out/move/data/UserData/schwung/presets/tinyhouse").glob("*.json")):
        n = src.get(f.stem, f.stem)
        header, tree = H.load_preset(n)
        have = sorted({fxmap.kind_of(x) for x in tree.get("FXRack0", {}).get("FX", []) or [] if fxmap.enabled(x)} & KINDS)
        if not have or not H.usable(tree):
            continue
        notes = H.sounding_notes(tree, n)
        full = serum().render(tree, notes, 3.5, 3.0)
        off = serum().render(FC.without_all(tree, KINDS), notes, 3.5, 3.0)
        st = json.loads(f.read_text(encoding="utf-8"))["state"]
        tab = H.run_cli(st, notes, 3.5, 3.0)
        a, b, c = stats(full), stats(off), stats(tab)
        out[f.stem] = {"kinds": have, "serum_full": a, "serum_off": b, "tinyhouse": c}
        print(f"{f.stem:28s} {','.join(have):22s} motion full {a[0]:5.2f} off {b[0]:5.2f} tinyhouse {c[0]:5.2f} | decorr full {a[1]:.2f} off {b[1]:.2f} tinyhouse {c[1]:.2f}", flush=True)
        if holder and holder[0].audio_seconds > 1050:
            print("render limit reached"); break
    (H.OUT / "modfx_worth.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    os._exit(0)


if __name__ == "__main__":
    main()
