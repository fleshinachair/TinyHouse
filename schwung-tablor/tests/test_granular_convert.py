"""Checks for the Granular oscillator half of the converter (no Serum needed): python schwung-tablor/tests/test_granular_convert.py

  * deephouse2move.ME_DST agrees with the engine's destination list in schwung-tablor/tools/gen_params.py (the preset stores the INDEX);
  * granular_pots turns Serum's stored settings into the engine's knob positions (docs/GRANULAR_PROBE_NOTES.md laws) and flags what is not carried.
"""
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "converter"))
import deephouse2move as D

fails = 0


def check(name, ok, detail=""):
    global fails
    print(("ok   " if ok else "FAIL ") + name + (("  " + detail) if detail and not ok else ""))
    fails += 0 if ok else 1


# 1. the destination table
src = (Path(__file__).resolve().parents[1] / "tools/gen_params.py").read_text(encoding="utf-8")
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

# 3. loops, scan range, manual position, Random settings
d = {"osc": {"kParamLoopMode": "kPingPong", "kParamLoopStart": 25.0, "kParamLoopEnd": 50.0, "kParamEnd": 80.0, "kParamReverse": 1.0}, "gran": {"kParamRandomPan": 30.0, "kParamRandomDir": 50.0, "kParamRandomGain": 20.0,
     "kParamRandomOffset": 70.0, "kParamRandomGrainLength": 10.0, "kParamRandomPitch": 24.0}, "node": {}}
p, gaps, _ = D.granular_pots("smp", d, 0, 1, nofold)
check("PingPong loop 25..50, End 80, Reverse (scan sign flips)", p["smp_gloop"] == 2 and p["smp_gloops"] == 25.0 and p["smp_gloope"] == 50.0 and p["smp_gend"] == 80.0 and p["smp_gscan"] == -100.0, str(p))
check("Random Pan / Dir / Gain / Offset / Length carried as stored, Pitch capped at 12", p["smp_grpan"] == 30.0 and p["smp_grdir"] == 50.0 and p["smp_grgain"] == 20.0 and p["smp_groff"] == 70.0 and p["smp_grlen"] == 10.0 and p["smp_grpitch"] == 12.0, str(p))
check("... and none of that is a gap", gaps == [], str(gaps))
p, _, _ = D.granular_pots("smp", {"osc": {}, "gran": {}, "node": {}}, 0, 1, nofold)
check("an untouched oscillator writes no loop / random keys", not any(k.startswith(("smp_gloop", "smp_gr", "smp_gend", "smp_gman", "smp_gpos")) for k in p), str(p))
d = {"osc": {"kParamManualPositionMode": 1.0, "kParamPosition": 40.0}, "gran": {}, "node": {}}
p, _, _ = D.granular_pots("smp", d, 3, 1, Counter({("Oscillator", "kParamPosition", 3): 0.1, ("GranularOsc", "kParamRandomPan", 3): 0.25}))
check("manual position 40 + a folded route of 10 %; a folded route adds to Random Pan", p["smp_gman"] == 1 and abs(p["smp_gpos"] - 50.0) < 1e-6 and p["smp_grpan"] == 25.0, str(p))

# 4. what is still flagged
d = {"osc": {"kParamScanRange": 1.0, "kParamPan": 40.0, "kParamPitchTrack": 0.0, "kParamScanBPMRate": 2.0, "kParamScanBPMDivide": 1.0}, "gran": {"kParamRandomWindowSkew": 30.0, "kParamWarpMenu": "kFM_OSC", "kParamLengthMode": "kLengthBPM"}, "node": {"gain": 3.0}}
p, gaps, _ = D.granular_pots("smp", d, 0, 3, nofold)
g = " | ".join(gaps)
check("pitch track off -> fixed pitch", p.get("smp_fixed") == 1)
check("flags Random Window Skew, warp, gain (pan is carried)", all(s in g for s in ("WindowSkew", "warp", "gain")) and "pan" not in g, g)
check("... and no longer scan range, tempo scan, BPM length, unison", not any(s in g for s in ("scan range", "tempo", "grain length", "unison")), g)
check("ScanRange 1, Divide + BPM rate 2, BPM length carried", p.get("smp_gscx") == 1 and p.get("smp_gscm") == 2 and p.get("smp_gsbr") == 2 and p.get("smp_glenm") == 1 and p.get("smp_gscan") == 100.0, str(p))
d = {"osc": {"kParamScanTempoLock": 1.0, "kParamBaseTempo": 138.0, "kParamScanRate": -76.9}, "gran": {"kParamDensityMode": "kDensityBPM"}, "node": {}}
p, gaps, _ = D.granular_pots("smp3", d, 2, 1, nofold)
check("Tempo lock at base 138, BPM density", p.get("smp3_gscm") == 1 and p.get("smp3_gbase") == 138.0 and p.get("smp3_gdenm") == 1 and p["smp3_gscan"] == -76.9 and not gaps, str(p) + str(gaps))
d = {"osc": {"kParamUnison": 2.0, "kParamUnisonWarp": 37.5}, "gran": {}, "node": {}}
p, gaps, _ = D.granular_pots("smp", d, 0, 2, nofold)
check("unison warp is still a gap, plain unison is not", any("UnisonWarp" in x for x in gaps) and len(gaps) == 1, str(gaps))

print("\n%d failure(s)" % fails)
sys.exit(1 if fails else 0)
