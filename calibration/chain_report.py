#!/usr/bin/env python3
"""Before/after of two sets of preset JSONs against Serum's full chain.

  chain_report.py BEFORE_DIR AFTER_DIR [--shift N] [--out file.json]

Each preset's `state` string (what the Move loads) is played by the engine, FX on, and compared with
the cached Serum full-chain render (calibration/refs/*_fx_*). With --shift N both sides play notes N
semitones away: Serum is rendered fresh for those (cached), so a fit that only learned its own notes
shows up. Measures: RMS error (Tablor minus Serum, dB, pooled over the sounding part), the signed
error of the 1-3 kHz, 3-8 kHz and >8 kHz band levels (re the render's total, so level does not leak in),
the side/mid error, and the full-chain loss.
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import eq_cap_test as E
import fit_chain as C
import fit_level as L
import fit_perceptual as F
import test_render_calibration as H

H.LOG_PATH = ROOT / "calibration/chain_report.log"
log = H.log
SR = H.SR
TEST_GROUP = ["Bass - A Classic", "Bass - AVAION", "Bass - BUNT", "Bass - Black Out", "Keys - Can't Find You", "Keys - Catching Flies",
              "Keys - Hans Deeper", "Keys - Zerb", "Lead - Can't Find You", "Lead - Consoul Training", "Pad - Aquatiq", "Pad - BICEP",
              "Pluck - HotLap", "Pluck - NOTSOBAD"]


def state_of(d, name, rescale=False):
    """A preset's state string. rescale: the set was made for the engine whose volume range was 18 dB
    narrower, so its volume pot is divided by sqrt(10^(18/20)) to be the same sound on this engine."""
    p = Path(d) / f"{name}.json"
    if not p.exists():
        return None
    st = json.loads(p.read_text(encoding="utf-8"))["state"]
    if rescale:
        st = re.sub(r"volume=(\d+)", lambda m: f"volume={max(1, round(int(m.group(1)) / 2.8184))}", st)
    return st


def pooled_sm(runs, end):
    s = m = 0.0
    for r in runs:
        x = r[:, int(0.01 * SR):int(end * SR)]
        s += (((x[0] - x[1]) / 2) ** 2).sum()
        m += (((x[0] + x[1]) / 2) ** 2).sum()
    return float(10 * np.log10(max(s, 1e-20) / max(m, 1e-20)))


def measure(it, refs, end, ref_feat, blob):
    rs = list(F.POOL.map(lambda _: H.run_cli(blob, it["notes"], C.DUR, C.HOLD), range(3)))
    rb, tb = E.band_levels(refs), E.band_levels(rs)
    return {"rms": L.rms_db(rs, end) - L.rms_db(refs, end), **{k: tb[k] - rb[k] for k in rb},
            "sm": pooled_sm(rs, end) - pooled_sm(refs, end), "loss": F.loss_of(ref_feat, F.features(rs, end))["loss"]}


def main():
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    before, after = a[0], a[1]
    shift = int(sys.argv[sys.argv.index("--shift") + 1]) if "--shift" in sys.argv else 0
    holder = []
    serum = lambda: holder[0] if holder else (holder.append(H.Serum()) or holder[0])
    names = sorted(p.stem for p in Path(after).glob("*.json"))
    source = {H.d2m.ascii_name(p.stem): p.stem for p in H.PRESET_DIR.glob("*.SerumPreset")}   # "Keys -  Zerb" is "Keys - Zerb" on the Move
    rows = {}
    for out_name in names:
        n = source.get(out_name, out_name)
        sb, sa = state_of(before, out_name, "--rescale-before" in sys.argv), state_of(after, out_name)
        if sb is None or sa is None:
            continue
        header, tree = H.load_preset(n)
        if not H.usable(tree):
            continue
        notes0 = H.sounding_notes(tree, n)
        notes = [x + shift for x in notes0]
        refs = H.reference(serum, n + "_fx", tree, notes, 2, C.DUR, C.HOLD)
        end = F.sounding_end(refs)
        it = {"notes": notes}
        ref_feat = F.features(refs, end)
        rows[n] = {"before": measure(it, refs, end, ref_feat, sb), "after": measure(it, refs, end, ref_feat, sa)}
        b, f = rows[n]["before"], rows[n]["after"]
        log(f"  {n:28s} rms {b['rms']:+5.1f} -> {f['rms']:+5.1f}  3-8k {b['3-8k']:+5.1f} -> {f['3-8k']:+5.1f}  >8k {b['>8k']:+5.1f} -> {f['>8k']:+5.1f}  "
            f"side/mid {b['sm']:+5.1f} -> {f['sm']:+5.1f}  loss {b['loss']:5.1f} -> {f['loss']:5.1f}")
    for label, sel in (("ALL", list(rows)), ("TEST GROUP (14)", [n for n in TEST_GROUP if n in rows])):
        log(f"\n{label}  n={len(sel)}" + (f"  (notes shifted {shift:+d} st)" if shift else ""))
        for key, nm in (("rms", "RMS error dB"), ("1-3k", "1-3 kHz band err dB"), ("3-8k", "3-8 kHz band err dB"), (">8k", ">8 kHz band err dB"), ("sm", "side/mid err dB"), ("loss", "full-chain loss")):
            x = np.array([[rows[n]["before"][key], rows[n]["after"][key]] for n in sel])
            if not len(x):
                continue
            if key == "loss":
                log(f"  {nm:22s} mean {x[:, 0].mean():6.2f} -> {x[:, 1].mean():6.2f}   better {int((x[:, 1] < x[:, 0] - 0.3).sum())}, worse {int((x[:, 1] > x[:, 0] + 0.3).sum())}")
            else:
                log(f"  {nm:22s} signed {x[:, 0].mean():+6.2f} -> {x[:, 1].mean():+6.2f}   |err| {np.abs(x[:, 0]).mean():6.2f} -> {np.abs(x[:, 1]).mean():6.2f}   "
                    f"closer {int((np.abs(x[:, 1]) < np.abs(x[:, 0]) - 0.5).sum())}, further {int((np.abs(x[:, 1]) > np.abs(x[:, 0]) + 0.5).sum())}")
    if "--out" in sys.argv:
        Path(sys.argv[sys.argv.index("--out") + 1]).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    H.LOG_PATH.write_text("", encoding="utf-8")
    main()
