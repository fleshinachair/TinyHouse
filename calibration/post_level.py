#!/usr/bin/env python3
"""Keep the output limiter out of the sound: lower any preset's volume until no more than SHARE_CAP of its samples reach the
limiter's shoulder. (The limiter's RMS cost was tried as a second condition and dropped: it compares two renders with different
random unison phases, so it swings by more than the 0.5 dB it was meant to hold. The share is read from one render.)

  post_level.py IN_DIR OUT_DIR

fit_level.py allowed the limiter 2 dB: matching Serum's loudness mattered more. On a bass or a dense chord that is audibly
squashed and distorted (Bass - oskar med k sat at maximum volume with 8 % of its samples over the knee). The cost of the
limiter is read the way fit_level does it: the same sound 18 dB or more lower is linear, so its RMS scaled back up is what
the preset would be with no limiter. Presets over the cap are turned down until they are under it; quieter than Serum is
the price. EXEMPT presets were approved by ear and are left alone. Writes OUT_DIR/*.json and calibration/post_level.json.
"""
import json
import math
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fit_chain as C
import fit_level as L
import post_fit as PF
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/post_level.log"
log = H.log
CAP_DB = 0.5
SHARE_CAP = 0.015      # and at most this share of the samples may reach the limiter's shoulder (above its 0.7 knee)
KNEE = 0.7
EXEMPT = set()                       # Bass - A Classic was exempt (approved by ear); the user chose faithful translation for every preset


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.glob("*.json"):
        shutil.copyfile(f, dst / f.name)
    srcname = {H.d2m.ascii_name(p.stem): p.stem for p in H.PRESET_DIR.glob("*.SerumPreset")}
    out, lock = {}, threading.Lock()

    def work(f):
        stem = srcname.get(f.stem, f.stem)
        if stem in EXEMPT or f.stem in EXEMPT:
            return
        j = json.loads(f.read_text(encoding="utf-8"))
        items = C.load_items([stem])
        if not items:
            return
        it = items[0]
        st = j["state"]

        def rms(state, runs=3):
            return L.rms_db([H.run_cli(state, it["notes"], C.DUR, C.HOLD) for _ in range(runs)], it["end"])

        a, b = int(0.01 * H.SR), int(it["end"] * H.SR)
        lin_cache = {}

        def linear(k2):                       # the sound at a volume low enough for the limiter to be linear
            if k2 not in lin_cache:
                lin_cache[k2] = [H.run_cli(PF.setk(st, "volume", k2), it["notes"], C.DUR, C.HOLD)[:, a:b] for _ in range(3)]
            return lin_cache[k2]

        def share(v):                         # share of samples over the knee before the limiter, at volume v
            k2 = max(1, int(round(v / 8.0)))
            gain = (v / k2) ** 2
            return float(np.mean([(np.abs(x) * gain > KNEE).mean() for x in linear(k2)]))

        def loss(v):
            cur = rms(PF.setk(st, "volume", v))
            k2 = max(1, int(round(v / 8.0)))
            lin = rms(PF.setk(st, "volume", k2)) - 20 * math.log10((k2 / v) ** 2)
            return lin - cur
        v0 = int(PF.getk(st, "volume", 36))
        l0 = loss(v0) if v0 >= 8 else 0.0
        s0 = share(v0) if v0 >= 8 else 0.0
        rec = {"volume": [v0, v0], "limiter_loss_db": [l0, l0], "share": [s0, s0]}
        if s0 > SHARE_CAP:
            lo, hi = 1, v0
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if share(mid) <= SHARE_CAP or mid < 8:
                    lo = mid
                else:
                    hi = mid - 1
            v = lo
            j["state"] = PF.setk(st, "volume", v)
            (dst / f.name).write_text(json.dumps(j) + "\n", encoding="utf-8")
            rec = {"volume": [v0, v], "limiter_loss_db": [l0, loss(v) if v >= 8 else 0.0], "share": [s0, share(v) if v >= 8 else 0.0]}
        with lock:
            out[f.stem] = rec
            if rec["volume"][0] != rec["volume"][1]:
                log(f"  {f.stem:28s} limiter took {rec['limiter_loss_db'][0]:.2f} dB -> {rec['limiter_loss_db'][1]:.2f} dB, {100 * rec['share'][0]:.1f} % -> {100 * rec['share'][1]:.1f} % over the knee   volume {rec['volume'][0]} -> {rec['volume'][1]} ({40 * math.log10(rec['volume'][1] / rec['volume'][0]):+.1f} dB)")
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(work, sorted(src.glob("*.json"))))
    Path("calibration/post_level.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    n = sum(1 for r in out.values() if r["volume"][0] != r["volume"][1])
    log(f"\n{n} of {len(out)} presets turned down; the rest were already under {CAP_DB} dB")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
