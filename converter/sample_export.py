#!/usr/bin/env python3
"""Serum's sample oscillators as TinyHouse sample sets (.tbs).

A Serum preset's Sample oscillator names one audio file (relative to Serum's Samples folder); its Multisample oscillator names an SFZ file
(relative to the Multisamples folder) that maps key ranges to files. Either becomes one .tbs file the engine's sample oscillator loads:

  "TBS3" | u32 regions | per region: u8 lokey, u8 hikey, u8 root key, u8 flags (bit 0 = loops, bit 1 = ping-pong: forward, then backward, between the loop points), u8 lovel, u8 hivel, f32 loop start, f32 loop end
  (both as a fraction of the region's length), f32 sample rate, f32 tune in cents, f32 volume in dB, f32 loop crossfade (a fraction of the loop's length), u32 frames, u8 channels (1 or 2), then the
  samples as int16. (TBS1 and TBS2, still read by the engine, lack the velocity layer and volume, and the crossfade: TBS1 played the first layer of a key at any velocity.)

These are Serum's own factory sounds (or a pack's): the .tbs files go beside the wavetables in the working folders under out/ and are not
to be committed or redistributed. The converter passes the sample set's path to the module as the `smp_set` parameter.
"""
import hashlib
import re
import struct
import tempfile
from pathlib import Path

import numpy as np
try:
    import soundfile as sf
except ImportError:             # the browser importer (Pyodide) has no audio decoder: sample files are not exported there
    sf = None

from serum_paths import SERUM_ROOT      # noqa: E402  (set the SERUM_ROOT environment variable if Serum is not in the default place)

# The browser importer has no audio decoder in Python: the page decodes the files a preset names, and writes each as "<name>.dec" (u32 rate, u32 channels, then 16-bit samples, the precision of the exported sets)
# under BROWSER_DIR/Samples and BROWSER_DIR/Multisamples (the layout of Serum's own folder). With BROWSER_DIR set the reader looks there instead.
BROWSER_DIR: Path | None = None

# Serum plays a Multisample oscillator 7.3 dB louder than the engine plays the same files at the same oscillator volume (calibration/dev/probe_multisample_level.py:
# Trumpets LE, Full Strings LE, Violins Mute LE and Bass Clarinet LE at volumes 0.25 / 0.5 / 1.0, every one within 0.1 dB of +7.3). A Sample oscillator agrees
# within 0.3 dB with no offset (probe_sample_level.py: six files spanning 22 dB of recorded level), so the offset is the multisample's alone and goes into each
# of its regions' volume, where the engine applies it at playback (sample.h); the int16 samples stay as recorded.
MULTISAMPLE_PLAYBACK_DB = 7.3
EXPORT_VERSION = 4            # bumped when the export's law changes: a set made by an older version is exported again (export_info)


def _root(kind: str) -> Path:
    return (BROWSER_DIR if BROWSER_DIR is not None else SERUM_ROOT) / kind


# A Sample oscillator whose audio is stored inside the preset (`embedded_audio`: planar float channels; the file it was loaded from is on the author's machine) has no
# path under Serum's Samples folder. Its audio is written here in the decoded-copy layout (see _read) and named "_embedded/<name>", which find_sample resolves.
EMBED_DIR = Path(tempfile.gettempdir()) / "tinyhouse_embedded_samples"
EMBED_PREFIX = "_embedded/"


def _embed(node: dict) -> str | None:
    """The name ("_embedded/<table>_<digest>") of a Sample oscillator node's embedded audio, after writing it to EMBED_DIR; None if the node holds none."""
    ea = node.get("embedded_audio")
    data = ea.get("data") if isinstance(ea, dict) else None
    if not data:
        return None
    a = np.asarray(data, dtype=np.float32)
    ch = int(node.get("numChannels") or (a.shape[0] if a.ndim == 2 else 1))
    a = a[:, None] if a.ndim == 1 else (a.T if a.shape[0] == ch else a)        # planar (channels, frames) -> (frames, channels)
    a = a[:, :2]
    pcm = (np.clip(a, -1.0, 1.0) * 32767.0).astype("<i2")
    rate = int(round(float(ea.get("sampleRate") or node.get("sampleRate") or 44100)))
    blob = struct.pack("<II", rate, pcm.shape[1]) + pcm.tobytes()
    name = re.sub(r"[^A-Za-z0-9]+", "_", str(ea.get("table_name") or "sample")).strip("_")[:40] + "_" + hashlib.sha1(blob).hexdigest()[:8]
    dest = EMBED_DIR / (name + ".wav.dec")
    if not dest.is_file():
        EMBED_DIR.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
    return EMBED_PREFIX + name + ".wav"


