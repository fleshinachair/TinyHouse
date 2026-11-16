#!/usr/bin/env python3
"""Serum's Multisample oscillator level against the engine's (probe_sample_level.py found the Sample oscillator agrees within 0.3 dB; the
multisample is louder in Serum). The INIT - Brass Multisample template at three oscillator volumes, then the same preset pointed at other
factory SFZ sets (the embedded copy removed, so Serum reads the file from the Multisamples folder as the exporter does), each at three volumes.
Output: calibration/probe_multisample_level.json and a printed table.

   python calibration/dev/probe_multisample_level.py
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import test_render_calibration as H
import post_fit as PF
import serum_paths
from serum2_preset_loader.codec import decode_preset_file

d2m = H.d2m
SR = H.SR
SETS = [None, "Factory/Strings/Full Strings LE.sfz", "Factory/Strings/Violins Mute LE.sfz", "Factory/Keys/Baby Grand Piano.sfz", "Factory/Winds/Bass Clarinet LE.sfz"]
VOLUMES = (0.25, 0.5, 1.0)
NOTES, DUR, HOLD, A, B = [48, 55, 60, 63], 2.0, 1.9, 0.1, 1.5


def rms_db(runs):
    return float(10 * np.log10(np.mean([(r[:, int(A * SR):int(B * SR)] ** 2).mean() for r in runs]) + 1e-20))


def main():
    pres = next((Path(serum_paths.SERUM_ROOT) / "Presets" / "Factory").rglob("INIT - Brass Multisample.SerumPreset"))
    header, base = decode_preset_file(pres)
    base = H.strip_fx(base)
    serum = [H.Serum()]
    out = {}
    print(f"{'set':40s}  vol   serum   engine   serum-engine   (pot, master)", flush=True)
    for sfz in SETS:
        for vol in VOLUMES:
            t = copy.deepcopy(base)
            t["Oscillator0"]["plainParams"]["kParamVolume"] = vol
            if sfz is not None:
                node = t["Oscillator0"]["MultiSampleOsc0"]
                node["sfzPathRelative"] = sfz
                node.pop("embedded_sfz", None); node.pop("files", None)
            if serum[0].audio_seconds > 600:
                serum[0] = H.Serum()
            try:
                s = rms_db([serum[0].render(t, NOTES, DUR, HOLD)])
            except Exception as e:
                print(sfz, vol, "Serum failed:", str(e)[:80], flush=True); serum[0] = H.Serum(); continue
            m = d2m.map_to_tablor(d2m.to_vital(d2m.physical(t, header)), {}, H.SERUM_TABLES, H.WT_OUT, "Probe")
            e = rms_db([H.run_cli(PF.setk(d2m.blob(m), "fx_on", 0), NOTES, DUR, HOLD) for _ in range(2)])
            name = sfz or "Factory/Winds/Trumpets LE.sfz (as stored)"
            out[f"{name}@{vol}"] = {"vol": vol, "serum_db": s, "engine_db": e, "pot": m["params"].get("smp_level"), "volume": m["params"].get("volume"), "set": m["params"].get("smp_set")}
            print(f"{name[-40:]:40s}  {vol:.2f}  {s:+6.1f}  {e:+6.1f}   {s - e:+6.1f}        ({m['params'].get('smp_level')}, {m['params'].get('volume')})  {str(m['params'].get('smp_set'))[-30:]}", flush=True)
    Path("calibration/probe_multisample_level.json").write_text(json.dumps(out, indent=1))
    sys.stdout.flush(); os._exit(0)


if __name__ == "__main__":
    main()
