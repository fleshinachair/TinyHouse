#!/usr/bin/env python3
"""Measure what each preset's own reverb, delay and width effects do in Serum.

Serum stores an effect's settings in its own units and omits any it leaves at default, so the
converter does not read them: it measures the real thing. Each preset's rack-0 effects are put,
as they are, on a noise burst in Serum, and what comes out is written to fx_fingerprints.json
for the converter to match in Tablor (fxmap.py). Only measurements are stored, not audio.

  fx_fingerprint.py [first last]      presets first..last-1 of the 70 (default all), cached

The plugin goes silent after 1200 s of rendered audio per process, so a full run is two calls:
  fx_fingerprint.py 0 35 ; fx_fingerprint.py 35 70
"""
import copy
import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import numpy as np
import test_render_calibration as H
import fxmetrics as M
import fx_probe as P

H.LOG_PATH = ROOT / "calibration/fx_fingerprint.log"
log = H.log
OUT = ROOT / os.environ.get("FP_OUT", "calibration/fx_fingerprints.json")

REVERB = ("Reverb", "Conv")
DELAY = ("Delay",)
WIDE = ("HyperD",)            # Chorus, Flanger and Phaser are stages of their own in the engine now (fxmap.modfx_params)


def rack0(tree):
    """The main FX rack's nodes, untouched, in order."""
    return [copy.deepcopy(n) for n in (tree.get("FXRack0", {}).get("FX") or [])]


def kind_of(node):
    return next((k[2:] for k in node if k.startswith("FX")), None)


def with_nodes(base, nodes):
    t = copy.deepcopy(base)
    t["FXRack0"]["FX"] = nodes
    return t


def force_wet(nodes, kinds):
    out = copy.deepcopy(nodes)
    for n in out:
        k = kind_of(n)
        if k in kinds:
            pp = n[f"FX{k}"].get("plainParams")
            if not isinstance(pp, dict):
                n[f"FX{k}"]["plainParams"] = pp = {}
            pp["kParamWet"] = 100.0
    return out


_silent_in_a_row = 0


def render(serum, tree, dur, hold):
    """None for a render that is silent. One can be genuine (two chained delays at wet 100 can put
    the whole burst past the end of the window), but the plugin's render limit is silent too, and
    that one never ends: so two in a row stop the run."""
    global _silent_in_a_row
    try:
        a = serum.render(tree, [48], dur, hold)
    except RuntimeError:
        serum.audio_seconds -= dur
        _silent_in_a_row += 1
        if _silent_in_a_row >= 2:
            raise
        return None
    _silent_in_a_row = 0
    return a


def measure(serum, base, ref_db, dry_noise2, tree):
    """One preset's fingerprint: what its rack-0 reverb, delay and width effects do to a noise burst in Serum, as a dict
    ({"fx": kinds, "reverb": ..., "delay": ..., "wide": ...}); a render that came out silent leaves that entry out."""
    import fxmap
    nodes = rack0(tree)
    ph = H.d2m.physical(tree, {"presetName": "probe", "tags": []})
    nodes = fxmap.fold_macro_wets(nodes, ph["mods"], ph["macros"])        # a macro that drives a Wet is part of what Serum plays
    kinds = [kind_of(x) for x in nodes]
    rec = {"fx": kinds}
    inside = fxmap.branch_nodes(nodes)
    nodes = [x for i, x in enumerate(nodes) if i not in inside]      # an effect in a Split branch works on one band: not the master reverb/delay/width
    # Each kind is measured on its own: in the whole rack the other effects (a delay's echoes, a Hyper's diffusion) read as part of the reverb's
    # wet part and tail (Lead - Nyday's reverb came out 2.5 x too wet and twice as long). The engine applies the stages in the same order anyway.
    rv = [x for x in nodes if kind_of(x) in REVERB]
    if rv:
        nodes_rv = rv
        act = render(serum, with_nodes(base, nodes_rv), 5.0, 0.12)
        full = render(serum, with_nodes(base, force_wet(nodes_rv, REVERB)), 5.0, 0.12)
        steady = render(serum, with_nodes(base, force_wet(nodes_rv, REVERB)), 3.0, 2.9)
        if act is None or full is None or steady is None:
            return rec
        rec["reverb"] = M.reverb_tail(full) | {
            "wet_ratio": M.wet_ratio(act, full),
            "level100_db": M.window_db(steady, 1.5, 3.0) - ref_db,
            "n": len(rv)}
    dl = [x for x in nodes if kind_of(x) in DELAY]
    if dl:
        pp = [x["FXDelay"].get("plainParams") for x in dl]
        ms = max(max(fxmap.delay_ms(q if isinstance(q, dict) else {}, s) for s in ("L", "R")) for q in pp)      # tempo-synced: see fxmap.delay_ms
        act = render(serum, with_nodes(base, dl), 2.0, 0.1)
        full = render(serum, with_nodes(base, force_wet(dl, DELAY)), 2.0, 0.1)
        rec["delay"] = {"wet_ratio": M.echo_amp_ratio(act, full, ms) if act is not None and full is not None else None,
                        "ms": ms, "n": len(dl)}
    wd = [x for x in nodes if kind_of(x) in WIDE]
    if wd:
        wide = render(serum, with_nodes(base, wd), 2.0, 1.9)
        if wide is not None:
            rec["wide"] = {"side_added_db": M.side_added_db(wide, dry_noise2, 0.3, 1.8), "n": len(wd)}
    return rec


def start_measuring():
    """The noise-source plugin and its dry references: (serum, base, ref_db, dry_noise2)."""
    serum, base = P.start()
    ref = [render(serum, base, 3.0, 2.9) for _ in range(2)]
    return serum, base, float(np.mean([M.window_db(r, 1.5, 3.0) for r in ref])), render(serum, base, 2.0, 1.9)


def main():
    first = int(sys.argv[1]) if len(sys.argv) > 2 else 0
    last = int(sys.argv[2]) if len(sys.argv) > 2 else 10 ** 6
    names = sorted(Path(p).stem for p in glob.glob(str(H.PRESET_DIR / "*.SerumPreset")))[first:last]
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    serum, base = P.start()                          # the noise source
    ref = [render(serum, base, 3.0, 2.9) for _ in range(2)]
    ref_db = float(np.mean([M.window_db(r, 1.5, 3.0) for r in ref]))
    dry_burst = render(serum, base, 5.0, 0.12)
    dry_noise2 = render(serum, base, 2.0, 1.9)
    log(f"presets {first}..{min(last, first + len(names))}: dry noise {ref_db:.1f} dB")

    for n, name in enumerate(names):
        if name in data:
            continue
        header, tree = H.load_preset(name)
        t0 = serum.audio_seconds
        rec = measure(serum, base, ref_db, dry_noise2, tree)
        data[name] = rec
        OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
        log(f"  {name:28s} {rec['fx']}  ({serum.audio_seconds - t0:.0f} s)  " +
            " ".join(f"{k}:{ {a: round(b, 2) if isinstance(b, float) else b for a, b in v.items()} }" for k, v in rec.items() if k != "fx"))
    log(f"done: {len(data)} presets in the file, {serum.audio_seconds:.0f} s of Serum audio this process")


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
