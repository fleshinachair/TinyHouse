#!/usr/bin/env python3
"""Do the macro routes move the engine the way Serum's macro moves Serum? For each routed macro of the given presets (default: the first N), render the preset in Serum
with the macro at the far end of its range and at its saved value, and the engine the same (TB_MACRO), and compare the CHANGE in third-octave band levels (dB, absolute, not
normalised). `err` is the rms difference between the two changes with the routes; `base` is what it would be without them (Serum's change alone). Output: printed."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
import test_render_calibration as H
d2m = H.d2m

N = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 12
NAMES = [a for a in sys.argv[1:] if not a.isdigit()]
BANDS = 20 * 2 ** (np.arange(0, 30) / 3.0)


def bands(a):
    m = a[:, int(0.15 * H.SR):int(1.3 * H.SR)].mean(axis=0)
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)))) ** 2
    f = np.fft.rfftfreq(len(m), 1 / H.SR)
    return np.array([10 * np.log10(sp[(f >= lo) & (f < lo * 2 ** (1 / 3))].sum() + 1e-12) for lo in BANDS])


class Pool:
    def __init__(self): self.s = H.Serum()
    def render(self, tree, notes):
        if self.s.audio_seconds > 30: self.s = H.Serum()
        return self.s.render(tree, notes, 1.6, 1.4)


pool = Pool()
names = NAMES or sorted(p.stem for p in H.PRESET_DIR.glob("*.SerumPreset"))[:N]
tot_err, tot_base, cnt = 0.0, 0.0, 0
for n in names:
    hd, tree = H.load_preset(n)
    v = d2m.to_vital(d2m.physical(tree, hd))
    m = d2m.map_to_tablor(v, {}, H.SERUM_TABLES, H.WT_OUT, "Deep House")
    mc = m["macro"]
    notes = H.sounding_notes(tree, n)
    blob = d2m.blob(m)
    try:
        s0, e0 = bands(pool.render(tree, notes)), bands(H.run_cli(blob, notes, 1.6, 1.4))
    except Exception as ex:
        print(n, "skipped:", str(ex)[:80]); continue
    for k in sorted({r[0] for r in mc["routes"]}):
        stored = mc["stored"][k - 1]
        far = 100.0 if stored < 50 else 0.0
        t2 = copy.deepcopy(tree)
        mac = t2.setdefault(f"Macro{k - 1}", {})
        pp = mac.get("plainParams")
        mac["plainParams"] = dict(pp) if isinstance(pp, dict) else {}
        mac["plainParams"]["kParamValue"] = far
        os.environ["TB_MACRO"] = f"{k}={far / 100 * 127:g}"
        try:
            s1, e1 = bands(pool.render(t2, notes)), bands(H.run_cli(blob, notes, 1.6, 1.4))
        except Exception as ex:
            print(n, k, "skipped:", str(ex)[:80]); os.environ.pop("TB_MACRO", None); continue
        os.environ.pop("TB_MACRO", None)
        ds, de = s1 - s0, e1 - e0
        sel = (s0 > s0.max() - 50) | (e0 > e0.max() - 50)        # the bands that sound: within 50 dB of the loudest
        err = float(np.sqrt(np.mean((ds - de)[sel] ** 2)))
        base = float(np.sqrt(np.mean(ds[sel] ** 2)))
        lvl_s = 10 * np.log10(np.sum(10 ** (s1 / 10)) / np.sum(10 ** (s0 / 10)))
        lvl_e = 10 * np.log10(np.sum(10 ** (e1 / 10)) / np.sum(10 ** (e0 / 10)))
        routes = ",".join(r[1] for r in mc["routes"] if r[0] == k)
        print(f"{n[:26]:26s} m{k} {mc['names'][k - 1][:12]:12s} err {err:5.1f}  base {base:5.1f}  level change serum {lvl_s:+5.1f} engine {lvl_e:+5.1f}  [{routes}]", flush=True)
        tot_err += err; tot_base += base; cnt += 1
print(f"{cnt} macros: mean err {tot_err / max(cnt, 1):.2f} dB, without the routes {tot_base / max(cnt, 1):.2f} dB")
os._exit(0)
