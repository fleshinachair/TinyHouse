#!/usr/bin/env python3
"""Open Serum 2's own editor window on a preset (blocks until the window is closed). Used by ui_shot.py.

  ui_child.py "Preset name"
"""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[2] / "converter"), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root, converter/ and calibration/ (this script lives in calibration/dev/)
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "converter")); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import fxmap
import test_render_calibration as H

name = sys.argv[1]
_, tree = H.load_preset(next(p.stem for p in H.PRESET_DIR.glob("*.SerumPreset") if fxmap.norm_name(H.d2m.ascii_name(p.stem)) == fxmap.norm_name(name)))
s = H.Serum()
fd, path = tempfile.mkstemp(suffix=".bin")
with os.fdopen(fd, "wb") as f:
    f.write(H.convert_preset_tree(tree))
s.synth.load_state(path)
os.unlink(path)
s.synth.open_editor()
