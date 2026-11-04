# Patch 4: waveform and spectral autopsy, per-preset filter and width fits

Status: converted and validated; **not yet installed on the Move** (it was unreachable: `move.local`
did not resolve and 192.168.1.169, 172.16.254.1 and 192.168.1.150 timed out or refused). Install with
`./deploy_presets.sh [host]` once it is back. No engine change, so `dsp.so` (1.6.0-dh) is untouched and
nothing needed rebuilding.

## 1. Autopsy of the two anchors (`calibration/autopsy.py`)

Serum free-runs its unison phases, so nothing compares samples: envelopes, averaged spectra and stereo
statistics only, against cached Serum renders. Slices come from Serum's own envelope, because the bass
is gone in 0.26 s and the keys in 0.42 s. Numbers below are **oscillators only** (FX stripped on both
sides) unless marked full chain. Raw numbers: `autopsy_before.json`, `autopsy_after.json`.

**Envelope / transient**
- Bass: 10-90 % attack rise 1 ms on both. Tablor's peak lands 4 ms later (6 vs 2 ms), 4 dB lower in the
  raw first 40 ms (0.69 vs 1.10), crest 5.2 vs 6.4 dB, largest sample step 0.155 vs 0.166. No harsh attack.
  Decay curve within 4 dB to 200 ms. Serum then drops to digital silence at about 260 ms; Tablor keeps a
  tail (-39 dB at 300 ms). Inaudible, but a different shape.
- Keys: attack rise 24 ms vs Serum's 17 ms. Decay within 2 dB to 150 ms, then Tablor is 9 dB lower at 200 ms
  and 14.5 dB higher at 400 ms. The largest first-40 ms sample step is 0.169 vs 0.061: Tablor's edges are
  about 2.8x sharper, consistent with its excess top end.

