# Serum 2 Granular oscillator: measured behaviour

Measured 2026-11-13 with `calibration/granwave.py` (one Granular oscillator straight to the output, note 60, velocity 100, host at 120 BPM). Nothing is built yet; the engine design is in
`docs/GRANULAR_DESIGN.md`.

## Test files
In Serum's `Samples/User/_tinyhouse_probe/` (outside the repo; delete the folder when the work is finished). All 44.1 kHz mono, 8 s:
- `tone1k.wav`: a steady 1 kHz sine. `noise.wav`: white noise. `ruler.wav`: 16 half-second tones, segment k at 300 * 2^(k/4) Hz (the tone heard says where in the file a grain reads).
- `click.wav`: silent but for +0.9 at 50 ms and -0.9 at 150 ms. With Scan Rate 0 every grain reads the file from the start, so each grain gives a + pulse 50 ms and a - pulse 150 ms after it starts
  (at pitch 1): the pulse times are the grain start times and the pulse heights are the window at those two ages. This is the probe that cracked the model.

## Where a preset stores it
`Oscillator{n}.plainParams` (kParamType = kOsc_Granular, Start, Position, ScanRate, Loop*, Unison*, Pitch, Octave, Fine, Volume, Pan ...) and `GranularOsc{n}.plainParams` (Density,
GrainLength, LengthMode, DensityMode, Random*, Window*, WarpMenu/Warp and WarpMenu2/Warp2) plus `samplePathRelative`. 53 granular oscillators in 46 presets. Mod-matrix routes to granular
settings in those presets: Density 26, GrainLength 24, ScanRate 15, RandomPan 7, RandomPitch 6, Warp/Warp2 10, Position 5, RandomDir 4, others 1 each.

## The model (measured)
- **Grain length**: Free mode, `kParamGrainLength` is the grain's length in seconds of output time (T). 0 is the shortest (a click file gives silence). Absent: T = 0.625 s.
  BPM mode: steps of musical lengths, at 120 BPM: L 0.25 -> 0.25 s, 0.5 -> 0.5 s, 1 -> 1 s, 1.5 -> 2 s, 2 and 2.5 -> 4 s, 3 and 4 -> 8 s (the grid and the tempo dependence are still to map).
- **Grain starts**: on a strict regular grid, every T / N seconds, where **N = 5.336 * Density^(1/4)** (`kParamDensity`, plain value). Within 0.1 % from Density 0.001 to 800
  (N 0.95 .. 28.4); independent of pitch and of the length mode. N is the mean number of grains sounding at once. Density 0 is silence. Absent Density: N = 7.50.
  A grain starts exactly at note-on and grains born before note-on are already sounding then (seen with Scan Rate 0; with the scan moving, the note fades in over about one grain
  length, so the pre-note grains are silent then: rule to confirm).
- **Window**: the default is Hann, sin^2(pi * age / T), in output time (pulse ratio w(150 ms)/w(50 ms) matches sin^2 for T = 0.25, 0.5, 0.75, 1, 2, 3 s).
- **Pitch**: each grain plays the file at the key's rate (C4 = the file's own pitch; note 48 reads at half speed, note 72 at double, Pitch +7 at 1.498). The grain grid and T do
  not change with pitch.
- **Scan Rate** (`kParamScanRate`, percent): the read position at a grain's birth moves through the file at Scan/100 times real time (default 100). 0 holds it at Start; 200 is
  double speed; negative runs backwards (-50 starts from the file's end).
- **Gain**: each grain is the file at the Sample oscillator's level (one grain at a time, N < 1, is exactly the Sample oscillator's level) times the window times a factor that
  depends only on **M = ceil(N)**, the most grains that can sound at once. Steps, not a curve: the same to 5 digits for N 1.13, 1.42, 1.69 (M = 2).
  Measured factor, M = 1..30 (P2, single-click file `click1.wav` so pulses of different grains never overlap; 5 digits stable): 1: 1.0000, 2: 0.5957, 3: 0.5730, 4: 0.5545, 5: 0.5388,
  6: 0.5249, 7: 0.5125, 8: 0.5012, 9: 0.4908, 10: 0.4812, 11: 0.4722, 12: 0.4638, 13: 0.4558, 14: 0.4483, 15: 0.4412, 16: 0.4344, 17: 0.4279, 18: 0.4217, 19: 0.4158, 20: 0.4100,
  21: 0.4045, 22: 0.3993, 23: 0.3942, 24: 0.3892, 25: 0.3844, 26: 0.3798, 27: 0.3753, 28: 0.3710, 29: 0.3668, 30: 0.3627 (and Density exactly 1000, N = 30.0: 0.3587, i.e. the step to M 31:
  M = floor(N) + 1 at an exact integer). A closed form was tried (power law, 1/(1+a ln M), 1/sqrt(a+bM)) and none fits: use the table. (The earlier two-sided click file gave a false
  0.4173 at M 20 and 0.52 at M 22: the negative pulse of one grain sat on the positive pulse of another when 100 ms was a multiple of the grain spacing.)
