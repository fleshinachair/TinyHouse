"""Checks for the Granular oscillator half of the converter (no Serum needed): python test_granular_convert.py

  * deephouse2move.ME_DST agrees with the engine's destination list in schwung-tablor/tools/gen_params.py (the preset stores the INDEX);
  * granular_pots turns Serum's stored settings into the engine's knob positions (docs/GRANULAR_PROBE_NOTES.md laws) and flags what is not carried.
"""
import re
import sys
from collections import Counter
from pathlib import Path

import deephouse2move as D

fails = 0


def check(name, ok, detail=""):
    global fails
    print(("ok   " if ok else "FAIL ") + name + (("  " + detail) if detail and not ok else ""))
    fails += 0 if ok else 1


# 1. the destination table
src = (Path(__file__).parent / "schwung-tablor/tools/gen_params.py").read_text(encoding="utf-8")
block = src[src.index("MOD_DST = ["):]
block = block[:block.index("]")]
names = re.findall(r'"([^"]+)"', re.sub(r"#[^\n]*", "", block))
check("ME_DST matches gen_params MOD_DST", all(names[i] == n for n, i in D.ME_DST.items()) and all(n in names for n in D.ME_DST), "")
check("ME_DST has the 9 granular names at 27..35", [D.ME_DST[f"Smp{s} {w}"] for s in (1, 2, 3) for w in ("Density", "Length", "Scan")] == list(range(27, 36)))

# 2. the knob laws
nofold = Counter()
d = {"osc": {}, "gran": {}, "node": {}}
p, gaps, _ = D.granular_pots("smp", d, 1, 1, nofold)
check("defaults: Density knob 0.25, Length knob 0.5, Scan 100, Start 0", p["smp_gdens"] == 31.75 and p["smp_glen"] == 63.5 and p["smp_gscan"] == 100.0 and p["smp_gstart"] == 0.0 and p["smp_kind"] == 1, str(p))
check("defaults: no gaps", gaps == [], str(gaps))

d = {"osc": {"kParamScanRate": -40.0, "kParamStart": 25.0}, "gran": {"kParamDensity": 62.5, "kParamGrainLength": 0.625, "kParamWindowShape": "kWindowGaussian", "kParamWindowParam": 40.0, "kParamWindowSkew": -20.0}, "node": {}}
p, gaps, _ = D.granular_pots("smp2", d, 0, 1, nofold)
check("Density 62.5 = knob 0.5 (1000 k^4)", abs(p["smp2_gdens"] - 63.5) < 0.01, str(p["smp2_gdens"]))
check("Length 0.625 s = knob 0.5 (10 k^4)", abs(p["smp2_glen"] - 63.5) < 0.01, str(p["smp2_glen"]))
check("scan, start, window carried", p["smp2_gscan"] == -40.0 and p["smp2_gstart"] == 25.0 and p["smp2_gwin"] == 4 and p["smp2_gwinp"] == 40.0 and p["smp2_gskew"] == -20.0, str(p))

d = {"osc": {}, "gran": {"kParamDensity": 0.0}, "node": {}}
p, _, _ = D.granular_pots("smp", d, 0, 1, nofold)
check("a stored Density 0 stays 0 (silent)", p["smp_gdens"] == 0.0)

fold = Counter({("GranularOsc", "kParamDensity", 2): 0.1, ("GranularOsc", "kParamGrainLength", 2): -0.1, ("Oscillator", "kParamScanRate", 2): 0.25, ("Oscillator", "kParamStart", 2): 0.1})
d = {"osc": {}, "gran": {}, "node": {}}
p, _, _ = D.granular_pots("smp3", d, 2, 1, fold)
check("a folded route adds to the knob (Density +10 %, Length -10 %), Scan +400 x, Start +100 x",
      abs(p["smp3_gdens"] - 127 * 0.35) < 0.01 and abs(p["smp3_glen"] - 127 * 0.4) < 0.01 and p["smp3_gscan"] == 200.0 and abs(p["smp3_gstart"] - 10.0) < 1e-6, str(p))

# 3. what is flagged
d = {"osc": {"kParamLoopMode": "kForward", "kParamPan": 40.0, "kParamPitchTrack": 0.0}, "gran": {"kParamRandomPan": 30.0, "kParamRandomGain": 0.0, "kParamWarpMenu": "kFM_OSC", "kParamLengthMode": "kLengthBPM"}, "node": {"gain": 3.0}}
p, gaps, _ = D.granular_pots("smp", d, 0, 3, nofold)
g = " | ".join(gaps)
check("flags loop, BPM length, Random Pan (not Random Gain at 0), warp, unison, pan, gain", all(s in g for s in ("loop", "grain length", "Random Pan", "warp", "unison 3", "pan", "gain")) and "Random Gain" not in g, g)
check("pitch track off -> fixed pitch", p.get("smp_fixed") == 1)

print("\n%d failure(s)" % fails)
sys.exit(1 if fails else 0)
