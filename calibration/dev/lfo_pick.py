#!/usr/bin/env python3
"""Add LFOs to the shipped presets only where Serum says they belong.

  lfo_pick.py IN_DIR OUT_DIR MAPPED_DIR

The converter maps each Serum LFO (rate, shape, retrigger or free, and its routes) but three things about Serum are
assumptions: the rate of an LFO whose rate is not stored, the shape of one whose shape is not stored, and whether an LFO
swings 0..1 or around the value. So for every preset the converter gave routes to, three versions are played against
Serum's full chain on two sets of notes (the ones fitted and five semitones up): the shipped file as it is, with the LFO
unipolar, and with it bipolar. The LFO goes in only if it beats the shipped file on BOTH note sets and by at least MARGIN
on average, and then the volume is nudged so the RMS matches Serum's again. Every file also gets its eight route sources
written as 0, because loading a preset only sets the keys it holds and a route must not outlive the preset that had it.
Writes OUT_DIR/*.json and calibration/lfo_pick.json.
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import json
import math
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import fit_chain as C
import fit_level as L
import fit_perceptual as F
import post_fit as PF
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/lfo_pick.log"
log = H.log
MARGIN = 1.0


def motion(runs):
    """How much the spectrum's fine detail moves over the held part: the std over time of each FFT bin's log level (the level
    of each frame removed), averaged over the bins between 300 Hz and 8 kHz that carry sound. A wobble makes it larger."""
    vals = []
    for x in runs:
        mono = ((x[0] + x[1]) / 2)[int(0.05 * H.SR):int(1.0 * H.SR)]
        n, hop = 2048, 1024
        win = np.hanning(n)
        fr = np.array([20 * np.log10(np.abs(np.fft.rfft(mono[i:i + n] * win)) + 1e-9) for i in range(0, len(mono) - n, hop)])
        fr = fr - fr.mean(axis=1, keepdims=True)
        f = np.fft.rfftfreq(n, 1 / H.SR)
        keep = (f > 300) & (f < 8000) & (fr.mean(axis=0) > -60)
        vals.append(float(fr[:, keep].std(axis=0).mean()) if keep.any() else 0.0)
    return float(np.mean(vals))


def refs_for(name, notes):
    def none():
        raise RuntimeError("no cached Serum render")
    return H.reference(none, name + "_fx", None, notes, 2, C.DUR, C.HOLD)


def main():
    src, dst, mapped = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    dst.mkdir(parents=True, exist_ok=True)
    zero = "".join(f"lr{r}_src=0;" for r in range(1, 9))
    srcname = {H.d2m.ascii_name(p.stem): p.stem for p in H.PRESET_DIR.glob("*.SerumPreset")}
    out, lock = {}, threading.Lock()

    def work(f):
        stem = srcname.get(f.stem, f.stem)
        j = json.loads(f.read_text(encoding="utf-8"))
        base = j["state"]
        for r in range(1, 9):
            base = PF.setk(base, f"lr{r}_src", 0)
        mf = mapped / f.name
        rec = {"pick": "none"}
        try:
            ms = json.loads(mf.read_text(encoding="utf-8"))["state"] if mf.exists() else ""
            kv = [x for x in ms.split(";")[1:] if x.startswith(("lfo", "lr")) and "=" in x]
            used = any(re.match(rf"lr{r}_src=([1-9])", x) for x in kv for r in range(1, 9))
            if used:
                items = C.load_items([stem])
                it = items[0]
                header, tree = H.load_preset(stem)
                notes5 = [n + 5 for n in H.sounding_notes(tree, stem)]
                refs5 = refs_for(stem, notes5)
                end5 = F.sounding_end(refs5)
                feat5 = F.features(refs5, end5)

                m_ser = (motion(it["refs"]), motion(refs5))

                def lossof(st):
                    a = [H.run_cli(st, it["notes"], C.DUR, C.HOLD) for _ in range(3)]
                    b = [H.run_cli(st, notes5, C.DUR, C.HOLD) for _ in range(3)]
                    return (F.loss_of(it["ref_feat"], F.features(a, it["end"]))["loss"], F.loss_of(feat5, F.features(b, end5))["loss"], a, (motion(a), motion(b)))
                def withlfo(bip, scale):
                    st = base
                    for x in kv:
                        k, v = x.split("=", 1)
                        if re.fullmatch(r"lr\d_amt", k):                    # the depth: 64 is none, so scale the distance from it
                            v = str(int(round(64 + (float(v) - 64) * scale)))
                        st = PF.setk(st, k, v)
                    return PF.setk(st, "lfo_bip", bip)
                l0 = lossof(base)
                best = None
                for bip, scale in ((0, 1.0), (1, 1.0), (0, 0.5), (1, 0.5), (0, 0.25), (1, 0.25)):
                    lab = f"{'bi' if bip else 'uni'}x{scale:g}"
                    st = withlfo(bip, scale)
                    l1 = lossof(st)
                    g1, g2 = l0[0] - l1[0], l0[1] - l1[1]
                    d1 = abs(l0[3][0] - m_ser[0]) - abs(l1[3][0] - m_ser[0])           # motion error removed (positive: closer to Serum)
                    d2 = abs(l0[3][1] - m_ser[1]) - abs(l1[3][1] - m_ser[1])
                    rec[lab] = {"gain_fit": round(g1, 2), "gain_shift": round(g2, 2), "motion_fit": round(d1, 2), "motion_shift": round(d2, 2)}
                    by_loss = g1 > 0 and g2 > 0 and (g1 + g2) / 2 >= MARGIN
                    by_motion = g1 > -0.5 and g2 > -0.5 and d1 >= 0.4 and d2 >= 0.4          # no worse by the loss, and the wobble is closer to Serum's
                    score = (g1 + g2) / 2 + 2 * (d1 + d2) / 2
                    if (by_loss or by_motion) and (best is None or score > best[0]):
                        best = (score, lab, st, l1[2])
                if best:
                    _, lab, st, runs = best
                    d = L.rms_db(it["refs"], it["end"]) - L.rms_db(runs, it["end"])
                    if abs(d) > 0.5:
                        v0 = int(PF.getk(st, "volume", 36))
                        st = PF.setk(st, "volume", int(round(min(max(v0 * 10 ** (d / 40.0), 1), 127))))
                        rec["volume_db"] = round(d, 1)
                    base = st
                    rec["pick"] = lab
        except Exception as e:
            rec = {"pick": "none", "error": str(e)[:100]}
        j["state"] = base
        (dst / f.name).write_text(json.dumps(j) + "\n", encoding="utf-8")
        with lock:
            out[f.stem] = rec
            if rec.get("uni") or rec.get("unix1") or rec.get("error"):
                top = max(((v["gain_fit"] + v["gain_shift"] + 2 * (v["motion_fit"] + v["motion_shift"]), k) for k, v in rec.items() if isinstance(v, dict)), default=(0, ""))
                log(f"  {f.stem:28s} {rec['pick']:9s} best tried {top[1]} (loss+motion score {top[0] / 2:+.1f})" + (f"  level {rec['volume_db']:+.1f} dB" if "volume_db" in rec else "") + (f"  ERROR {rec['error']}" if "error" in rec else ""))
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(work, sorted(src.glob("*.json"))))
    Path("calibration/lfo_pick.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    n = sum(1 for r in out.values() if r["pick"] != "none")
    log(f"\n{n} presets get their LFOs ({sum(1 for r in out.values() if r['pick'] == 'uni')} unipolar, {sum(1 for r in out.values() if r['pick'] == 'bi')} bipolar), of {len([r for r in out.values() if r.get('uni')])} tried")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
