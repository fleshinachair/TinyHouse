#!/usr/bin/env python3
"""Serum's Sample oscillator level law against the engine's: the same file through both at the same oscillator volume.

Serum plays a Sample oscillator's file at some gain over the file's own level; the engine plays the exported set at the file's level times the
level pot squared (sample.h), with the pot from the converter's wavetable law. Whatever Serum adds on top is measured here, on files that span
30 dB of recorded level, at three oscillator volumes, so a constant offset and a normalisation can be told apart. The two INIT Multisample
templates go through the same path as they are. Output: calibration/probe_sample_level.json and a printed table.

   python calibration/dev/probe_sample_level.py
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np, soundfile as sf
import test_render_calibration as H
import post_fit as PF
import sample_export as SE
import serum_paths
from serum2_preset_loader.codec import decode_preset_file

d2m = H.d2m
SR = H.SR
FILES = ["Factory Non-Tonal/Noises/SOR/Atmos 20.wav", "Factory Non-Tonal/Loop/Perc Loop/90 Bongo.flac", "Factory/Plucked/On Far Away Planet.flac",
         "Factory/Synth/DX Ensemble 1 C2.flac", "Factory/Vox/Oo Long High.flac", "Factory/Synth/16 Voice C3.flac"]
VOLUMES = (0.25, 0.5, 1.0)
NOTE, DUR, HOLD, A, B = 60, 2.0, 1.9, 0.1, 1.5


def rms_db(runs):
    return float(10 * np.log10(np.mean([(r[:, int(A * SR):int(B * SR)] ** 2).mean() for r in runs]) + 1e-20))


def file_rms_db(path):
    x, sr = sf.read(str(path), dtype="float32")
    if x.ndim == 2:
        x = x.mean(axis=1)
    seg = x[int(A * sr):int(min(B, len(x) / sr - 0.05) * sr)]
    return float(10 * np.log10((seg ** 2).mean() + 1e-20)), len(x), sr, (1 if sf.info(str(path)).channels == 1 else 2)


def main():
    pres = next((Path(serum_paths.SERUM_ROOT) / "Presets" / "Factory").rglob("INIT - Brass Multisample.SerumPreset"))
    header, base = decode_preset_file(pres)
    base = H.strip_fx(base)
    serum = [H.Serum()]

    def render_serum(tree):
        if serum[0].audio_seconds > 600:
            serum[0] = H.Serum()
        return rms_db([serum[0].render(tree, [NOTE], DUR, HOLD)])

    def render_engine(tree):
        m = d2m.map_to_tablor(d2m.to_vital(d2m.physical(tree, header)), {}, H.SERUM_TABLES, H.WT_OUT, "Probe")
        st = PF.setk(d2m.blob(m), "fx_on", 0)
        return rms_db([H.run_cli(st, [NOTE], DUR, HOLD) for _ in range(2)]), m["params"]

    out = {}
    print(f"{'file':44s} {'file rms':>8s}  vol   serum   engine   serum-engine   (pot)", flush=True)
    for rel in FILES:
        path = SE.find_sample(rel)
        if path is None:
            print("missing", rel); continue
        frms, frames, sr, ch = file_rms_db(path)
        for vol in VOLUMES:
            t = copy.deepcopy(base)
            t["Oscillator0"]["plainParams"] = {"kParamType": "kOsc_Sample", "kParamVolume": vol}
            t["Oscillator0"]["SampleOsc0"] = {"samplePathRelative": rel, "baseNote": NOTE, "sampleRate": sr, "numFrames": frames, "numChannels": ch, "plainParams": "default"}
            s = render_serum(t)
            e, P = render_engine(t)
            out[f"{rel}@{vol}"] = {"file_rms_db": frms, "vol": vol, "serum_db": s, "engine_db": e, "pot": P.get("smp_level"), "volume": P.get("volume")}
            print(f"{rel[-44:]:44s} {frms:+8.1f}  {vol:.2f}  {s:+6.1f}  {e:+6.1f}   {s - e:+6.1f}        ({P.get('smp_level')}, master {P.get('volume')})", flush=True)
    for name in ("INIT - Brass Multisample", "INIT - Strings Multisample"):
        p = next((Path(serum_paths.SERUM_ROOT) / "Presets" / "Factory").rglob(name + ".SerumPreset"))
        hdr, tree = decode_preset_file(p)
        t = H.strip_fx(tree)
        s = render_serum(t)
        m = d2m.map_to_tablor(d2m.to_vital(d2m.physical(t, hdr)), {}, H.SERUM_TABLES, H.WT_OUT, "Probe")
        e = rms_db([H.run_cli(PF.setk(d2m.blob(m), "fx_on", 0), [NOTE], DUR, HOLD) for _ in range(2)])
        vol = tree["Oscillator0"]["plainParams"]["kParamVolume"]
        out[name] = {"vol": vol, "serum_db": s, "engine_db": e, "pot": m["params"].get("smp_level"), "volume": m["params"].get("volume"), "multisample": True}
        print(f"{name:44s} {'multi':>8s}  {vol:.2f}  {s:+6.1f}  {e:+6.1f}   {s - e:+6.1f}        ({m['params'].get('smp_level')}, master {m['params'].get('volume')})", flush=True)
    Path("calibration/probe_sample_level.json").write_text(json.dumps(out, indent=1))
    sys.stdout.flush(); os._exit(0)


if __name__ == "__main__":
    main()
