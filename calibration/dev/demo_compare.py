#!/usr/bin/env python3
"""Rebuild a website demo from its transcribed notes on the real Serum preset and on the TinyHouse preset, and compare.

  demo_compare.py "<Preset name>" <demo.mp3> [transpose_semitones]

Plays calibration/transcripts/<demo>.json's note events through (a) Serum with all the preset's FX and (b) the
installed TinyHouse preset, writes both as WAV beside the demo's own audio in calibration/demo_renders/, and prints
how close each is to the demo: level, third-octave spectrum, side/mid, and the timing of the onsets. What the demo has
that Serum's plain render does not (drums, bus processing, other layers, mastering) shows up as the gap between them.
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import soundfile as sf
import test_render_calibration as H

SR = 44100
OUT = ROOT / "calibration/demo_renders"


def serum_render(tree, events, dur, tr):
    s = H.Serum()
    import tempfile as tf
    fd, path = tf.mkstemp(suffix=".bin")
    with os.fdopen(fd, "wb") as f:
        f.write(H.convert_preset_tree(tree))
    s.synth.load_state(path); os.unlink(path)
    s.synth.clear_midi()
    for st, en, n in events:
        s.synth.add_midi_note(int(n + tr), 100, float(st), float(max(en - st, 0.05)))
    s.engine.load_graph([(s.synth, [])]); s.engine.render(dur)
    return np.array(s.engine.get_audio(), dtype=np.float64)


def tinyhouse_render(name, events, dur, tr):
    st = json.load(open(ROOT / f"out/move/data/UserData/schwung/presets/tinyhouse/{H.d2m.ascii_name(name)}.json", encoding="utf-8"))["state"]
    args = []
    for s_, e_, n in events:
        args += [f"{s_:.3f},on,{int(n + tr)},100", f"{e_:.3f},off,{int(n + tr)},0"]
    exe = os.environ.get("RETRIG_PROBE", "retrig_probe.exe")     # the retrigger probe, built from the engine (not in the repo)
    with tempfile.TemporaryDirectory() as td:
        bf, rf = Path(td, "b.txt"), Path(td, "o.raw")
        bf.write_text(st, encoding="utf-8", newline="\n")
        r = subprocess.run([exe, str(ROOT / "out/move"), str(bf), str(rf), str(dur)] + args, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return np.fromfile(rf, dtype=np.float32).reshape(-1, 2).T.astype(np.float64)


def bands(x):
    return H.band_db(x)


def env10(x):
    m = (x[0] + x[1]) / 2
    n = len(m) // 441
    return 20 * np.log10(np.sqrt((m[:n * 441].reshape(n, 441) ** 2).mean(axis=1)) + 1e-9)


def onsets(e):
    d = np.diff(e); return np.where((d[1:] > 6) & (d[1:] > d[:-1]))[0] + 1


def main():
    name, mp3 = sys.argv[1], sys.argv[2]
    tr = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    demo, sr = sf.read(mp3, always_2d=True); demo = demo.T
    ev = json.loads(Path("calibration/transcripts", Path(mp3).stem + ".json").read_text(encoding="utf-8"))["events"]
    dur = demo.shape[1] / SR
    header, tree = H.load_preset(name)
    ser = serum_render(tree, ev, dur, tr)
    tab = tinyhouse_render(name, ev, dur, tr)
    tag = Path(mp3).stem.split(".")[0] + "_" + name.replace(" ", "_").replace("'", "")
    for lab, a in (("serum", ser), ("tinyhouse", tab), ("demo", demo)):
        sf.write(OUT / f"{tag}_{lab}.wav", a.T[:int(dur * SR)], SR)
    n = min(demo.shape[1], ser.shape[1], tab.shape[1])
    D, S, T = demo[:, :n], ser[:, :n], tab[:, :n]
    rms = lambda x: 20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-9)
    sm = lambda x: 10 * np.log10((((x[0] - x[1]) / 2) ** 2).sum() / ((((x[0] + x[1]) / 2) ** 2).sum() + 1e-20) + 1e-12)
    bd, bs, bt = bands(D), bands(S), bands(T)
    ok = np.isfinite(bd) & np.isfinite(bs) & np.isfinite(bt) & (bd > -70)
    print(f"{name}: {len(ev)} notes, {dur:.1f} s, transposed {tr:+d}")
    print(f"  level (rms dB)      demo {rms(D):6.1f}   serum {rms(S):6.1f}   tinyhouse {rms(T):6.1f}")
    print(f"  side/mid (dB)       demo {sm(D):6.1f}   serum {sm(S):6.1f}   tinyhouse {sm(T):6.1f}")
    print(f"  spectrum, rms band error vs demo (dB, shape only):   serum {np.sqrt(np.mean((bs[ok] - bd[ok]) ** 2)):5.1f}   tinyhouse {np.sqrt(np.mean((bt[ok] - bd[ok]) ** 2)):5.1f}   serum-vs-tinyhouse {np.sqrt(np.mean((bs[ok] - bt[ok]) ** 2)):5.1f}")
    ed, es, et = env10(D), env10(S), env10(T)
    k = min(len(ed), len(es), len(et))
    cc = lambda a, b: float(np.corrcoef(np.maximum(a[:k], -60), np.maximum(b[:k], -60))[0, 1])
    print(f"  loudness-shape correlation with the demo (10 ms):    serum {cc(ed, es):5.2f}   tinyhouse {cc(ed, et):5.2f}")
    od, os_ = onsets(ed), onsets(es)
    print(f"  onsets found: demo {len(od)}, serum {len(os_)}; transcript has {len(ev)} notes")
    print(f"  written to {OUT}")
    sys.stdout.flush(); os._exit(0)


if __name__ == "__main__":
    main()
