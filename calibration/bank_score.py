#!/usr/bin/env python3
"""How close a whole bank is to Serum, so a fix can be measured across it and the release notes can say how many presets it improved and by how much.

  bank_score.py run --label 0.9.16-pre [--bank Factory] [--jobs 4] [--names A,B,...]
      every preset of the bank through the importer's own path (import_web.convert_one, what the Remote UI and the Move's convert-on-load run), the engine render
      (calibration/tablor_cli_det.exe: the engine's noise fixed, so an untouched preset scores the same every run) against Serum's full-chain render of the same notes (cached in
      calibration/refs/ as Factory_<name>_fx_*: made once, only presets with no cached render load Serum). Writes calibration/scores/<label>.json.
      BANK_SCORE_DROP_FILTER=Reverb1 scores the bank as if that voice filter model were not carried (what the converter did before it), so one fix can be measured on its own.
  bank_score.py compare BEFORE AFTER [--min 0.5] [--list 10]
      the two scorecards side by side: presets improved / worse by more than --min dB, the mean before and after, the median gain, and a few lines ready for the release notes.

The score is the importer gate's octave-band error (import_pack.floored_band_error: rms over the octave bands of each render's level re its loudest band, every band held at 40 dB
under the loudest, engine minus Serum), in dB: 0 is Serum's spectrum, the gate drops a preset above 14. "level" is the engine's loudness minus Serum's (dB) over the sounding part."""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "converter", ROOT / "calibration"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
SCORES = ROOT / "calibration/scores"
DET_CLI = ROOT / "calibration/tablor_cli_det.exe"     # built by `scripts/zig-build.sh native` with TB_DETERMINISTIC_NOISE


def _presets(bank, names):
    import serum_paths as S
    files = sorted((Path(S.SERUM_ROOT) / "Presets" / bank).rglob("*.SerumPreset"))
    return [p for p in files if not names or p.stem in names]


def _ref_key(bank, name):
    return f"{bank}_{name}_fx"


def make_refs(bank, files):
    """Serum's renders for every preset that has none cached (one plugin, in this process). Returns {name: notes} for the presets Serum could play."""
    import numpy as np
    import test_render_calibration as H
    from serum2_preset_loader.codec import decode_preset_file
    plug = {}
    def serum():
        if "s" not in plug or plug["s"].audio_seconds > 900:
            plug["s"] = H.Serum()
        return plug["s"]
    out, made = {}, 0
    for p in files:
        try:
            header, tree = decode_preset_file(p)
            if not H.usable(tree):
                continue
            notes = H.sounding_notes(tree, p.stem)
            path = H.OUT / "refs" / f"{re.sub(r'[^A-Za-z0-9]+', '_', _ref_key(bank, p.stem))}_{'_'.join(map(str, notes))}_2x4.0.npy"
            if not path.exists():
                refs = H.reference(lambda: serum(), _ref_key(bank, p.stem), tree, notes, 2, 4.0, 1.0)
                made += 1
                if max(float(np.abs(r).max()) for r in refs) < 1e-4:
                    path.unlink(missing_ok=True)
                    continue
            out[p.stem] = notes
        except Exception as e:                       # a preset Serum cannot play is left out of the score, not the run
            print(f"  no Serum render for {p.stem}: {type(e).__name__}: {str(e)[:80]}", flush=True)
    print(f"Serum renders: {len(out)} presets ({made} rendered now, the rest cached)", flush=True)
    return out


_TABLES = {}


def tables():
    """Serum's Tables folder and its tables by file name: what the Remote UI's import hands convert_one (import_web.convert: the page's copy of the folder and a
    name map over it). Without them every wavetable oscillator falls back to the engine's Init table."""
    if not _TABLES:
        import serum_paths as S
        d = Path(S.SERUM_TABLES)
        _TABLES["dir"], _TABLES["pack"] = d, {p.name.lower(): p for p in d.rglob("*.wav")}
    return _TABLES["dir"], _TABLES["pack"]


def score_one(args):
    """One preset: convert it as the importer does, render the engine twice on fixed seeds, score against the cached Serum render."""
    bank, path, notes = args
    import numpy as np
    import test_render_calibration as H
    import import_web as W
    import import_pack as IP
    import fit_level as L
    import fit_chain as C
    import fit_perceptual as F
    import voice_survey as VS
    W.RECORD_DEPS = False
    for m in filter(None, os.environ.get("BANK_SCORE_DROP_FILTER", "").split(",")):     # score as if the converter did not carry this voice filter model (the old fallback), to measure one fix
        W.d2m.FILTER_MAP.pop(m, None)
    p = Path(path)
    name = p.stem
    try:
        refs = H.reference(None, _ref_key(bank, name), None, notes, 2, 4.0, 1.0)
        r = W.convert_one(p, name, bank, *tables(), H.WT_OUT)
        if not r["json"]:
            return name, {"error": r["error"] or "no preset", "tier": r["tier"]}
        state = json.loads(r["json"])["state"]
        H.CLI = DET_CLI                              # the engine's noise the same every run (two renders would be identical: one is enough)
        H._seed = int(hashlib.md5(name.encode()).hexdigest()[:6], 16)
        ren = [H.run_cli(state, notes, C.DUR, C.HOLD)]
        end = F.sounding_end(refs)
        level = L.rms_db(ren, end) - L.rms_db(refs, end)
        eb = np.mean([VS.bands(x) for x in ren], axis=0)
        sb = np.mean([VS.bands(x) for x in IP.as_played(refs)], axis=0)
        e_, s_ = np.maximum(eb - eb.max(), -IP.BAND_FLOOR_DB), np.maximum(sb - sb.max(), -IP.BAND_FLOOR_DB)
        return name, {"band": round(IP.floored_band_error(eb, sb), 2), "level": round(float(level), 2), "tier": r["tier"], "gaps": r["gaps"], "approx": r["approx"],
                      "not_carried": r["not_carried"], "bands": [round(float(x), 1) for x in e_ - s_]}      # engine minus Serum per octave band (voice_survey.EDGES), each re its loudest, floored like the gate
    except Exception as e:
        return name, {"error": f"{type(e).__name__}: {str(e)[:120]}"}


