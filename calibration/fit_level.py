#!/usr/bin/env python3
"""Per-preset volume that puts the engine's full-chain RMS where Serum's is (Patch 5).

  fit_level.py [name ...]

Serum's full-chain render and the engine's (the converter's complete output, FX on, every fit applied)
play the same chord. RMS is pooled in power over the part that sounds (found from Serum's own
envelope), the volume knob is bisected until they agree, and the volume is then capped so the
output limiter takes at most LIMITER_CAP_DB off the RMS: Serum itself peaks above full scale on some
presets and the engine's soft limiter is a ceiling, not a place to live. The limiter's cost is read the
way autopsy.py does it: the same patch 18 dB or more lower is linear, so its RMS scaled back up is
what the preset would be with no limiter. Output: calibration/level_fits.json, with a signature of
every other parameter so the converter only uses a fit that still describes the preset.
"""
import copy
import json
import math
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter"))
sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fit_chain as C
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/fit_level.log"
log = H.log
d2m, SR = H.d2m, H.SR
OUT = H.OUT / "level_fits.json"
LIMITER_CAP_DB = 2.0
LIMITER_KNEE = 0.7                                     # saturate.h kLimiterKnee
TOLERANCE_DB = 0.4
LOCK = threading.Lock()


def rms_db(runs, end):
    a, b = int(0.01 * SR), int(end * SR)
    return float(10 * np.log10(np.mean([(r[:, a:b] ** 2).mean() for r in runs]) + 1e-20))


def fit_item(it):
    m = C.mapped(it["name"], it["header"], it["tree"])
    P = m["params"]
    end = it["end"]
    target = rms_db(it["refs"], end)

    def at(v, runs=4):
        q = copy.deepcopy(m)
        q["params"]["volume"] = int(v)
        blob = d2m.blob(q)
        return rms_db([H.run_cli(blob, it["notes"], C.DUR, C.HOLD) for _ in range(runs)], end)

    def peak_at(v):
        q = copy.deepcopy(m)
        q["params"]["volume"] = int(v)
        x = H.run_cli(d2m.blob(q), it["notes"], C.DUR, C.HOLD)
        return float(np.abs(x[:, int(0.01 * H.SR):int(end * H.SR)]).max())

    def limiter_loss(v, r_v):
        if peak_at(v) <= LIMITER_KNEE:                 # nothing reaches the shoulder: no limiter loss. (The check below renders at v/8 gain, which for a quiet preset is
            return 0.0                                  # under the 16-bit dither floor: its rms was noise, the loss read as huge and the volume was driven to 1: Quiet Ridge, Cookies)
        k2 = max(1, int(round(v / 8.0)))
        lin = at(k2, 3) - 20 * math.log10((k2 / v) ** 2)
        return lin - r_v

    start_v = P["volume"]
    start_db = at(start_v)
    lo, hi = 1, 127
    for _ in range(7):                                 # RMS rises with volume: the smallest one that reaches Serum's
        mid = (lo + hi) // 2
        if at(mid, 3) < target:
            lo = mid + 1
        else:
            hi = mid
    v = min(lo, 127)                                   # the bisect runs one past the top if Serum is never reached
    v_fit = v                                          # the volume that matches Serum's loudness, before the limiter cap below
    r = at(v)
    loss = limiter_loss(v, r) if v >= 8 else 0.0
    if loss > LIMITER_CAP_DB:
        lo, hi = 1, v
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if limiter_loss(mid, at(mid, 3)) <= LIMITER_CAP_DB:
                lo = mid
            else:
                hi = mid - 1
        v = lo
        r = at(v)
        loss = limiter_loss(v, r) if v >= 8 else 0.0
    return {"sig": d2m.params_sig(P), "volume": int(v), "volume_uncapped": int(v_fit), "start_volume": int(start_v), "start_rms_db": start_db, "rms_db": r,
            "serum_rms_db": target, "shortfall_db": max(0.0, target - r), "limiter_loss_db": float(loss), "kept": True}


def main():
    given = [a for a in sys.argv[1:] if not a.startswith("--") and not a.endswith(".json")]
    for a in sys.argv[1:]:                                      # --names-file list.json: a JSON list of preset names
        if a.endswith(".json"):
            given += json.loads(Path(a).read_text(encoding="utf-8"))
    names = given or sorted(p.stem for p in H.PRESET_DIR.glob("*.SerumPreset"))
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    # the converter as it ships (faithful: no per-preset fits); only the loudness is fitted here
    items = [it for it in C.load_items(names) if it["name"] not in data]
    log(f"{len(items)} presets to level")

    def work(it):
        try:
            rec = fit_item(it)
        except Exception as e:
            rec = {"kept": False, "error": str(e)[:120]}
        with LOCK:
            data[it["name"]] = rec
            OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
            if "rms_db" in rec:
                log(f"  {it['name']:28s} serum {rec['serum_rms_db']:+6.1f} dB  tablor {rec['start_rms_db']:+6.1f} -> {rec['rms_db']:+6.1f}  "
                    f"volume {rec['start_volume']} -> {rec['volume']}  limiter {rec['limiter_loss_db']:.1f} dB  short {rec['shortfall_db']:.1f}")
            else:
                log(f"  {it['name']:28s} {rec.get('error')}")
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(work, items))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    if "--fresh" in sys.argv and OUT.exists():
        OUT.unlink()
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