- **Summing**: grains add as they are, so with Scan 100 % and pitch 1 every grain reads the same place at the same time and they add coherently (a steady tone through the
  default settings is about 20 dB louder at note 60 than at notes 48 or 72, where the grains read different places and add incoherently). The engine must render the grains
  themselves; no shortcut reproduces this.
- **Determinism**: with no Random settings the same settings give the same audio twice.

## Knob curves and mod routes (P1, measured 2026-11-13)
Density and Grain Length are NOT host parameters (the plugin lists Scan Rate, Start, Position, Loop for each osc, nothing for the grain settings), so the curves were measured through a
Macro 1 route (source `[25, 0]`, macro at 100 %, `kParamAmount` in percent of the knob range, dest type `GranularOsc`, ids Density 6, GrainLength 7, RandomOffset 12, RandomDir 13,
RandomPitch 14, RandomGrainLength 15, RandomPan 16, RandomGain 17, WindowParam 9, DensityMode 23, Warp 0, Warp2 3; Oscillator ids Octave 3, Pitch 4, Fine 5, PitchRatio 7, Start 10,
ScanRate 13, Position 16, LoopEnd 18). Sources: macro 1..8 = 25..32, mod wheel 1.
- **Density**: knob k = (Density / 1000)^(1/4), **N = 30 * k** (so Density = 1000 * k^4, range 0..1000). A route adds `amount / 100` to k (checked from bases 0, 1 and 50 with +-10..40 %:
  N within 0.01). Density absent = 3.9 (k 0.25, N 7.5).
- **Grain Length (Free)**: k = (T / 10 s)^(1/4), **T = 10 * k^4 seconds** (k 0.2/0.3/0.4/0.5/0.6/0.7/0.8/0.9/1: 0.016, 0.081, 0.256, 0.625, 1.296, 2.401, 4.097, 6.562, 10.0). Absent = k 0.5.
- **Scan Rate**: knob 0..1 = -200..+200 % linear (host text); a route of `a` percent adds `4 * a` percent of scan speed (ruler file: a = 12.5 -> 50 %, 25 -> 100 %, 50 -> 200 %, -12.5 -> -50 %).
- **Start**: host text 0..100 % linear. Position, Loop Start: see the host text.
- **BPM mode length** (120 BPM, Density 1): the knob moves through a stepped menu, not a curve: knob 25 % -> 1/8 beat, 30-35 % -> 1/4 beat, 40 % -> 1/2, 45-50 % -> 1 beat, 55 % -> 2,
  60-65 % -> 4, 70 % -> 8, 75 % -> 16 beats (T = 0.0625 .. 8 s); 80-100 % longer than the 9 s window used (P9 maps the rest and the tempo dependence).

## Note-on (P3, measured 2026-11-13)
The grid starts at note-on (a grain is born exactly at t = 0, then every T / N). Grains of the grid born BEFORE the note (births at -j * T / N) exist only if their birth read position
(Start + Scan * birth time, birth time negative) is inside the file. Scan 0 or Start large enough: the full pre-roll is there at t = 0, steady level at once (tone, Start 25 %, Scan 100 %:
envelope flat from 10 ms). Start 0 with Scan 100 %: no pre-roll, the envelope is the sum of the windows of grains born from t = 0 on (a ramp over about one grain length; matches
to 4 digits). Start 10 % (0.8 s): grains born earlier than -0.8 s are dropped (level at 10 ms 1.6 % low, the model says 1.0 %). Negative scan: pre-note births lie beyond the end,
so (by the same rule) none. Not yet tested: loop modes (P4) that wrap the position.

## Window shapes (P6, measured 2026-11-13)
Probe: Density 0.001 (N 0.95, one grain at a time), steady 1 kHz file, Scan 0, the smoothed |output| over the grain is the window. x = age / T in 0..1. Param (`kParamWindowParam`,
default **100**, range 0..100, 0 is a rectangle for every shape) and Skew (`kParamWindowSkew`, -100..100, default 0). Shape names `kWindowTukey` (the **default shape**), `kWindowExpDec`,
`kWindowTriangle`, `kWindowBlackmanHarris`, `kWindowGaussian`. In the factory presets: Tukey 5, ExpDec 3, Triangle 2, BlackmanHarris 1, Gaussian 1; the rest use the default.
With a = Param / 100 and u = the skewed position:
- **Tukey**: flat in the middle, raised-cosine tapers, taper fraction a (a = 1 is the Hann window the default gives): w = 0.5 (1 - cos(pi * u / (a / 2))) for u < a/2, 1 in the
  middle, mirrored at the end (checked a = 0.25, 0.5, 0.75).