def run(a):
    if not DET_CLI.exists():
        sys.exit(f"{DET_CLI} is missing: run schwung-tablor/scripts/zig-build.sh native")
    names = set(a.names.split(",")) if a.names else None
    files = _presets(a.bank, names)
    notes = make_refs(a.bank, files)
    jobs = [(a.bank, str(p), notes[p.stem]) for p in files if p.stem in notes]
    res = {}
    with ProcessPoolExecutor(max_workers=a.jobs) as ex:
        for i, (name, row) in enumerate(ex.map(score_one, jobs, chunksize=4)):
            res[name] = row
            if (i + 1) % 50 == 0:
                print(f"  {i + 1} / {len(jobs)}", flush=True)
    retry = [j for j in jobs if "band" not in res[Path(j[1]).stem]]
    for j in retry:                                  # the workers share the converter's caches (fx_tablor_cache.json): a read that met another worker's write is tried again alone
        name, row = score_one(j)
        res[name] = row
    if retry:
        print(f"  {len(retry)} tried again alone, {sum('band' in res[Path(j[1]).stem] for j in retry)} scored", flush=True)
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
    except OSError:
        commit, dirty = "", False
    import import_web as W
    doc = {"label": a.label, "bank": a.bank, "date": datetime.datetime.now().isoformat(timespec="seconds"), "commit": commit + ("+local" if dirty else ""),
           "conv": W.CONV["hash"], "presets": res}
    SCORES.mkdir(exist_ok=True)
    out = SCORES / f"{a.label}.json"
    out.write_text(json.dumps(doc, indent=0))
    ok = [r["band"] for r in res.values() if "band" in r]
    print(f"{len(ok)} scored, {len(res) - len(ok)} not ({a.bank}); mean band error {sum(ok) / max(len(ok), 1):.2f} dB -> {out}")


def compare(a):
    import statistics as st
    A, B = (json.loads((SCORES / f"{x}.json").read_text()) if not x.endswith(".json") else json.loads(Path(x).read_text()) for x in (a.before, a.after))
    pa, pb = A["presets"], B["presets"]
    both = sorted(n for n in pa if n in pb and "band" in pa[n] and "band" in pb[n])
    d = {n: pa[n]["band"] - pb[n]["band"] for n in both}            # positive: closer to Serum
    better = [n for n in both if d[n] > a.min]
    worse = [n for n in both if d[n] < -a.min]
    ma, mb = st.mean(pa[n]["band"] for n in both), st.mean(pb[n]["band"] for n in both)
    pct = lambda n: 100.0 * d[n] / pa[n]["band"] if pa[n]["band"] > 0 else 0.0
    newly = [n for n in pb if "band" in pb[n] and n not in pa or (n in pa and "band" not in pa[n] and "band" in pb[n])]
    lost = [n for n in pa if "band" in pa[n] and (n not in pb or "band" not in pb[n])]
    gate = 14.0
    pass_a, pass_b = sum(pa[n]["band"] <= gate for n in both), sum(pb[n]["band"] <= gate for n in both)
    print(f"{A['label']} ({A['commit']}) -> {B['label']} ({B['commit']}), {A['bank']}: {len(both)} presets scored in both")
    print(f"  closer to Serum by more than {a.min} dB: {len(better)}" + (f" (median {st.median(d[n] for n in better):.1f} dB, {st.median(pct(n) for n in better):.0f} % closer)" if better else ""))
    print(f"  further by more than {a.min} dB: {len(worse)}" + (f" (median {st.median(-d[n] for n in worse):.1f} dB)" if worse else ""))
    print(f"  mean octave-band error {ma:.2f} -> {mb:.2f} dB ({100.0 * (ma - mb) / ma:+.1f} %), within the import gate (14 dB): {pass_a} -> {pass_b}")
    if newly:
        print(f"  scored now, not before: {len(newly)}")
    if lost:
        print(f"  scored before, not now: {len(lost)}: " + ", ".join(lost[:a.list]))
    for title, lst, key in (("most improved", better, lambda n: -d[n]), ("most worse", worse, lambda n: d[n])):
        if lst:
            print(f"  {title}:")
            for n in sorted(lst, key=key)[:a.list]:
                print(f"    {n:34s} {pa[n]['band']:5.1f} -> {pb[n]['band']:5.1f} dB")
    if better or worse:
        print("\nRelease notes:")
        print(f"- {len(better)} of {len(both)} {A['bank']} presets sound closer to Serum" + (f" (typically {st.median(pct(n) for n in better):.0f} % closer, up to {max(pct(n) for n in better):.0f} %)" if better else "")
              + (f"; {len(worse)} moved further" if worse else "") + f". Across the bank the average distance from Serum went from {ma:.1f} to {mb:.1f} dB ({100.0 * (ma - mb) / ma:+.1f} %).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--label", required=True)
    r.add_argument("--bank", default="Factory")
    r.add_argument("--jobs", type=int, default=4)
    r.add_argument("--names", default="")
    c = sub.add_parser("compare")
    c.add_argument("before"); c.add_argument("after")
    c.add_argument("--min", type=float, default=0.5, help="dB a preset has to move by to count as better or worse")
    c.add_argument("--list", type=int, default=10)
    a = ap.parse_args()
    run(a) if a.cmd == "run" else compare(a)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
