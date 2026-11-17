"""Performance sweep: what do fewer Voices / a lower Unison Cap cost in sound, and save in CPU?

    python calibration/dev/perf_sweep.py "<preset .json>" [--notes 48,55,60,64,67] [--seeds 3]

Renders the preset through the real engine (calibration/tablor_cli.exe, built by schwung-tablor/scripts/zig-build.sh native) as a held
chord with a release, once per (voices, uni_cap) combination, and prints for each combination
  cpu   mean block render time against the full setting (the preset as it is, cap 16)
  band  the largest difference in a third-octave band of the long-term spectrum (dB), 100 Hz .. 12 kHz
  level overall level difference (dB)
  width stereo correlation difference
and the same numbers between two seeds of the FULL setting, which is how much a render differs from itself (random start phases, grain
positions): a combination whose differences stay inside that is not a change anyone can hear.
"""
import argparse, json, os, subprocess, sys, tempfile
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLI = os.path.join(ROOT, "calibration", "tablor_cli.exe")
MOVE_ROOT = os.path.join(ROOT, "out", "move")
SR = 44100
EDGES = [100 * 2 ** (i / 3) for i in range(0, 21) if 100 * 2 ** (i / 3) <= 12000]


def render(preset, notes, seed, tb_set, dur=7.0, hold=3.5, vel=100):
    out = tempfile.mktemp(suffix=".raw")
    env = dict(os.environ, TB_TIME="1")
    if tb_set:
        env["TB_SET"] = tb_set
    r = subprocess.run([CLI, MOVE_ROOT, preset, out, str(dur), str(hold), str(vel), notes, str(seed)], capture_output=True, text=True, env=env, timeout=600)
    if r.returncode:
        raise SystemExit("render failed: " + r.stderr[-300:])
    t = [l for l in r.stderr.splitlines() if l.startswith("TIME")][-1].split()
    a = np.fromfile(out, dtype=np.float32).reshape(-1, 2)
    os.remove(out)
    return a, float(t[2]), float(t[4])


def bands(a):
    m = a.mean(axis=1)
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)))) ** 2
    f = np.fft.rfftfreq(len(m), 1 / SR)
    return np.array([10 * np.log10(sp[(f >= lo) & (f < hi)].sum() + 1e-12) for lo, hi in zip(EDGES[:-1], EDGES[1:])])


def stats(a):
    rms = 20 * np.log10(np.sqrt((a ** 2).mean()) + 1e-9)
    c = np.corrcoef(a[:, 0], a[:, 1])[0, 1] if a.std() > 0 else 0.0
    return bands(a), rms, c


def diff(x, y):
    bx, rx, cx = x
    by, ry, cy = y
    d = bx - by - (bx - by).mean()                        # the shape of the spectrum, not its level (level is its own column)
    return float(np.abs(d).max()), float(ry - rx), float(cy - cx)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preset")
    ap.add_argument("--notes", default="48,55,60,64,67")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--voices", default="")
    ap.add_argument("--caps", default="16,8,6,5,4,3,2,1")
    a = ap.parse_args()
    state = json.load(open(a.preset, encoding="utf-8"))["state"]
    v0 = int(dict(kv.split("=", 1) for kv in state.split(";")[1:] if "=" in kv).get("voices", 8))
    voices = [int(x) for x in a.voices.split(",")] if a.voices else sorted({v0, 6, 4, 3, 2}, reverse=True)
    caps = [int(x) for x in a.caps.split(",")]
    n_chord = len(a.notes.split(","))
    ref = [render(a.preset, a.notes, s, "") for s in range(1, a.seeds + 1)]
    ref_stats = [stats(r[0]) for r in ref]
    cpu0 = np.mean([r[1] for r in ref])
    selfd = [diff(ref_stats[0], s) for s in ref_stats[1:]]
    print(f"preset {os.path.basename(a.preset)}   Voices {v0}   chord of {n_chord} notes   full: mean {cpu0:.0f} us/block, p99 {np.mean([r[2] for r in ref]):.0f}")
    if selfd:
        print("seed-to-seed at full: band %.2f dB   level %+.2f dB   width %+.3f   (a render against itself)" % (max(d[0] for d in selfd), max(abs(d[1]) for d in selfd), max(abs(d[2]) for d in selfd)))
    print("voices cap |  cpu   band   level  width")
    for v in voices:
        for c in caps:
            if v == v0 and c == 16:
                continue
            r = render(a.preset, a.notes, 1, f"voices={v},uni_cap={c}")
            d = diff(ref_stats[0], stats(r[0]))
            print(f"{v:>6} {c:>3} | {r[1] / cpu0 * 100:4.0f}% {d[0]:6.2f} {d[1]:+6.2f} {d[2]:+7.3f}")


main()