- **Triangle**: w = (1 - a) + a * tri(u), tri(u) = 1 - |2u - 1| (a pedestal under the triangle, a = 1 pure triangle).
- **BlackmanHarris**: w = (1 - a) + a * BH(u), BH = 0.35875 - 0.48829 cos(2 pi u) + 0.14128 cos(4 pi u) - 0.01168 cos(6 pi u).
- **Gaussian**: w = exp(-(u - 0.5)^2 / (2 s^2)), s^2 = 0.05 / Param (Param in percent; s = 0.0707 at 10, 0.0316 at 50, 0.0224 at 100; spread < 0.001); Param 0 a rectangle.
- **ExpDec**: w = exp(-k * x), **k = (Param / 10)^2** (Param 10 -> 1, 20 -> 4, 50 -> 25, 100 -> 100; fits to 0.1 %). Starts at 1 at the grain start. Skew has no effect on it.
- **Skew** (all shapes except ExpDec) is a piecewise-linear time warp that puts the window's centre at c = 0.5 + Skew / 200 (-100: at the start, +100: at the end, -50: 0.25):
  u = 0.5 x / c for x < c, u = 0.5 + 0.5 (x - c) / (1 - c) for x > c (checked on Triangle and Tukey at +-50, BH and Gaussian at +-100).
- Independent of grain length (same shape at T = 0.5, 1, 2). The window is applied in output time as for the default.
- Peak is 1.0 for every shape (the grain gain table above is unchanged).

## Still to measure
See the probe plan in `docs/GRANULAR_DESIGN.md`.

## Random settings (P5, measured 2026-11-14, `calibration/probe_granular_random.py`)
Method: one grain at a time (Density 0.001), Scan 0, a rectangular window (Tukey Param 0) where the grain's extent is read, a click or a linear chirp (`chirp.wav`, 300 -> 3000 Hz over 8 s) where its read position is read.
Every Random value is a percent (0..100), the amount v = Random / 100. The grain's draw is uniform, taken at its birth.
- **Not seeded**: two renders of the same settings differ (pan sequences differ), and so do two notes. No seed rule is needed; any PRNG will do.
- **Random Direction**: each grain is reversed with probability v (0.25 / 0.54 / 1.00 at 25 / 50 / 100). A reversed grain starts at the same read position as a forward one and runs backwards from it
  (chirp: forward grains' mean frequency is the file's at Start + T/2, reversed ones' the file's at Start - T/2).
- **Random Gain**: amplitude x (1 - v u), u uniform 0..1 (rms/ref spreads over 0.75..1 at 25, 0.5..1 at 50, 0.1..1 at 100): it only turns grains down.
- **Random Pitch**: semitones, uniform in +-min(v x 100, 12) (v at 2 / 6 / 12 gives +-2 / +-6 / +-12 st, 24 is capped at +-12). A small positive bias of about 0.9 st was seen at 2 and 6 (cause not found; not modelled).
- **Random Offset**: the grain's read start moves by +-0.15 x (the file's length) x v^4, uniform (v = 1: +-1.18 s on an 8 s file, +-0.58 s on 4 s; v = 0.75: +-0.37 s; v = 0.5: +-0.07 s; v = 0.25: +-0.005 s), independent of the grain length.
- **Random Grain Length**: the length becomes T (1 + r v^4), r uniform in -1..1 (v = 1: lengths from about 0 to 2 T, v = 0.5: +-6 %, v = 0.25: +-0.4 %). The grid keeps the nominal spacing.
- **Random Pan**: a pan x drawn uniform in +-g(v)... the measured extremes of x = (R - L) / (R + L) are 0.083 / 0.208 / 0.43 / 0.685 / 0.99 at v = 0.1 / 0.25 / 0.5 / 0.75 / 1, fitted by g(p) = p (0.83 + 0.16 p^2); the grain's gains
  are L = (1 - x) c and R = (1 + x) c with c = 1 / sqrt(1 + 0.462 x^2), relative to the centre's L = R = 1 (mono file; fit within 0.5 %). It is not constant power: at the extreme one side is 1.65 and the other 0.
  Stereo files and the pan of a stereo grain: not yet measured (P11).
- Not measured: Random Window Amount / Skew and Random Warp (4 oscillators each), Random Start on the oscillator (4).

## Scan range, loops, manual position (P4, measured 2026-11-14, `chirp.wav`: f = 300 + 337.5 t Hz over 8 s, one rectangular-window grain at a time, the grain's frequency is where it reads)
Positions in percent of the file; the scan position p(t) = Start + Scan x t (t since note-on, signed).
- No loop (mode absent, Off, Tailed): grains exist while p < End (End default 100 %, the file end); past it there are none. A negative rate starts at End and runs down to Start, where the grains stop.
  Tailed behaved as Off up to the file end; two odd grains at the very end were seen (not understood).
