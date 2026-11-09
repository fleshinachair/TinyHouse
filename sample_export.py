#!/usr/bin/env python3
"""Serum's sample oscillators as TinyHouse sample sets (.tbs).

A Serum preset's Sample oscillator names one audio file (relative to Serum's Samples folder); its Multisample oscillator names an SFZ file
(relative to the Multisamples folder) that maps key ranges to files. Either becomes one .tbs file the engine's sample oscillator loads:

  "TBS2" | u32 regions | per region: u8 lokey, u8 hikey, u8 root key, u8 flags (1 = loops), u8 lovel, u8 hivel, f32 loop start, f32 loop end
  (both as a fraction of the region's length), f32 sample rate, f32 tune in cents, f32 volume in dB, u32 frames, u8 channels (1 or 2), then the
  samples as int16. (TBS1, still read by the engine, has neither the velocity layer nor the volume: it played the first layer of a key at any velocity.)

These are Serum's own factory sounds (or a pack's): the .tbs files go beside the wavetables in the working folders under out/ and are not
to be committed or redistributed. The converter passes the sample set's path to the module as the `smp_set` parameter.
"""
import re
import struct
from pathlib import Path

import numpy as np
import soundfile as sf

from serum_paths import SERUM_ROOT      # noqa: E402  (set the SERUM_ROOT environment variable if Serum is not in the default place)
MAX_FRAMES = 1 << 21                 # 47 s at 44.1 kHz: no region longer than this is kept whole


def _sfz_num(v) -> float:
    """An SFZ number that may carry a stray unit letter ('796s'): its digits."""
    try:
        return float(v)
    except ValueError:
        m = re.match(r"\s*-?\d+(\.\d+)?", str(v))
        return float(m.group(0)) if m else 0.0


def parse_sfz(path: Path) -> list[dict]:
    """The regions of an SFZ file: key range, root key, loop points (in samples), tune (cents), the file, with group opcodes inherited."""
    text = re.sub(r"//[^\n]*", "", path.read_text(encoding="utf-8", errors="replace"))
    group, regions, default_path = {}, [], ""
    for m in re.finditer(r"<(group|region|global|master|control)>([^<]*)", text):
        kind, body = m.group(1), m.group(2)
        ops = {}
        for k, v in re.findall(r"(\w+)=((?:(?!\s+\w+=).)+)", body.replace("\n", " ")):
            ops[k] = v.strip()
        if kind == "control":
            default_path = ops.get("default_path", "").replace("\\", "/")
        elif kind in ("group", "global", "master"):
            group = ops
        elif kind == "region":
            regions.append({**group, **ops})
    out = []
    for r in regions:
        if "sample" not in r:
            continue
        key = int(r["key"]) if "key" in r else None
        lo = int(r.get("lokey", key if key is not None else 0))
        hi = int(r.get("hikey", key if key is not None else 127))
        root = int(r.get("pitch_keycenter", key if key is not None else 60))
        loops = r.get("loop_mode", "no_loop") in ("loop_continuous", "loop_sustain")
        out.append({"lovel": int(float(r.get("lovel", 0))), "hivel": int(float(r.get("hivel", 127))), "volume": float(r.get("volume", 0)),
                    "lo": lo, "hi": hi, "root": root, "loops": loops, "tune": float(r.get("tune", 0)) + 100.0 * float(r.get("transpose", 0)),
                    "loop_start": _sfz_num(r.get("loop_start", 0)), "loop_end": _sfz_num(r.get("loop_end", 0)),
                    "file": (path.parent / default_path / r["sample"].replace("\\", "/")).resolve()})
    return out


MAX_ONESHOT_S = 4.0                  # a sample that does not loop is cut at 4 s: the longest note a preset is played for here


def _region(audio: np.ndarray, rate: float, lo: int, hi: int, root: int, loop: tuple[float, float] | None, tune: float,
            lovel: int = 0, hivel: int = 127, volume_db: float = 0.0) -> bytes:
    if audio.ndim == 1:
        audio = audio[:, None]
    audio = audio[:, :2]
    ch = audio.shape[1]
    n = len(audio)
    keep = min(n, int(np.ceil(loop[1] * n)) + 1) if loop else min(n, int(MAX_ONESHOT_S * rate))
    keep = min(keep, MAX_FRAMES)
    if loop:                                                  # the loop points are fractions of the kept part now
        loop = (min(loop[0] * n / keep, 1.0), min(loop[1] * n / keep, 1.0))
    pcm = np.clip(audio[:keep], -1.0, 1.0)
    data = (pcm * 32767.0).astype("<i2").tobytes()
    ls, le = loop if loop else (0.0, 0.0)
    head = struct.pack("<BBBBBBfffffIB", lo, hi, root, 1 if loop else 0, lovel, hivel, ls, le, rate, tune, volume_db, keep, ch)
    return head + data


