#!/usr/bin/env python3
"""Import a folder of Serum 2 presets into a TinyHouse bank, keeping only what the engine can carry whole.

  import_pack.py SRC_DIR --bank "Serum Bass" [--out out_import] [--limit N] [--no-level]

For every .SerumPreset under SRC_DIR:
  1. convert it with the converter's own mapping (deephouse2move.map_to_tablor), no per-preset tuning;
  2. drop it if anything audible is left behind (deephouse2move.unsupported_reasons: an unsupported oscillator, a missing wavetable,
     an effect the engine has no stage for) and say why in OUT/skipped.md;
  3. play it through Serum (full chain, notes chosen by test_render_calibration.sounding_notes) and set the preset's volume so its
     loudness matches, with the output limiter kept out of the sound (the same two steps as fit_level.py and post_level.py);
  4. write OUT/<bank>/<name>.json (Schwung's preset format) and the wavetables it needs under out/move/data/UserData/UserLibrary/Wavetables/<bank>/.
The plugin goes silent after about 1200 s of audio per process, so the plugin is reloaded before that. The Serum renders are cached
in calibration/refs/ like the rest, so a second run is fast. Copy OUT/<bank> to the Move with BANK="<bank>" deploy_presets.sh.
"""
import argparse
import copy
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
import numpy as np
import fit_chain as C
import fit_level as L
import fit_perceptual as F
import fx_fingerprint as FP
import fxmap
import comp_rescale as CR
import post_fit as PF
import voice_survey as VS
import test_render_calibration as H
from serum2_preset_loader.codec import decode_preset_file

d2m = H.d2m
SHARE_CAP, KNEE = 0.015, 0.7          # post_level.py: at most this share of samples may reach the limiter's shoulder


def cap_limiter(state, it):
    """Lower the volume until at most SHARE_CAP of the samples would reach the limiter's shoulder."""
    a, b = int(0.01 * H.SR), int(it["end"] * H.SR)
    v0 = int(PF.getk(state, "volume", 36))

    def share(v):
        k2 = max(1, int(round(v / 8.0)))
        gain = (v / k2) ** 2
        runs = [H.run_cli(PF.setk(state, "volume", k2), it["notes"], C.DUR, C.HOLD)[:, a:b] for _ in range(3)]
        return float(np.mean([(np.abs(x) * gain > KNEE).mean() for x in runs]))
    if v0 < 8 or share(v0) <= SHARE_CAP:
        return state
    lo, hi = 1, v0
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if share(mid) <= SHARE_CAP or mid < 8:
            lo = mid
        else:
            hi = mid - 1
    return PF.setk(state, "volume", lo)


def voice_level_error_db(m, it, serum, bank):
    """How much louder the engine's voice is than Serum's (dB, engine minus Serum) at the converter's closed-form volume: both with their effects off, the
    preset's notes, the sounding part. None when Serum's voice alone is too quiet to read (a sound that needs its effects). The stages before the master
    gain (compressor, distortion) see the voice, not the whole chain, so their reference level is the one at which the voices agree."""
    refs = H.reference(serum, f"{bank}_{it['name']}_voice", H.strip_fx(it["tree"]), it["notes"], 2, C.DUR, C.HOLD)
    s = L.rms_db(refs, it["end"])
    if s < -70.0:
        return None
    st = PF.setk(d2m.blob(m), "fx_on", 0)
    e = L.rms_db([H.run_cli(st, it["notes"], C.DUR, C.HOLD) for _ in range(3)], it["end"])
    return float(min(max(e - s, -12.0), 12.0))


