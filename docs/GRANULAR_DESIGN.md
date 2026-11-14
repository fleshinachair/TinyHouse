# Granular oscillator: engine design and probe plan (2026-11-13)

What Serum does is in `docs/GRANULAR_PROBE_NOTES.md`. This is how the engine should reproduce it, and what to measure before and while building it.
Rule as everywhere else: laws measured on Serum with controlled probes, no per-preset values.

## 1. Where it goes
A granular oscillator is a **mode of the existing sample oscillators** (smp, smp2, smp3), not a new oscillator:
- the converter exports the granular file as a sample set exactly as for a Sample oscillator (`sample_export.py`, one region, root key 60: Serum plays the file at its own pitch on C4);
- the slot keeps its level (same squared-pot law: one grain alone is exactly the Sample oscillator's level), tune, fine, pitch tracking, route, filter balance, buses and unison;
- new per-slot settings (x3), appended after the current params so the Move's first-256 key cache is not disturbed:
  `smpN_kind` (Sample / Granular), `smpN_gdens` and `smpN_glen` (Density and Grain Length as **Serum's knob position 0..1**, see below), `smpN_glmode` (Free / BPM), `smpN_gscan`
  (Scan Rate %), `smpN_gstart` (Start %), `smpN_gwin` (window shape) with `smpN_gwinp` and `smpN_gskew`, and the random amounts `smpN_groff`, `smpN_grpitch`, `smpN_grpan`,
  `smpN_grdir`, `smpN_grlen`, `smpN_grgain`. About 16 per slot, 48 in all.

**Knob units, not physical units, for Density and Grain Length.** These are the most modulated settings in the granular presets (50 mod routes between them). Serum's mod amounts
add in knob space, so the engine has to hold the knob position and apply Serum's curve (knob -> plain value -> N or T) itself, per block. The other settings can be physical if
the probe shows their curves are linear.

## 2. The DSP: `src/dsp/granular.h`, one `GrainCloud` per sample slot per voice
State per grain: read position (double), step (signed: pitch ratio * file rate / output rate, negative when the grain plays backwards), window phase and increment (1 / (T * sr)),
left/right gain (pan), amplitude, alive. A fixed pool of 64 grains per cloud (M is at most 29 from Density alone; unison streams share the pool).

Per block:
1. From the modulated knobs: T (s) and N; spacing D = T / N; M = ceil(N) and the measured gain factor `kGrainGain[M]` (a table in code, measured values only).
2. Scheduler: a countdown in samples to the next birth on the grid. At a birth, latch everything for that grain: T, pitch (plus random pitch), direction, pan, gain, window,
   and the start position = Start + scan position + random offset. The scan position moves at ScanRate % of real time, wrapping by the loop mode.
3. Render: **grain-major**. For each live grain, run its own loop over the block: position += step, linear interpolation of the int16 file (as `SamplePlayer` does), times
   the window from a 512-point table (rebuilt only when the shape, its parameter or the skew changes), added into the slot's left and right buffers. A grain that reaches its
   end stops early in the block. A grain born mid-block starts at its own sample offset.
4. Note-on: spawn the grains born on the grid before the note that would still be sounding, at their ages, following the measured pre-note rule (probe P3).
5. Randomness: one small PRNG (xorshift) per voice, seeded at note-on by the rule probe P5 finds (Serum renders identically twice with no Random settings; whether random
   settings differ between notes decides the seeding).

Why grain-major: the per-grain state stays in registers, there is no per-sample loop over grains, and the inner loop is the same shape as `SamplePlayer::stream`, which is already
measured on the Move.

## 3. CPU and memory (estimates, to be benchmarked before tuning anything)
- About 10-14 cycles per grain per sample on the Move's ARM cores (two loads, interpolation, window lookup, two multiply-adds).
- The heaviest presets have about 30 grains sounding per voice (Density 800 at one stream, or Density about 10 with 3 unison streams). 8 voices x 30 grains x 48 k x 12 cycles is about
  140 M cycles/s, roughly 9 % of one core at 1.5 GHz. That is affordable next to the current 11.5 % for a six-note chord, but it needs measuring with `tablor_cli` on the Move
  (worst case: Density 800, Grain Length 1 s, 8 notes).
- A safety cap: an engine-wide budget of live grains (192 to start). Past it, new grains in the busiest clouds are skipped and that cloud's gain rises by sqrt(M / M_kept), so
  the level holds and only the texture thins. It is a guard, not a fitted value; the benchmark decides whether it is needed.
- Memory: the granular files are ordinary sample sets (the factory ones are up to about 8 s stereo, 1.4 MB as int16).

## 4. Converter (`deephouse2move.py`)
kOsc_Granular -> the slot's sample set plus `smpN_kind = Granular` and the settings above. Carried: Density, Grain Length (Free; BPM through the measured grid and the host tempo),
Scan Rate, Start, the window, the random amounts and the mod routes to Density, Grain Length and Scan Rate. Each setting that is not yet measured is listed in the preset's notes.
A setting that is in use and changes the sound but is not carried (for example a warp on the grains, until P8) is a sound gap (crit), as now.

## 5. Tests
- Native DSP test with a click file, mirroring the Serum probe: grain starts every T / N, Hann pulse ratios, one-grain level = sample level, `kGrainGain[M]`.
- Then the 46 presets through the import render gate (levels and bands against Serum), as for the other oscillators.

## 5b. Status after the first probe round (2026-11-13, Sonnet)
P1, P2, P3 and P6 are done and recorded in `docs/GRANULAR_PROBE_NOTES.md`. What changed in the design:
- Density and Grain Length are held as knob positions k (0..1) exactly as planned: **N = 30 k_d**, **T = 10 k_l^4 s** (Free), plain Density = 1000 k_d^4. Mod routes add `amount/100` to k.
  The engine params `smpN_gdens` / `smpN_glen` are k; the converter converts the stored plain values: k_d = (D/1000)^(1/4), k_l = (T/10)^(1/4). Scan Rate and Start are linear, no knob law needed.
- `kGrainGain[M]` is a measured table for M = 1..31 (M = floor(N) + 1).
- Note-on: the grid starts at the note; pre-note grains exist only if their birth read position is inside the file (Scan 0 or a Start offset).
- Window: Tukey is the default shape (Param 100 = Hann); five shapes with closed forms and a skew warp, all independent of T.
Still open before those settings are complete: P4, P5, P7-P11 (BPM mode, randomness, positions/loops, unison, warps, level against pitch, stereo).

## 6. Probe plan (Sonnet), in the order the build needs them
- P1 **knob curves**: knob position -> plain value for Density, Grain Length (Free and BPM), Scan Rate, Start, Position and the Random settings. Cheapest route: read the
  plugin's parameter text for each knob position through DawDreamer, then confirm two points by rendering.
- P2 **gain table** for the M not yet measured (9, 11, 13, 14, 16, 18-20, 22, 23, 25-28); and whether, when Density is modulated, the factor follows the current M or each
  grain keeps its birth value.
- P3 **note-on**: which pre-note grains exist and sound, with the scan at 0 and moving (the scan-moving fade-in).
- P4 **position**: Start, Position with ManualPositionMode, ScanRange, the loop modes (Forward, PingPong, Tailed), reading past the end, negative scan.
- P5 **randomness**: Random Offset (seconds or % of file), Pitch (semitones; distribution), Pan (law), Direction (% backwards), Grain Length, Gain, Window; and whether two notes
  or two renders differ (the seeding).
- P6 **window shapes**: Tukey, ExpDec, BlackmanHarris, Triangle, Gaussian, with Window Param and Skew. Method: Density 0.001 (N 0.95, one grain at a time) and a click train
  file, so each grain draws its own window directly.
- P7 **unison** on a granular oscillator: detune, stack, span, the trig patterns (Random, Even, Exponential).
- P8 **warps on the grains** (11 presets): per grain or on the summed output (if the latter, the existing warp stage carries them).
- P9 **BPM mode**: the length grid over L 0..10 and its tempo dependence; DensityMode BPM.
- P10 **level against pitch**, measured by energy rather than pulse peaks (the pulse heights changed with pitch, probably the interpolator; must be confirmed).
- P11 stereo files and the pan law.

P1, P2, P3 and P6 are needed before the first engine build; the rest can follow one at a time with the converter carrying each as it lands.

## 8. Opus review of granular.h (2026-11-13) and the wiring plan for Sonnet
Review fixes, each with a test in `tests/test_granular.cpp` (all pass, `scripts/zig-build.sh native` builds `native_granular_test.exe`):
- the slot level is applied live (it was latched into each grain at birth, so a level route would have lagged a grain length);
- the scan position is integrated (it was rate x time since note-on, so a modulated Scan Rate jumped the read position);
- the grid is a phase, not a countdown (a Density raised from almost nothing waited out the old gap, seconds);
- the window table is no longer rebuilt at every note-on (about 1000 cos/exp per slot: a CPU spike on chords), and a modulated Window Param / Skew rebuilds per 0.25 step.
PC benchmark after the fixes: 8 voices x Density 800 (224 grains live) = 4.6 % of one core. **The Move figure is still needed** (build the arm test and run it there, or tablor_cli
on a granular preset) before deciding on the grain cap in section 3; no cap is in the code.
Kept as assumptions, each to be probed: a grain latches its pitch, length and M at birth (P2b, and a new **P12: pitch bend / vibrato / glide on live grains**); negative scan
from the file's end (P4); grains reading past the file's end stop (P4: Serum may loop or wrap the scan, which matters for notes held longer than file / scan speed).

**Wiring plan (decided; Sonnet builds it):**
1. Params, appended at the end in `tools/gen_params.py` (never inserted), per slot prefix `smp`, `smp2`, `smp3`, 8 each, 24 in all:
   `_kind` enum [Sample, Granular] 0; `_gdens` pot = 127 x Density knob, default 31.75 (knob 0.25); `_glen` pot = 127 x Length knob, default 63.5 (knob 0.5);
   `_gscan` num -200..200 default 100; `_gstart` num 0..100 default 0; `_gwin` enum [Tukey, ExpDec, Triangle, BlackmanHarris, Gaussian] 0 (the GrainWindow order);
   `_gwinp` num 0..100 default 100; `_gskew` num -100..100 default 0. Values may be fractional (presets are read with strtof and not rounded), so the converter writes
   127 x k to two decimals. Hidden from the Move's pages for now (not hand-turned).
2. Mod destinations, appended to `MOD_DST` in gen_params and to `enum ModDst` in voice.h in the same order: Smp1 Density, Smp1 Length, Smp1 Scan, Smp2 ..., Smp3 ...
   (9). The engine reads them as knob fractions: kd = gdens/127 + modOff[...DENS], kl = glen/127 + modOff[...GLEN] (each clamped 0..1), scan % = gscan + 400 x modOff[...SCAN].
   That is Serum's law: a route of a % adds a/100 to the knob (Scan: 4a % of speed).
3. Voice: a `GrainCloud` beside each `SamplePlayer` (gran, gran2, gran3). At note start, kind Granular starts the cloud instead of the player; release() both at voice end.
   In the render, kind Granular calls cloud.processAdding(gp, semis, knob*knob, sr, l, r, n) inside the same addSource (route, filter balance, buses) and with the same
   semis as the sample slot. Unison is not applied in granular mode until P7.
4. Converter (`deephouse2move.py`): kOsc_Granular -> export the GranularOsc file as the slot's sample set exactly as a Sample oscillator, kind = Granular,
   gdens = 127 (D/1000)^(1/4) (absent: 31.75; 0 stays 0 = silent, as Serum), glen = 127 (GL/10)^(1/4) in Free mode (absent: 63.5), gscan = kParamScanRate (absent 100),
   gstart = kParamStart (absent 0), window shape / param (absent 100) / skew. Routes: macro, wheel, velocity and note routes into Density / GrainLength / ScanRate / Start are
   folded into the knob statically (k += amount/100 x source value, as for the other knobs); LFO and spare-envelope routes go to the new destinations with the
   whole-range class (k += a). Sound gaps (crit) while not yet carried: any Random* above 0 (P5), unison above 1 (P7), a warp on the grains (P8), BPM length or density
   mode (P9), loop / position settings other than default (P4), stereo pan (P11).
5. Tests: test_granular as above, then the 46 granular presets through the render gate against Serum.

**Probe order after the wiring** (by how many presets each unlocks): P5 randomness first (Random Pan in 41 of 59 granular oscs, Random Length 40, Random Offset 30, Random
Direction 30, Random Pitch 22, Random Gain 15), then P4 (file end, loops, negative scan), P2b + P12 (what live grains do under modulation), P7 unison, P9 BPM, P8 warps,
P10 level against pitch, P11 stereo.

## 7. Model for each stage
- P1-P11 probes, the build, the converter and the tests: Sonnet.
- Opus: a review of `granular.h` before it goes on the Move, and again only if a probe overturns the model above (for example if the pre-note rule or the gain under modulation
  turns out not to fit it).

## 9. Status after the engine wiring (steps 1-3 of section 8 done, 2026-11-13)
Done and tested natively (all test exes pass; `scripts/zig-build.sh native`):
- `tools/gen_params.py`: bank "Granular" appended last (24 params, TB_P_SMP{,2,3}_KIND / _GDENS / _GLEN / _GSCAN / _GSTART / _GWIN / _GWINP / _GSKEW, params 435..458; short codes GRA*, GRB*, GRC*). Hidden from chain_params (regex), `params.h` regenerated. 9 mod destinations appended ("Smp1 Density" ... "Smp3 Scan").
- `voice.h`: `enum ModDst` extended to match; `gran / gran2 / gran3` GrainClouds; `startSamples()` starts the cloud or the SamplePlayer by kind, `releaseSamples()` releases all six; `addGrains` renders a granular slot (kd = knob + route, T from the length knob + route, scan % = pot + 400 x route).
- `tests/test_engine.cpp` `testGranularSlot`: Sample kind unchanged, Granular plays, Density 0 silent, envelope route to Density wakes it, level 0 silent, release ends.
Not done (resume here): step 4, the converter (`deephouse2move.py`: kOsc_Granular -> slot kind/gdens/glen/gscan/gstart/window; the MOD_DST name->index table near line 557 needs the 9 new names at indices 27..35; unsupported features as sound gaps), step 5 the 46-preset render gate. Unison is ignored in granular mode (P7). Nothing committed or deployed. The Move CPU is still unmeasured.

## 10. Status after the converter (step 4 done, 2026-11-14)
`deephouse2move.py` carries a Granular oscillator as a sample slot in granular mode (`granular_pots`, `sample_export.granular_osc`; `python test_granular_convert.py` checks the laws):
- the file exported as a one-region set (root = the file's baseNote, default C4), `kind = Granular`, Density / Grain Length as knob positions (127 k, stored 0 stays 0), Scan Rate,
  Start, window shape / param / skew; Pitch Track off -> fixed pitch. Macro, wheel, velocity and note routes into Density, Grain Length, Scan Rate and Start are folded in at their
  saved value (k += a x source; Scan 400 a %, Start 100 a %); LFO and spare-envelope routes use the 9 destinations (`ME_DST` 27..35); a bipolar envelope route moves the base by -a/2.
- Sound gaps (crit, listed per oscillator as "osc X (granular): ... not carried") until their probe: loop settings (P4, 37 of 58 presets), scan range / position / reverse (P4), tempo-synced
  scan / length / density (P4, P9), Random* above 0 (P5), warp on the grains (P8), unison above 1 (P7), pan >= 10 (P11), sample gain / trim, key / velocity zones, and any route into one of those
  or into Position / Start from an LFO or envelope.
- Run over the 58 granular presets in the Presets folder (scratch output): 43 carry a granular slot; none is free of gaps yet. Nothing was auditioned or put through the render gate (step 5).
- Assumptions to check: baseNote means the same as for a Sample oscillator; a route into Start adds 100 a % (linear); Pitch Track 0 means fixed pitch as for the sample slot.

## 11. Status after P4 and P5 (2026-11-14)
Measured (see the notes): the Random settings (P5) and the scan range, loops, manual position and Reverse (P4). Built: `granular.h` (scan from Start to End, negative rate from End, Forward / PingPong loops, manual position;
Random Direction / Gain / Pitch / Offset / Length / Pan drawn per grain by an unseeded xorshift), 36 more params appended (bank "Granular 2", `smpN_gloop` ... `gpan`, indices 459..494, contiguous per slot, hidden from the Move's pages),
converter (`granular_pots`: loop, End, manual position, Reverse, the five Random amounts, Random Pitch capped at 12; static routes into Start, Position and the Random amounts are folded). `test_granular.cpp` has the ramp-file tests.
Result over the 58 granular presets in the Presets folder: 43 carry a granular slot, **19 have no granular gap left, 18 of them no gap at all**. Still gaps: warp on the grains (11), ScanRange (7), unison (5), a route into Random Pitch (5), Random Start (4),
tempo-synced scan / length / density (6), Warp routes, pan, zones. The engine's grain count is unlimited by design; the PC bench varies 5-19 % of a core for 8 voices x Density 800 depending on the load of the machine: **the Move figure is still needed**.
Next: the render gate against Serum for the 18-19 clean presets (needs the user), the Move CPU, then P8 (warps on the grains), P7 (unison), P9 (BPM modes).