**Harmonic profile, body slice (dB re Serum's total)**

| Bass | Serum | Tablor before | after fit |
|---|---|---|---|
| 100-300 Hz | -5.7 | -8.2 | -6.8 |
| 300-1 k | -15.7 | -11.1 | -20.5 |
| 1-3 k | -54.1 | **-28.4** | -59.0 |
| 3-8 k | -94.8 | -63.0 | -71.2 |

Warmth at 100-300 Hz was not missing; the excess was above 300 Hz (+25.7 dB at 1-3 kHz). The fit overshoots
dark by about 5 dB at 300 Hz-3 kHz. Keys: 1-3 kHz is +5.5 dB hot and the fit does not change it (+6.5).

**Stereo (L/R correlation, side/mid)**
- Bass oscillators: 0.398 / -4.0 dB Serum, 0.390 / -4.3 dB Tablor. The side channel is **not** overpowering.
  Keys: -0.02 / 0.0 vs +0.07 / -0.6.
- Bass full chain: Serum 0.92 / -13.6 dB, Tablor 0.46 / -4.7 dB. Serum's SplitMS/Utils block makes the
  lows near-mono (0.93-0.99 below 300 Hz); that block is not carried. This is the real stereo gap on the
  bass anchor and no panning law can fix it.
- All 70 presets, dry (`stereo_survey.py`): median side/mid error -0.08 dB. Pooled error by stack: 2-3 voices
  +3.7 dB, 4-5 +2.2, 6-9 +0.9 (run-to-run noise is 1-2 dB). Individual presets run from -14.5 to +10.2 dB
  and not in step with voice count, so the panning law in `wt_oscillator.h` was **not changed**.

**Sub-bass (Bass - A Classic, 65.4 Hz)**: no cancellation (L/R coherence 0.67 vs Serum 0.57), no asymmetric
clipping (peak asymmetry 1.6 vs 1.4 dB, 0.4 % of samples over the clipper knee, RMS -0.01 dB). The
fundamental is **6.5 dB light** (-24.0 vs -17.5 dB) and the 2nd harmonic sits 2.1 dB above it (Serum
-5.0). ~~Filter resonance accounts for about 4 dB of it~~ **Corrected in Patch 5**: that was a 3-render fluke. With 16 renders per setting over the same window the gap is about 3.5 dB (Serum's own three renders span 5 dB) and resonance between 0 and 87 moves it by about 1.4 dB, inside the noise. Only resonance 127 costs level.

**Headroom, all 70 presets** (`calibration/headroom_all.json`): the output soft limiter takes at most
0.20 dB off any preset's RMS (Keys - Lynnic); none loses more than 0.5 dB. Nothing to fix.

## 2. A bug in my own Patch 3 code, found by this

`fxmap.map_reverb` and `map_wide` read the name-keyed Serum fingerprint without checking the preset's
rack held a reverb or width effect. Shipped conversions were unaffected (racks and fingerprints agree), but
every "FX stripped" Tablor render used for comparison still had the reverb and chorus on: the stereo survey
showed Tablor wide where Serum was mono. Fixed (`fxmap.map_fx` drops a fingerprint whose effect is absent);
the 70 regenerated presets differ from the previous output only in intended keys. Everything in section 1
was re-measured after the fix. The first autopsy and a first set of filter fits were discarded.

A second one, in Patch 3's naming: the converter named each output from the Serum header and fell back to the
file name only on a collision. `Pad - Nu Aspect 2.SerumPreset` is headed `Pad - Nu Aspect` and sorts first, so
the pair's names were swapped, and each looked up the other's FX fingerprint (and, now, fit). Outputs are now
named by file stem, which is also what the calibration files are keyed by; it changes only that pair (the
header matches the stem on the other 69). On the Move, the contents of `Pad - Nu Aspect` and
`Pad - Nu Aspect 2` therefore swap relative to Patch 3, and both now use their own FX measurements.

## 3. Fits

**Rejected: one global fit** (`fit_perceptual.py`, 3 pooled constants, loss = third-octave spectrum in 3 time
slices + 5 ms envelope + side/mid). Train 26.96 -> 25.32, **validation 26.71 -> 29.10** (worse), single
presets 10 points worse. The filter envelope error is per patch, not a scale.

**Kept: per preset** (the way the FX were done), each against its cached Serum render:
- `fit_filter.py`: cutoff, envelope depth, envelope decay. 65 fitted, 5 skipped (filter open and
  unmodulated), **42 kept** (loss cut at least 12 %). Median loss over kept 24.9 -> 12.5.
- `fit_width.py`: one scale on the unison spread. 13 kept (6 were also filter-fitted). Narrowing only.
  Where Serum uses a sample oscillator (NOTSOBAD, Selected) the fit matches the overall image, it does
  not fix a unison error.
- The converter applies a fit only if the mapper still produces the values it was fitted from
  (`deephouse2move.apply_filter_fit`, `apply_width_fit`); otherwise it says so in the report.

**Held-out check** (`check_fits.py`: Serum re-rendered on notes 5 semitones away, new renders, 4 Tablor
renders each):

| | before | after | better / worse |
|---|---|---|---|
| filter, loss (n=42) | 25.1 | 13.5 | 42 / 0 |
| filter, spectrum dB | 18.9 | 8.6 | 41 / 1 |
| filter, centroid st (not in the loss) | 5.7 | 3.2 | **20 / 20** |
| width, side/mid error dB (n=13) | 4.9 | 1.5 | 11 / 1 |

Against the Patch 3 presets, 49 of 70 differ: 43 in a filter knob, 15 in a spread knob (13 fits plus the
swapped pair), 23 in `volume`, and the swapped pair's contents. Nothing else changed.

**Full chain, 14 validation presets** (`--stage fx`, FX on), Patch 3 -> now: band error 14.6 -> 11.4 dB,
tail brightness 9.5 -> 7.0 st, decay-time error 0.94 -> 0.82 log2; held-note RMS 2.7 -> 2.7 dB,
tail level 4.7 -> 5.1 dB, side/mid 3.5 -> 4.0 dB (slightly worse).

## 4. Open

- Mono-bass crossover is the missing piece for the bass stereo (SplitMS/Utils, 11+ presets).
- Bass fundamental 6.5 dB light; adding resonance to the per-preset fit is the obvious next step.
- Keys top end (+5 to +9 dB at 1-8 kHz) is not from the filter or the noise oscillator (noise off: unchanged).
- Distortion (36 presets), compressor (34), filter FX (27) still not carried.
- Not listened to. The fits are matched to Serum's measurements, not to anyone's ear.
- The clip guard's `volume` is noisy run to run (random unison phases): 21 of 70 preset volumes moved,
  partly because the fitted filters change the peaks it measures.
