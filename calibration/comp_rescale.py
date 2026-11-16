#!/usr/bin/env python3
"""The compressor's threshold, kept where Serum has it at the loudness the preset ends up at.

The engine's compressor sits before the master gain, so its threshold (the comp_thresh pots) is a level at the stage's input; Serum's is a
level at its output. The converter turns one into the other once, with the closed-form volume. The loudness fit then moves the volume
(oskar med k: 44 where the closed form says far higher), and the threshold is where it is no longer: the compressor works at the wrong level,
boosts a quiet tail, and a later note comes out of it far too loud. rescale() moves the thresholds with the volume.

  rescale(state, vol_from, vol_to) -> state     the blob with comp_thresh, comp_thresh2, comp_thresh3 re-derived for another volume
  fit_volume(state, v0, run, target_db, ...)    the volume at which the rescaled state plays at target_db, thresholds following each trial
"""
import math
import re

import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "converter"))
import deephouse2move as d2m

KEYS = ("comp_thresh", "comp_thresh2", "comp_thresh3")


def _get(state, k, default=0):
    m = re.search(r"(?<![a-z_0-9])%s=([^;]*);" % k, state)
    return float(m.group(1)) if m else default


def _set(state, k, v):
    return re.sub(r"(?<![a-z_0-9])%s=[^;]*;" % k, "%s=%d;" % (k, int(v)), state)


def gain_db(volume):
    return 20 * math.log10((max(volume, 1) / 127.0) ** 2 * d2m.MASTER_MAX)


def rescale(state, vol_from, vol_to):
    """The stages that work on a level before the master gain, moved with the volume: the compressor's thresholds (a level at Serum's output) and the
    calibrated distortion's pre-gain and output gain (the curve was measured at Serum's output, so a master gain x dB higher means the stage sees the signal
    x dB lower: the pre-gain goes up x dB and the output gain down x dB)."""
    if _get(state, "comp_mix") > 0 and (_get(state, "comp_ratio") > 0 or _get(state, "comp_multi")):
        for k in KEYS:
            pot = _get(state, k, None)
            if pot is None:
                continue
            t_out = -90.0 + 120.0 * pot / 127.0 + gain_db(vol_from)
            new = (t_out - gain_db(vol_to) + 90.0) / 120.0
            state = _set(state, k, round(min(max(new, 0.0), 1.0) * 127))
    if _get(state, "dist_cal") >= 1 and _get(state, "dist_mix") > 0 and _get(state, "dist_mode", 0) != 4:      # (mode 4, the hold, has no gain: its Drive pot is the hold length)
        dg = gain_db(vol_to) - gain_db(vol_from)
        state = _set(state, "dist_drive", round(min(max(_get(state, "dist_drive") + dg * 127.0 / 70.0, 0), 127)))
        state = _set(state, "dist_out", round(min(max(_get(state, "dist_out") - dg * 127.0 / 48.0, 0), 127)))
    return state


def level_stages(P):
    """True when the preset has a stage whose settings depend on the volume (rescale)."""
    return bool((P.get("comp_mix", 0) > 0 and (P.get("comp_ratio", 0) > 0 or P.get("comp_multi", 0))) or (P.get("dist_cal", 0) and P.get("dist_mix", 0) > 0))


def with_volume(state, v0, v, follow=True):
    """The state at volume v; with `follow` the thresholds move from where they were set for volume v0."""
    return rescale(_set(state, "volume", v), v0, v) if follow else _set(state, "volume", v)


def fit_volume(state, v0, rms_of, target_db, start=None, tol=0.4, iters=6, follow=True):
    """rms_of(state) -> dB. Secant steps on the volume (the output gain is 40 log10 v); returns the volume and its state.
    follow=False leaves the level-dependent stages where the state has them."""
    v = start or v0
    best = None
    for _ in range(iters):
        st = with_volume(state, v0, v, follow)
        err = rms_of(st) - target_db
        if best is None or abs(err) < abs(best[0]):
            best = (err, v, st)
        if abs(err) <= tol:
            break
        nv = int(round(min(max(v * 10 ** (-err / 40.0), 1), 127)))
        if nv == v:
            nv = v + (1 if err < 0 else -1)
        v = int(min(max(nv, 1), 127))
    return best[1], best[2]