- **Forward loop** (LoopStart / LoopEnd, 25..50 % = 2..4 s): from a Start below the loop end the position runs on to the loop end and wraps to the loop start (p = ls + (p - ls) mod (le - ls));
  before the loop end it is plain. **PingPong**: at the loop end it reverses to the loop start and goes forward again (triangle). With a negative rate: starts at End, runs down, and once below the loop end
  keeps going to the loop start, where it wraps to the loop end (Forward: 1.99 -> 3.99) or reflects (PingPong); the Start bound does not apply inside a loop.
- A **Start at or beyond the loop end**: no loop at all, the scan plays straight to the file end. LoopEnd absent = 100 % (the loop then wraps at the file end: Start 60 %, LoopStart 25 %, scan 200 -> wraps to 2.38 s at p = 8.38).
- **Reverse** (osc `kParamReverse` = 1): the same as a negative scan rate (starts at End, runs down to Start).
- **Manual Position** (`kParamManualPositionMode` = 1, `kParamPosition` percent): every grain reads at Position; the scan and Start are ignored (Position 50 % = 4.0 s with Start 25 % and Scan 100 %).
- **ScanRange** = 1 (8 oscillators): with End 50 %, Start 12.5 % the scan moved at twice the speed (1.0 -> 1.84 in 0.42 s). Meaning unknown, not carried.
- Not measured: Tailed past the end, Loop Crossfade, LoopStartLink, Random Start (oscillator), the tempo-synced scan (`kParamScanBPM*`, `kParamBaseTempo`, `kParamScanTempoLock`).

## Scan range, tempo, BPM modes, unison, warp (measured 2026-11-14 with `chirp.wav` / `click1.wav`; DawDreamer `set_bpm` for the tempo)
- **ScanRange** 1: the scan runs at exactly twice the speed at every rate (rate 10 / 25 / 50 / 100 / 200 -> 0.2 / 0.5 / 1 / 2 / 4 file-s/s), a negative rate likewise. Engine: `smpN_gscx`.
- **Tempo lock** (`kParamScanTempoLock` 1, `kParamBaseTempo`): speed = rate x host BPM / base tempo (base 60 / 138 / 240 at 120 BPM: 2 / 0.87 / 0.5; at 60 BPM and base 120: 0.5). Engine: `gscm` 1 + `gbase`.
- **Tempo sync** (`kParamScanBPMDivide` > 0 switches it on; the value, 1 / 2 / 3, changes nothing; without it `kParamScanBPMRate` does nothing): the scan covers a fixed fraction of the file per second,
  0.0625 x 2^rate x BPM / 120 (rate 0..4 measured; an 8 s and a 4 s file give the same fraction, so it is file-length independent: 1 bar per file at rate 3, 120 BPM, 4 s). The stored Scan Rate is ignored (its sign, from Reverse, is kept).
  Engine: `gscm` 2 + `gsbr`. `808 - Texture` stores BPMRate 0 without Divide: no tempo scan there (the old gap was a false alarm).
- **LengthMode BPM**: the grain length snaps to 1/128 s x 2^i at 120 BPM (i = 0..13, to 64 s) and scales as 120 / BPM. The knob k = (L/10)^0.25 steps 14 times: i = floor(14 k - 0.158) (boundaries measured at k = 0.2256 + n/14, +/-0.003).
- **DensityMode BPM**: the births per second are 2^m at 120 BPM, whatever the grain length, scaled by BPM / 120; m = floor(14 k - 3.82) - 2 (k = Density knob), from 1/16 s at k ~ 0.2 to 256 /s at k = 1 (boundaries k = 0.273 + n/14).
  The overlap is then births x length (the gain table follows it). Engine: `glenm`, `gdenm`.
- **Unison** (7 oscillators): every grain is born once per voice (the same grid, each voice its own Random draws is assumed), voices spread evenly across the Detune: total span 4 d^1.25 semitones for Detune d (0.25 -> 0.71 st, 0.5 -> 1.68, 1 -> 3.96), pan like the sample oscillator
  (the first voice left, alternating, the middle one of an odd stack in the centre; UnisonStereo 100 % = hard, 50 % = pan 0.59, 25 % = 0.31), level 1/sqrt(n) per voice. Detune Width does nothing. The engine reuses `smpN_uni/det/spread`; UnisonWarp and UnisonStack are not carried (gap).
- **Warp on the grains** (probes `calibration/probe_granular_warp.py`): a Dist* warp is applied to the file's raw samples inside each grain, before the window and the grain gain (the gain it adds does not change with the overlap
  of 1..100 grains but does with the file's own level: SoftSat at Warp 1: +13.4 dB for a 0.2-rms noise file, +8.6 dB at 0.33). Not built yet: needs the shaper law in absolute file units (probe the transfer curve with files of known level).
