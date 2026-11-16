"""Shared helpers for the time-domain warp probes: render one oscillator through a warp and fold the held note into one cycle (512 bins)."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import copy, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration")); os.chdir(ROOT)
import numpy as np
import probe_vfilter as V
import test_render_calibration as H
SR = H.SR; F0 = V.F0; NB = 512
_, bt = H.load_preset("Keys - Loop")
pool = V.SerumPool()
TREES = {}
def tree(table, frames):
    k = (table, frames)
    if k not in TREES:
        TREES[k] = H.init_tree(bt, table=table, frames=frames)
    return TREES[k]

def render(table, frames, pos, mode=None, amt=0.0, extra=None, vol=None):
    t = copy.deepcopy(tree(table, frames))
    pp = {"kParamTablePos": pos, "kParamRandomPhase": 0.0}   # random phase off: the held note starts at the same phase every render
    if mode:
        pp["kParamWarpMenu"] = mode; pp["kParamWarp"] = amt
    if extra:
        pp.update(extra)
    t["Oscillator0"]["WTOsc0"]["plainParams"] = pp
    return pool.render(t)[0]

OFF = None
def cycle(x, anchor=None):
    """One cycle (NB bins) of the held note, folded from the known pitch. With random phase off every render starts at the same phase, so no alignment is needed;
    the cycle is rolled so that the reference saw's wrap (found once) is bin 0."""
    global OFF
    n0, n1 = int(0.35 * SR), int(1.2 * SR)
    ph = (np.arange(n0, n1) * F0 / SR) % 1.0
    idx = (ph * NB).astype(int) % NB
    c = np.zeros(NB); cnt = np.zeros(NB)
    np.add.at(c, idx, x[n0:n1]); np.add.at(cnt, idx, 1)
    c = c / np.maximum(cnt, 1)
    if OFF is None:
        d = np.diff(np.r_[c, c[0]]); OFF = (int(np.argmax(np.abs(d))) + 1) % NB      # first call must be the unwarped saw
    return np.roll(c, -OFF)
