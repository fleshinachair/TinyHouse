#!/usr/bin/env python3
"""Play a preset's own demo MIDI through Serum (full chain) and through the installed TinyHouse preset, and compare.

  midi_ab.py [--midi-dir DIR] [--out DIR] [--tail S] [name ...]       names are preset names ("Lead - Lynnic"); default: every preset that has a demo file

For each preset: DIR/<preset> - <key> - <bpm>.mid is read (notes, velocities and the file's own tempo for the timing), played through Serum with the preset's whole FX chain
and through the engine (tablor_cli with TB_EVENTS), both at the host tempo 120 BPM (so the tempo-synced delays agree). Writes OUT/<preset>__serum.wav and __move.wav (16 bit,
the natural level of each) and prints: loudness, peak, the level of each octave band re the render's total, and the brightness. Real playing finds what a held test chord does not:
velocity, note length, overlapping notes, retriggers, the preset in its own register.
Serum renders are the preset's own sound and are not to be committed or shared: OUT defaults to out/ab_midi (git-ignored).
"""
import argparse
import json
import os
import re
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import soundfile as sf
import test_render_calibration as H

SR = H.SR
PRESETS = ROOT / "out/move/data/UserData/schwung/presets/tinyhouse"
OCT = [(31, 62), (62, 125), (125, 250), (250, 500), (500, 1000), (1000, 2000), (2000, 4000), (4000, 8000), (8000, 16000)]


def read_midi(path):
    """[(on_s, off_s, note, velocity)] of a standard MIDI file, with its tempo map."""
    d = Path(path).read_bytes()
    assert d[:4] == b"MThd"
    _, ntrk, div = struct.unpack(">HHH", d[8:14])
    p, evs, tempos = 14, [], [(0, 500000)]
    for _ in range(ntrk):
        assert d[p:p + 4] == b"MTrk"
        ln = struct.unpack(">I", d[p + 4:p + 8])[0]; q, end, t, run = p + 8, p + 8 + ln, 0, 0
        while q < end:
            dt = 0
            while True:
                b = d[q]; q += 1; dt = (dt << 7) | (b & 0x7F)
                if not b & 0x80: break
            t += dt
            st = d[q]
            if st == 0xFF:
                ty = d[q + 1]; q += 2; n = 0
                while True:
                    b = d[q]; q += 1; n = (n << 7) | (b & 0x7F)
                    if not b & 0x80: break
                if ty == 0x51: tempos.append((t, int.from_bytes(d[q:q + 3], "big")))
                q += n; continue
            if st in (0xF0, 0xF7):
                n = 0
                q += 1
                while True:
                    b = d[q]; q += 1; n = (n << 7) | (b & 0x7F)
                    if not b & 0x80: break
                q += n; continue
            if st & 0x80: run = st; q += 1
            else: st = run
            kind = st & 0xF0
            n = 1 if kind in (0xC0, 0xD0) else 2
            a = d[q:q + n]; q += n
            if kind == 0x90 and a[1] > 0: evs.append((t, "on", a[0], a[1]))
            elif kind == 0x80 or (kind == 0x90 and a[1] == 0): evs.append((t, "off", a[0], 0))
        p = end
    tempos.sort()

    def secs(tick):
        s, last_t, last_us = 0.0, 0, 500000
        for tt, us in tempos:
            if tt >= tick: break
            s += (tt - last_t) * last_us / 1e6 / div; last_t, last_us = tt, us
        return s + (tick - last_t) * last_us / 1e6 / div
    evs.sort(key=lambda e: (e[0], e[1] == "on"))
    open_, notes = {}, []
    for t, kind, n, v in evs:
        if kind == "on":
            open_.setdefault(n, []).append((secs(t), v))
        elif open_.get(n):
            on, v0 = open_[n].pop(0); notes.append((on, secs(t), n, v0))
    return sorted(notes)