def finalize_level(m, it, serum=None, bank=""):
    """The state at the volume that puts the preset's full chain at Serum's loudness, capped by the limiter share.
    The compressor's thresholds and the calibrated distortion work on a level before the master gain. The converter sets them for its closed-form volume, which
    is right when the engine's voice is as loud as Serum's; where it is not (voice_level_error_db) they are moved to the volume at which the voices agree
    (comp_rescale.rescale) and stay there while the loudness fit moves the master gain after them. Without a Serum plugin the voice is taken as agreeing.
    C.mapped must hand fit_item the mapped preset."""
    rec = L.fit_item(it)
    P = m["params"]
    v0 = int(P["volume"])
    state0 = d2m.blob(m)
    if CR.level_stages(P):
        eps = voice_level_error_db(m, it, serum, bank) if serum is not None else None
        if eps:
            v_voice = d2m.pot_volume(CR.gain_db(v0) - eps)
            state0 = CR.rescale(state0, v0, v_voice)
        target = L.rms_db(it["refs"], it["end"])
        rms_of = lambda stx: L.rms_db([H.run_cli(stx, it["notes"], C.DUR, C.HOLD) for _ in range(2)], it["end"])
        _, st1 = CR.fit_volume(state0, v0, rms_of, target, start=int(rec["volume"]), follow=False)
        return cap_limiter(st1, it)
    P["volume"] = rec["volume"]
    return cap_limiter(d2m.blob(m), it)


def quality(state, it):
    """How close the finished preset is to Serum's own render of it: the level gap (engine - Serum, dB, over the sounding part) and the octave-band error (rms of the engine's
    band levels re its loudest minus Serum's, dB). A gate on the result, not a fit: a preset the converter has not carried well (a silent filter, a missing oscillator kind) shows here."""
    ren = [H.run_cli(state, it["notes"], C.DUR, C.HOLD) for _ in range(2)]
    gap = L.rms_db(ren, it["end"]) - L.rms_db(it["refs"], it["end"])
    eb, sb = np.mean([VS.bands(x) for x in ren], axis=0), np.mean([VS.bands(x) for x in it["refs"]], axis=0)
    return float(gap), float(np.sqrt(np.mean(np.square((eb - eb.max()) - (sb - sb.max())))))