def _real(p) -> Path | None:
    """The file that holds p's audio: p itself, or its decoded copy beside it."""
    p = Path(p)
    if p.is_file():
        return p
    d = p.with_name(p.name + ".dec")
    return d if d.is_file() else None


def _read(p, dtype="float32", always_2d=True):
    """(audio, rate) of a sample file, from soundfile or from the page's decoded copy."""
    real = _real(p)
    if real is not None and real.suffix == ".dec":
        b = real.read_bytes()
        rate, ch = struct.unpack("<II", b[:8])
        a = (np.frombuffer(b, dtype="<i2", offset=8).reshape(-1, max(ch, 1)) / 32767.0).astype(dtype)
        return (a if always_2d or a.shape[1] > 1 else a[:, 0]), float(rate)
    return sf.read(str(real or p), dtype=dtype, always_2d=always_2d)
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
        out.append({"lovel": int(_sfz_num(r.get("lovel", 0))), "hivel": int(_sfz_num(r.get("hivel", 127))), "volume": _sfz_num(r.get("volume", 0)),
                    "lo": lo, "hi": hi, "root": root, "loops": loops, "tune": _sfz_num(r.get("tune", 0)) + 100.0 * _sfz_num(r.get("transpose", 0)),
                    "loop_start": _sfz_num(r.get("loop_start", 0)), "loop_end": _sfz_num(r.get("loop_end", 0)), "loop_crossfade": _sfz_num(r.get("loop_crossfade", 0)),
                    "file": (path.parent / default_path / r["sample"].replace("\\", "/")).resolve()})
    return out


MAX_ONESHOT_S = 4.0                  # a sample that does not loop is cut at 4 s: the longest note a preset is played for here


def _region(audio: np.ndarray, rate: float, lo: int, hi: int, root: int, loop: tuple[float, float] | None, tune: float,
            lovel: int = 0, hivel: int = 127, volume_db: float = 0.0, ping_pong: bool = False, xfade: float = 0.0) -> bytes:
    if audio.ndim == 1:
        audio = audio[:, None]
    audio = audio[:, :2]
    ch = audio.shape[1]
    n = len(audio)
    # a looped sample is kept to its loop end, and a ping-pong crossfade also reads the audio just beyond it
    tail = xfade * (loop[1] - loop[0]) if loop and ping_pong else 0.0
    keep = min(n, int(np.ceil((loop[1] + tail) * n)) + 1) if loop else min(n, int(MAX_ONESHOT_S * rate))
    keep = min(keep, MAX_FRAMES)
    if loop:                                                  # the loop points are fractions of the kept part now
        loop = (min(loop[0] * n / keep, 1.0), min(loop[1] * n / keep, 1.0))
    pcm = np.clip(audio[:keep], -1.0, 1.0)
    data = (pcm * 32767.0).astype("<i2").tobytes()
    ls, le = loop if loop else (0.0, 0.0)
    head = struct.pack("<BBBBBBffffffIB", lo, hi, root, (1 | (2 if ping_pong else 0)) if loop else 0, lovel, hivel, ls, le, rate, tune, volume_db, xfade if loop else 0.0, keep, ch)
    return head + data