def find_pairs(midi_dir, names):
    src = {H.d2m.ascii_name(p.stem): p.stem for p in H.PRESET_DIR.glob("*.SerumPreset")}
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    byname = {norm(k): (k, v) for k, v in src.items()}
    out = []
    for f in sorted(Path(midi_dir).glob("*.mid")):
        base = re.sub(r"( - [^-]+){2}$", "", f.stem)                    # "Lead - Lynnic - Fm - 126" -> "Lead - Lynnic"
        hit = byname.get(norm(base))
        if hit and (not names or any(norm(n) == norm(base) for n in names)):
            out.append((base, f, hit[1]))
    return out


def serum_render(tree, notes, dur):
    s = H.Serum()
    fd, path = tempfile.mkstemp(suffix=".bin")
    with os.fdopen(fd, "wb") as f:
        f.write(H.convert_preset_tree(tree))
    s.synth.load_state(path); os.unlink(path)
    s.synth.clear_midi()
    for on, off, n, v in notes:
        s.synth.add_midi_note(int(n), int(v), float(on), float(max(off - on, 0.03)))
    s.engine.load_graph([(s.synth, [])]); s.engine.render(dur)
    return np.array(s.engine.get_audio(), dtype=np.float64)


def move_render(state, notes, dur):
    with tempfile.TemporaryDirectory() as td:
        ev = Path(td, "ev.txt")
        ev.write_text("".join(f"{on:.5f} {off:.5f} {n} {v}\n" for on, off, n, v in notes), encoding="utf-8")
        os.environ["TB_EVENTS"] = str(ev)
        try:
            return H.run_cli(state, [], dur, dur)
        finally:
            del os.environ["TB_EVENTS"]


def bands(x):
    x = x.mean(axis=0)
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2; fr = np.fft.rfftfreq(len(x), 1 / SR); tot = sp.sum() + 1e-30
    return np.array([10 * np.log10(sp[(fr >= a) & (fr < b)].sum() / tot + 1e-20) for a, b in OCT]), float((fr * sp).sum() / tot)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--midi-dir", default=str(ROOT / "Loopsy - DEEP HOUSE - Demo MIDI"))
    ap.add_argument("--out", default=str(ROOT / "out/ab_midi"))
    ap.add_argument("--tail", type=float, default=2.5)
    ap.add_argument("--presets", default=str(PRESETS), help="folder of the engine presets to play (default: the converter's output folder)")
    ap.add_argument("--suffix", default="", help="added to the WAV names (to keep a second version)")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    print(f"{'preset':26s} {'len s':>5s} {'notes':>5s} | rms dB serum  move  diff | peak serum move | centroid Hz serum move | octave-band error engine - Serum (dB): " + " ".join(f"{lo:>5d}" for lo, _ in OCT))
    for name, mid, serum_name in find_pairs(a.midi_dir, a.names):
        notes = read_midi(mid)
        dur = max(off for _, off, _, _ in notes) + a.tail
        header, tree = H.load_preset(serum_name)
        f = next((p for p in Path(a.presets).glob("*.json") if re.sub(r"[^a-z0-9]", "", p.stem.lower()) == re.sub(r"[^a-z0-9]", "", H.d2m.ascii_name(serum_name).lower())), None)
        if f is None:
            print(f"{name:26s} (no installed preset)"); continue
        state = json.loads(f.read_text(encoding="utf-8"))["state"]
        ser, mov = serum_render(tree, notes, dur), move_render(state, notes, dur)
        n = min(ser.shape[1], mov.shape[1]); ser, mov = ser[:, :n], mov[:, :n]
        tag = H.d2m.ascii_name(serum_name).replace(" ", "_").replace("'", "")
        for lab, x in (("serum", ser), ("move", mov)):
            sf.write(out / f"{tag}{a.suffix}__{lab}.wav", x.T.astype(np.float32), SR, subtype="PCM_16")
        (bs, cs), (bm, cm) = bands(ser), bands(mov)
        rs, rm = H.rms_db(ser), H.rms_db(mov)
        print(f"{name:26s} {dur:5.1f} {len(notes):5d} | {rs:13.1f} {rm:5.1f} {rm - rs:+5.1f} | {np.abs(ser).max():10.2f} {np.abs(mov).max():4.2f} | {cs:16.0f} {cm:5.0f} | "
              + " " * 40 + " ".join(f"{d:+5.1f}" for d in bm - bs))
        sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