def write_set(regions: list[bytes], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(b"TBS2" + struct.pack("<I", len(regions)))
        for r in regions:
            f.write(r)
    return dest


def export_multisample(sfz_rel: str, dest: Path, loop_pct: tuple[float, float] | None = None) -> Path | None:
    """A Multisample oscillator's SFZ as one set. loop_pct: the oscillator's own Loop Start/End (percent of each sample), if it stores them;
    otherwise the SFZ's loop points are used."""
    sfz = SERUM_ROOT / "Multisamples" / sfz_rel.replace("\\", "/")
    if not sfz.is_file():
        return None
    out = []
    for r in parse_sfz(sfz):
        if not r["file"].is_file():
            continue
        audio, rate = sf.read(str(r["file"]), dtype="float32", always_2d=True)
        n = len(audio)
        loop = None
        if loop_pct is not None:
            loop = (loop_pct[0] / 100.0, loop_pct[1] / 100.0)
        elif r["loops"] and r["loop_end"] > r["loop_start"]:
            loop = (r["loop_start"] / n, min(r["loop_end"] / n, 1.0))
        out.append(_region(audio, rate, r["lo"], r["hi"], r["root"], loop, r["tune"], r["lovel"], r["hivel"], r["volume"]))
    return write_set(out, dest) if out else None


_found: dict = {}


def find_sample(rel: str) -> Path | None:
    """A sample named relative to Serum's Samples folder, or (the noise slot names it from a library folder below that) found by its last two
    path parts anywhere under it."""
    rel = rel.replace("\\", "/").lstrip("/")
    direct = SERUM_ROOT / "Samples" / rel
    if direct.is_file():
        return direct
    if rel not in _found:
        tail = "/".join(rel.split("/")[-2:])
        _found[rel] = next((p for p in (SERUM_ROOT / "Samples").rglob(Path(rel).name) if p.as_posix().endswith(tail)), None)
    return _found[rel]


def export_sample(sample_rel: str, dest: Path, base_note: int = 60, loop_pct: tuple[float, float] | None = None) -> Path | None:
    """A Sample oscillator's one file as a one-region set spanning the keyboard."""
    path = find_sample(sample_rel)
    if path is None:
        return None
    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    loop = (loop_pct[0] / 100.0, loop_pct[1] / 100.0) if loop_pct is not None else None
    return write_set([_region(audio, rate, 0, 127, base_note, loop, 0.0)], dest)


def sample_osc(tree: dict, i: int) -> dict | None:
    """What the converter needs of oscillator i if it is a Sample or Multisample: kind, file, loop %, pitch settings."""
    o = tree.get(f"Oscillator{i}")
    pp = o.get("plainParams") if isinstance(o, dict) else None
    if not isinstance(pp, dict) or pp.get("kParamType") not in ("kOsc_Sample", "kOsc_MultiSample"):
        return None
    multi = pp["kParamType"] == "kOsc_MultiSample"
    node = o.get(f"MultiSampleOsc{i}" if multi else f"SampleOsc{i}", {})
    loop = None
    if "kParamLoopStart" in pp and "kParamLoopEnd" in pp:
        loop = (float(pp["kParamLoopStart"]), float(pp["kParamLoopEnd"]))
    return {"multi": multi, "file": node.get("sfzPathRelative" if multi else "samplePathRelative"), "base_note": int(node.get("baseNote", 60)),
            "loop": loop, "octave": float(pp.get("kParamOctave", 0.0)), "pitch": float(pp.get("kParamPitch", 0.0)),
            "fine": float(pp.get("kParamFine", 0.0)), "level": float(pp.get("kParamVolume", 0.75))}


def noise_sample(tree: dict) -> dict | None:
    """The noise slot's sample, if it is a one-shot sample (a click or hit) rather than a noise to loop: played once, at its own pitch."""
    o = tree.get("Oscillator3")
    pp = o.get("plainParams") if isinstance(o, dict) else None
    node = o.get("NoiseOsc3") if isinstance(o, dict) else None
    if not isinstance(pp, dict) or not isinstance(node, dict) or not node.get("relativePathToNoiseSample"):
        return None
    q = node.get("plainParams") if isinstance(node.get("plainParams"), dict) else {}
    if not q.get("kParamOneShot", 0):
        return None
    return {"multi": False, "file": node["relativePathToNoiseSample"], "base_note": 60, "loop": None, "octave": 0.0, "pitch": 0.0, "fine": 0.0,
            "level": float(pp.get("kParamVolume", 0.75)), "fixed": True}


def export_for(tree: dict, i: int, out_dir: Path) -> tuple[Path, dict] | None:
    """Write oscillator i's sample set under out_dir and return (path, info), or None if the files are not there."""
    info = sample_osc(tree, i)
    if not info or not info["file"]:
        return None
    return export_info(info, out_dir)


def _magic(path: Path) -> bytes:
    with open(path, "rb") as f:
        return f.read(4)


def export_info(info: dict, out_dir: Path) -> tuple[Path, dict] | None:
    if not info or not info["file"]:
        return None
    stem = re.sub(r"[^A-Za-z0-9]+", "_", Path(info["file"]).with_suffix("").as_posix()).strip("_")
    if info["loop"]:
        stem += "_L%d_%d" % (round(info["loop"][0]), round(info["loop"][1]))
    dest = out_dir / f"{stem}.tbs"
    if not dest.exists() or _magic(dest) != b"TBS2":                # a set exported before the velocity layers were read is exported again
        made = (export_multisample(info["file"], dest, info["loop"]) if info["multi"] else export_sample(info["file"], dest, info["base_note"], info["loop"]))
        if made is None:
            return None
    return dest, info


# Serum's noise slot playing a sample plays it at its own spectrum, 11.6 dB under the sample's rms at oscillator volume 0.75, while Serum's White noise at that
# volume is -22.4 dB (calibration/probe_noise_sample.py: AC hum1/2, J 60, J106, H-Breath, AlphaNz, MicrKrg Noise, every octave band within 1 dB, level within 0.3 dB).
# The engine has no sample to play there, so a noise shaped to the sample's octave spectrum stands in: its Type's slope (White +3 dB/octave, Pink flat, Brown -3,
# equal at the 1-2 kHz octave) through the engine's 12 dB/octave high-pass and low-pass, fitted to the sample's octave levels (every octave within 40 dB of the loudest alike).
NOISE_PLAYBACK_DB, NOISE_WHITE_DB = -11.6, -22.4
_OCT_LO = [62.5 * 2 ** i for i in range(9)]               # octaves 62 Hz .. 16 kHz
_standin: dict = {}


def _noise_pot_hz(p: float) -> float:
    return 20.0 * 1000.0 ** (p / 127.0)


def noise_standin(rel: str):
    """(engine noise type, hp pot, lp pot, level gain in dB over White at the same oscillator volume) for a noise slot's sample, or None if the file is missing."""
    if rel in _standin:
        return _standin[rel]
    fp = find_sample(rel)
    res = None
    if fp:
        x, sr = sf.read(str(fp))
        x = x if x.ndim == 1 else x.mean(1)
        n = min(len(x), 1 << 18)
        seg = x[:n]
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
        f = np.fft.rfftfreq(len(seg), 1.0 / sr)
        tot = spec.sum() + 1e-30
        frac = np.array([spec[(f >= lo) & (f < 2 * lo)].sum() for lo in _OCT_LO]) / tot
        rms_db = 10 * np.log10(float((x ** 2).mean()) + 1e-30)
        target = (rms_db + NOISE_PLAYBACK_DB) + 10 * np.log10(frac + 1e-12)          # absolute octave levels as Serum plays them
        w = (frac > frac.max() * 1e-4).astype(float)           # every octave within 40 dB of the loudest counts alike, as the band profile counts them
        w /= w.sum()
        centre = np.array(_OCT_LO) * 1.414
        white = NOISE_WHITE_DB + 10 * np.log10(np.array(_OCT_LO) / (sr / 2.0))
        at1k = white[4]
        best = None
        for ty, slope in ((0, 3.0), (1, 0.0), (4, -3.0)):
            base = at1k + slope * (np.log2(centre / 1414.0))
            for lp in range(30, 128, 2):
                for hp in [0] + list(range(10, 100, 6)):
                    if hp and _noise_pot_hz(hp) >= _noise_pot_hz(lp) / 3:
                        continue
                    m = base - 10 * np.log10(1 + (centre / _noise_pot_hz(lp)) ** 4)
                    if hp:
                        m = m - 10 * np.log10(1 + (_noise_pot_hz(hp) / centre) ** 4)
                    e = target - m
                    g = float((w * e).sum())
                    loss = float((w * (e - g) ** 2).sum())
                    if best is None or loss < best[0]:
                        best = (loss, ty, hp, lp, g)
        _, ty, hp, lp, g = best
        res = (ty, hp, lp, g)
    _standin[rel] = res
    return res