def write_set(regions: list[bytes], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(b"TBS3" + struct.pack("<I", len(regions)))
        for r in regions:
            f.write(r)
    return dest


def export_multisample(sfz_rel: str, dest: Path) -> Path | None:
    """A Multisample oscillator's SFZ as one set. Its loops are the SFZ's own: Serum renders the same whatever Loop Mode and Loop Start/End the
    oscillator stores (calibration/dev/probe_loop_mode.py), and a region without a loop in the SFZ does not loop."""
    sfz = _root("Multisamples") / sfz_rel.replace("\\", "/")
    if not sfz.is_file():
        return None
    out = []
    for r in parse_sfz(sfz):
        if _real(r["file"]) is None:
            continue
        audio, rate = _read(r["file"])
        n = len(audio)
        loop = None
        xfade = 0.0
        if r["loops"] and r["loop_end"] > r["loop_start"]:
            loop = (r["loop_start"] / n, min(r["loop_end"] / n, 1.0))
            xfade = min(r["loop_crossfade"] * rate / (r["loop_end"] - r["loop_start"]), 1.0)          # the SFZ gives its crossfade in seconds
        out.append(_region(audio, rate, r["lo"], r["hi"], r["root"], loop, r["tune"], r["lovel"], r["hivel"], r["volume"] + MULTISAMPLE_PLAYBACK_DB, xfade=xfade))
    return write_set(out, dest) if out else None


_found: dict = {}


def find_sample(rel: str) -> Path | None:
    """A sample named relative to Serum's Samples folder, or (the noise slot names it from a library folder below that) found by its last two
    path parts anywhere under it."""
    rel = rel.replace("\\", "/").lstrip("/")
    if rel.startswith(EMBED_PREFIX):
        return _real(EMBED_DIR / rel[len(EMBED_PREFIX):])
    direct = _real(_root("Samples") / rel)
    if direct is not None:
        return direct
    if rel not in _found:
        tail = "/".join(rel.split("/")[-2:])
        hit = next((p for p in (_root("Samples")).rglob("*" + Path(rel).name + "*") if p.as_posix().endswith(tail) or p.as_posix().endswith(tail + ".dec")), None)
        if hit is None:
            return None                                  # not cached: the browser importer may add the file later
        _found[rel] = hit
    return _found[rel]


def export_sample(sample_rel: str, dest: Path, base_note: int = 60, loop_pct: tuple[float, float] | None = None, ping_pong: bool = False, xfade: float = 0.0) -> Path | None:
    """A Sample oscillator's one file as a one-region set spanning the keyboard."""
    path = find_sample(sample_rel)
    if path is None:
        return None
    audio, rate = _read(path)
    loop = (loop_pct[0] / 100.0, loop_pct[1] / 100.0) if loop_pct is not None else None
    return write_set([_region(audio, rate, 0, 127, base_note, loop, 0.0, ping_pong=ping_pong, xfade=xfade)], dest)


# Serum's Convolution reverb: the impulse response is a file under Serum's Impulses folder, or stored in the preset (embeddedIR, planar float channels). The engine's convolver (conv.h) reads it
# as a one-region TBS3 set: the converter writes the IR after the Size stretch, the Decay envelope and the level (fxmap.conv_plan), as int16 at peak 0.99 with the level in the region's volume.
def ir_audio(node: dict) -> tuple[np.ndarray, float] | None:
    """(frames x channels float audio, sample rate) of a Conv node's IR, or None if its file is not on this machine."""
    ir = node.get("embeddedIR")
    if ir is not None and np.size(ir):
        nc = max(int(node.get("numChannels") or 1), 1)
        a = np.asarray(ir, dtype=np.float64).reshape(nc, -1).T
        nf = int(node.get("numFrames") or len(a))
        return a[:nf, :2], float(node.get("sampleRate") or 44100)
    rel = node.get("relativePathToIR")
    rel = str(rel).replace("\\", "/") if rel else None
    p = _real(_root("Impulses") / rel) if rel else None
    if p is None and rel and BROWSER_DIR is not None:                       # the page has not decoded it: a PC run that also has Serum's folder reads it from there
        p = _real(SERUM_ROOT / "Impulses" / rel)
    if p is None:
        return None
    a, rate = _read(p, dtype="float64")
    return a[:, :2], float(rate)


def write_ir(audio: np.ndarray, rate: float, gain_db: float, dest: Path) -> Path:
    """The (already processed) IR as a one-region set; gain_db is the level the engine plays it at (the audio itself is normalised to peak 0.99)."""
    if audio.ndim == 1:
        audio = audio[:, None]
    audio = audio[:, :2]
    peak = float(np.abs(audio).max()) or 1.0
    pcm = np.round(audio / peak * 0.99 * 32767.0).astype("<i2")
    head = struct.pack("<BBBBBBffffffIB", 0, 127, 60, 0, 0, 127, 0.0, 0.0, float(rate), 0.0, float(gain_db + 20.0 * np.log10(peak / 0.99)), 0.0, len(pcm), pcm.shape[1])
    return write_set([head + pcm.tobytes()], dest)


def sample_osc(tree: dict, i: int) -> dict | None:
    """What the converter needs of oscillator i if it is a Sample or Multisample: kind, file, loop %, pitch settings."""
    o = tree.get(f"Oscillator{i}")
    pp = o.get("plainParams") if isinstance(o, dict) else None
    if not isinstance(pp, dict) or pp.get("kParamType") not in ("kOsc_Sample", "kOsc_MultiSample", "kOsc_Spectral"):
        return None
    multi = pp["kParamType"] == "kOsc_MultiSample"
    # A Spectral oscillator resynthesises its sample (scan, spectral filter), which the engine cannot. Its audio played as a sample at the same keys gives the right octave-band
    # shape: Serum's Spectral oscillator alone against the sample from root C4 on notes 48 / 60 / 72, sustain 3.9 / 9.0 / 4.4 dB and attack 1.7 / 4.3 / 6.0 dB rms in octave bands
    # (root C3 or C5: 12-16 dB); without it the oscillator is missing and the top octaves are 23-38 dB short. The scan movement and the spectral filter are not carried.
    spectral = pp["kParamType"] == "kOsc_Spectral"
    node = o.get(f"MultiSampleOsc{i}" if multi else f"SpectralOsc{i}" if spectral else f"SampleOsc{i}", {})
    # A Sample oscillator loops only in kForward and kPingPong (Loop Start/End default to the whole file); no stored mode, kOff and kTailed play the file once
    # through, whatever points are stored (probe_loop_mode.py). A Multisample's loops come from its SFZ (export_multisample).
    mode = pp.get("kParamLoopMode")
    loop = None
    if not multi and mode in ("kForward", "kPingPong"):
        loop = (float(pp.get("kParamLoopStart", 0.0)), float(pp.get("kParamLoopEnd", 100.0)))
    file = node.get("sfzPathRelative" if multi else "samplePathRelative")
    embedded = not multi and not file and _embed(node)
    if embedded:
        file = embedded
    return {"multi": multi, "spectral": spectral, "file": file, "embedded": bool(embedded), "base_note": int(node.get("baseNote", 60)),
            "loop": loop, "ping_pong": loop is not None and mode == "kPingPong", "xfade": min(max(float(pp.get("kParamLoopCrossfade", 0.0)), 0.0), 100.0) / 100.0 if loop else 0.0, "octave": float(pp.get("kParamOctave", 0.0)), "pitch": float(pp.get("kParamPitch", 0.0)),
            "fine": float(pp.get("kParamFine", 0.0)), "level": float(pp.get("kParamVolume", 0.75)), "scan": float(pp.get("kParamScanRate", 0.0)) if spectral else None}


def granular_osc(tree: dict, i: int) -> dict | None:
    """A Granular oscillator's file as the same kind of record sample_osc returns, so the converter exports it as a one-region sample set (the engine's granular slot
    reads the set exactly as its sample slot does: Serum plays the file at its own pitch on the base note, root C4 unless the file says otherwise). The grain settings
    are not here (deephouse2move.granular_pots reads them); `granular` marks the record. Loop settings are not applied to the set: they are a granular setting."""
    o = tree.get(f"Oscillator{i}")
    pp = o.get("plainParams") if isinstance(o, dict) else None
    if not isinstance(pp, dict) or pp.get("kParamType") != "kOsc_Granular":
        return None
    node = o.get(f"GranularOsc{i}", {})
    file = node.get("samplePathRelative")
    embedded = not file and _embed(node)
    if embedded:
        file = embedded
    return {"multi": False, "spectral": False, "granular": True, "file": file, "embedded": bool(embedded), "base_note": int(node.get("baseNote", 60)),
            "loop": None, "ping_pong": False, "xfade": 0.0, "octave": float(pp.get("kParamOctave", 0.0)), "pitch": float(pp.get("kParamPitch", 0.0)),
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
        stem += "_L%d_%d" % (round(info["loop"][0]), round(info["loop"][1])) + ("_P" if info.get("ping_pong") else "") + ("_X%d" % round(100 * info["xfade"]) if info.get("xfade") else "")
    dest = out_dir / f"{stem}.tbs"
    stamp = dest.with_name(dest.name + ".v")                          # the export version the set was made with (not copied to the Move: deploy_samples.sh takes *.tbs)
    stale = not dest.exists() or _magic(dest) != b"TBS3"              # a set exported in an older format (before the velocity layers, or the loop crossfade) is exported again
    try:
        stale = stale or int(stamp.read_text().strip() or 0) < EXPORT_VERSION
    except (OSError, ValueError):
        stale = True
    if stale:
        made = (export_multisample(info["file"], dest) if info["multi"] else export_sample(info["file"], dest, info["base_note"], info["loop"], info.get("ping_pong", False), info.get("xfade", 0.0)))
        if made is None:
            return None
        stamp.write_text(str(EXPORT_VERSION))
    return dest, info


# Serum's noise slot playing a sample plays it at its own spectrum, 11.6 dB under the sample's rms at oscillator volume 0.75, while Serum's White noise at that
# volume is -22.4 dB (calibration/dev/probe_noise_sample.py: AC hum1/2, J 60, J106, H-Breath, AlphaNz, MicrKrg Noise, every octave band within 1 dB, level within 0.3 dB).
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
        x, sr = _read(fp, dtype="float64", always_2d=False)
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
    if res is not None:
        _standin[rel] = res
    return res
