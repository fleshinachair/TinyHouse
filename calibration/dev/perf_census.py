"""CPU census: every preset in a folder rendered as a 5-note chord through the real engine, ranked by mean block cost (us, PC) and p99.
    python calibration/dev/perf_census.py <folder> [--out census_perf.json]
"""
import argparse, glob, json, os, subprocess, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLI = os.path.join(ROOT, "calibration", "tablor_cli.exe"); MOVE_ROOT = os.path.join(ROOT, "out", "move")
ap = argparse.ArgumentParser(); ap.add_argument("folder"); ap.add_argument("--out", default="")
a = ap.parse_args()
rows = []
for f in sorted(glob.glob(os.path.join(a.folder, "**", "*.json"), recursive=True)):
    env = dict(os.environ, TB_TIME="1")
    try:
        r = subprocess.run([CLI, MOVE_ROOT, f, os.devnull if False else "perf_tmp.raw", "4", "2.5", "100", "48,55,60,64,67", "1"], capture_output=True, text=True, env=env, timeout=300)
    except subprocess.TimeoutExpired:
        continue
    t = [l for l in r.stderr.splitlines() if l.startswith("TIME")]
    if r.returncode or not t:
        continue
    p = t[-1].split(); rows.append({"preset": os.path.relpath(f, a.folder), "mean": float(p[2]), "p99": float(p[4]), "max": float(p[6])})
rows.sort(key=lambda r: -r["mean"])
if a.out: json.dump(rows, open(a.out, "w"), indent=1)
for r in rows[:40]: print("%7.0f %7.0f  %s" % (r["mean"], r["p99"], r["preset"]))
print(len(rows), "presets; median mean %.0f us" % sorted(r["mean"] for r in rows)[len(rows) // 2])
if os.path.exists("perf_tmp.raw"): os.remove("perf_tmp.raw")
