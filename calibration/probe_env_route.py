#!/usr/bin/env python3
"""How a route into an envelope's Attack / Decay / Release / Sustain moves the stage: a macro at 100 % (x = 1) routed to Env 1 with amount a.
The stage's real length is read off the amplitude for a scan of stored values (static, no route) and the routed render is read against that scan, so the route's move is
measured in the stored (knob) domain: effective stored value = base + k * a * x. Output: calibration/probe_env_route.json and a printed table."""
import copy, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration")); os.chdir(ROOT)
import numpy as np
import probe_lfo as P
import test_render_calibration as H
SR = H.SR
PID = {"kParamAttack": 0, "kParamDecay": 2, "kParamSustain": 3, "kParamRelease": 4}


def env_db(x, w=0.005):
    m = x[0]; k = int(w * SR); n = len(m) // k
    return 20 * np.log10(np.sqrt((m[:n * k].reshape(n, k) ** 2).mean(1)) + 1e-9)


def measure(kind, a):
    """kind: attack -> time to reach 90 % of the peak; decay -> time from the peak to 20 dB under it (sustain 0); release -> time from note-off (0.6 s) to 20 dB under the held level."""
    e = env_db(a)
    pk = e.max()
    if kind == "attack":
        return float(np.argmax(e > pk - 1.0) * 0.005)
    if kind == "decay":
        i = int(np.argmax(e)); j = np.where(e[i:] < pk - 20)[0]
        return float(j[0] * 0.005) if j.size else 99.0
    held = e[int(0.55 / 0.005)]; j = np.where(e[int(0.6 / 0.005):] < held - 20)[0]
    return float(j[0] * 0.005) if j.size else 99.0


def tree(kind, v, route=None):
    t = P.tree_with()
    base = {"kParamAttack": 0.001, "kParamDecay": 0.05, "kParamSustain": 1.0, "kParamRelease": 0.05}
    if kind == "decay":
        base["kParamSustain"] = 0.0
    if kind == "attack":
        base["kParamAttack"] = v
    elif kind == "decay":
        base["kParamDecay"] = v
    elif kind == "release":
        base["kParamRelease"] = v
    t["Env0"] = {"plainParams": base}
    if route:
        amt, pname = route
        t["Macro0"] = {"plainParams": {"kParamValue": 100.0}}
        t["ModSlot0"] = {"destModuleID": 0, "destModuleParamID": PID[pname], "destModuleParamName": pname, "destModuleTypeString": "Env", "plainParams": {"kParamAmount": amt}, "source": [25, 0]}
    return t


def main():
    s = H.Serum()
    out = {}
    for kind, pname, grid, v0, amts in (
            ("decay", "kParamDecay", [0.02, 0.05, 0.1, 0.2, 0.3, 0.45, 0.6, 0.75, 0.9], 0.3, (-20, 10, 20, 30)),
            ("release", "kParamRelease", [0.02, 0.05, 0.1, 0.2, 0.3, 0.45, 0.6, 0.75, 0.9], 0.3, (-20, 10, 20, 30)),
            ("attack", "kParamAttack", [0.02, 0.05, 0.1, 0.2, 0.3, 0.45, 0.6, 0.75, 0.9], 0.3, (-20, 10, 20, 30))):
        scan = []
        for v in grid:
            a = s.render(tree(kind, v), [60], 6.0 if kind != "attack" else 4.0, 0.6)
            scan.append(measure(kind, a))
        print(kind, "static scan (stored value -> seconds):", {v: round(t, 3) for v, t in zip(grid, scan)}, flush=True)
        out[kind] = {"scan": dict(zip(map(str, grid)), scan) if False else [[v, t] for v, t in zip(grid, scan)]}
        for amt in amts:
            a = s.render(tree(kind, v0, (amt, pname)), [60], 6.0 if kind != "attack" else 4.0, 0.6)
            t = measure(kind, a)
            veff = float(np.interp(t, scan, grid)) if t >= min(scan) and t <= max(scan) else None
            print(f"   base {v0} amount {amt:+d}: {t:.3f} s -> effective stored value {veff}  (expected knob-domain {v0 + amt / 100:.2f})", flush=True)
            out[kind][f"amount{amt}"] = {"seconds": t, "effective": veff}
    (ROOT / "calibration/probe_env_route.json").write_text(json.dumps(out, indent=1))
    sys.stdout.flush(); os._exit(0)


if __name__ == "__main__":
    main()