def measure_fingerprints(name, tree, meas):
    """Serum's own reverb, delay and width for this preset's main rack and for each FX bus rack that has effects (under '<name>#bus1' / '#bus2'), measured on a noise burst
    (fx_fingerprint.py), unless already known. meas: the holder of the noise-source plugin, reloaded before the plugin's 1200 s render limit."""
    racks = [(name, tree)]
    for b in (1, 2):
        nodes = tree.get(f"FXRack{b}", {}).get("FX") or []
        if nodes:
            t2 = copy.deepcopy(tree)
            t2["FXRack0"]["FX"] = copy.deepcopy(nodes)
            racks.append((f"{name}#bus{b}", t2))
    for nm, tr in racks:
        if fxmap.fingerprint_for(nm) is not None:
            continue
        if not meas or meas[0][0].audio_seconds > 1000:
            meas[:] = [FP.start_measuring()]
        serum_, base, ref_db, dry2 = meas[0]
        rec = FP.measure(serum_, base, ref_db, dry2, tr)
        fxmap._fingerprints[fxmap.norm_name(nm)] = rec
        data = json.loads(fxmap.FINGERPRINTS.read_text(encoding="utf-8")) if fxmap.FINGERPRINTS.exists() else {}
        data[nm] = rec
        fxmap.FINGERPRINTS.write_text(json.dumps(data, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("src", type=Path)
    ap.add_argument("--bank", required=True)
    ap.add_argument("--out", type=Path, default=Path("out_import"))
    ap.add_argument("--limit", type=int, default=0, help="stop after this many kept presets")
    ap.add_argument("--max-level-gap", type=float, default=3.0, help="drop a preset whose finished level is further than this from Serum's (dB)")
    ap.add_argument("--max-band-rms", type=float, default=14.0, help="drop a preset whose octave-band levels are further than this (rms dB) from Serum's")
    ap.add_argument("--no-level", action="store_true", help="skip the Serum render and the loudness fit (the converter's own volume)")
    a = ap.parse_args()

    bank_dir = a.out / a.bank
    wt_out = H.WT_OUT          # tablor_cli reads its wavetables from here (out/move/...); copy the bank's folder to the Move with the presets
    bank_dir.mkdir(parents=True, exist_ok=True)
    wt_out.mkdir(parents=True, exist_ok=True)
    holder = []

    def serum():
        if not holder or holder[0].audio_seconds > 1000:
            holder[:] = [H.Serum()]
        return holder[0]
    meas = []                          # the noise-source plugin for the effect measurements, reloaded before the plugin's 1200 s render limit

    def fingerprint(name, tree):
        measure_fingerprints(name, tree, meas)
    used, kept, skipped = set(), [], []
    for p in sorted(a.src.rglob("*.SerumPreset")):
        try:
            header, tree = decode_preset_file(p)
        except Exception as e:
            skipped.append((p.stem, [f"cannot read: {str(e)[:60]}"]))
            continue
        name = d2m.ascii_name(p.stem)[:40]
        n = 2
        while name.lower() in used:
            name, n = f"{d2m.ascii_name(p.stem)[:36]} {n}", n + 1
        used.add(name.lower())
        if not H.usable(tree):
            skipped.append((name, ["a wavetable Serum's Tables folder does not have"]))
            continue
        def convert():
            v = d2m.to_vital(d2m.physical(tree, header))
            v["preset_name"] = name
            v["serum2move"]["name"] = name
            return d2m.map_to_tablor(v, {}, H.SERUM_TABLES, wt_out, a.bank)
        try:
            m = convert()
        except Exception as e:
            skipped.append((name, [f"cannot convert: {str(e)[:60]}"]))
            continue
        why = d2m.unsupported_reasons(m)
        needs_fx = [w for w in why if "no Serum fingerprint" in w]
        if needs_fx and len(needs_fx) == len(why):          # the effects are the only thing missing: measure them in Serum, then convert again
            try:
                fingerprint(name, tree)
            except RuntimeError as e:
                skipped.append((name, [f"effect measurement failed: {str(e)[:50]}"]))
                continue
            try:
                m = convert()
            except Exception as e:
                skipped.append((name, [f"cannot convert: {str(e)[:60]}"]))
                continue
            why = d2m.unsupported_reasons(m)
        if why:
            skipped.append((name, why))
            continue
        state = d2m.blob(m)
        if not a.no_level:
            notes = H.sounding_notes(tree, name)
            key = f"{a.bank}_{name}_fx"
            try:
                refs = H.reference(serum, key, tree, notes, 2, 4.0, 1.0)
            except RuntimeError as e:
                skipped.append((name, [f"Serum made no sound: {str(e)[:50]}"]))
                continue
            it = {"name": name, "header": header, "tree": tree, "notes": notes, "refs": refs, "end": F.sounding_end(refs)}
            C.mapped = lambda *args, _m=m, **kw: copy.deepcopy(_m)         # fit_item maps the preset itself; hand it ours
            state = finalize_level(m, it, serum, a.bank)
            gap, berr = quality(state, it)
            if abs(gap) > a.max_level_gap or berr > a.max_band_rms:
                skipped.append((name, [f"too far from Serum after the fit: level {gap:+.1f} dB, octave bands {berr:.1f} dB rms"]))
                print(f"  dropped {name}: level {gap:+.1f} dB, bands {berr:.1f} dB rms", flush=True)
                continue
        (bank_dir / f"{name}.json").write_text(json.dumps({"name": name, "module": "tinyhouse", "version": 1, "state": state}) + "\n", encoding="utf-8")
        kept.append(name)
        print(f"  kept {name}" + ("" if a.no_level else f"  (volume {int(PF.getk(state, 'volume', 0))}, {m['tier']}, level {gap:+.1f} dB, bands {berr:.1f} dB rms)"), flush=True)
        if a.limit and len(kept) >= a.limit:
            break
    lines = [f"# {a.bank}: {len(kept)} imported, {len(skipped)} skipped", ""] + [f"- {n}: {'; '.join(w)}" for n, w in skipped]
    (a.out / f"skipped_{a.bank.replace(' ', '_')}.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"{a.bank}: {len(kept)} imported, {len(skipped)} skipped -> {bank_dir}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    H.LOG_PATH = ROOT / "calibration/import_pack.log"
    main()
