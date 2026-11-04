# Patch 8: LFOs, distortion stage, limiter cap, what is still missing

Module 1.11.0-th and the final preset set are BUILT AND CHECKED; installing is done by `./deploy_all.sh [host]` (presets first, then
the module, then the on-device load test and a checksum check). If the Move was unreachable when this was written, nothing from this
patch is on it yet. The set on the Move before this patch is in `calibration/p14_record/`; rollback copies are written to the Move by
the deploy script. Nothing is committed.

## Engine
- **LFOs, hidden** (no page): four LFOs and eight routes. An LFO has a rate (0.05-120 Hz, log), a mode (Trig restarts it on every
  note, Free runs on), and a 16-point shape. A route picks an LFO, one of the spare envelopes' destinations (oscillator level, wavetable
  position, tune, pan, filter cutoff, sub, noise) and a bipolar amount. `lfo_bip` picks whether an LFO swings 0..1 or -1..+1. They
  run at the block rate (2.9 ms). An oscillator whose level is only ever raised by an LFO still sounds. The LFO, shape and route
  parameters are left out of chain_params (they would double its size); static_asserts in voice.h hold the layout the voice walks through.
  Tests (native and ARM): a routed LFO swings a level 78x, one cycle is 0.203 s at the 0.200 asked for, Trig repeats the same swing
  for a note struck at any time and Free does not, Bi swings below the base.
- **Distortion**: the first stage of the FX block (before width, delay, reverb): Soft (tanh) / Asym (biased, even harmonics) / Hard
  curves, a drive, a wet mix, ramped so switching never steps; FX Enable takes it out. Hidden. The converter maps Serum's mode, drive
  and wet onto it for 20 presets.
- **Denormals** (earlier this session): FPCR.FZ per audio callback, `sanitize()` in fx.h; `-ffast-math` is not enabled.

## What the fits and checks decided
- **LFOs**: the converter maps each Serum LFO (shape from its curve points, rate, mode, routes) for 33 presets, but the shape of an
  unstored shape (sine), the rate of an unstored rate (2 Hz) and the polarity are assumptions about Serum. `lfo_pick.py` splices the
  settings onto the installed presets and plays six variants (unipolar/bipolar at full, half and quarter depth) against Serum's full
  chain on the fitted notes and on notes +5 st, judging the full-chain loss AND how close the spectrum's motion is to Serum's.
  Only 6 presets pass: Keys - Loop (bipolar, quarter depth), Lead - Oskar Med, Lead - Loop, Pluck - HotLap, Pad - Reiny, Pluck - Scorpios.
  The other 27 keep no LFO: with the mapping as it is, adding one did not bring them closer to Serum. The loss is nearly blind to motion
  (overall loss moved 14.73 -> 14.28 and 15.56 -> 15.39, noise level), so a better motion check would unlock more of them.
- **Distortion**: fitted on the installed presets (`post_fit_dist.py`) only Lead - Oskar Med and Lead - Space & Time gained; those two
  are not in the shipped set (the LFO step started from the previous set). The stage is in the module for later.
- **Limiter cap** (`post_level.py`): no more than 1.5 % of a preset's samples may reach the limiter's shoulder; 5 more presets were
  turned down (earlier pass: 12, including Bass - oskar med k 127 -> 111). The RMS-loss version of the cap was dropped: it compares two
  renders with different random unison phases and swings by more than the 0.5 dB it was meant to hold.
- Every preset now writes its eight LFO route sources as 0, because loading a preset only sets the keys it holds and a route must not
  outlive the preset that had it. Bass - A Classic differs from the version you approved only by those eight zero keys.

## What is missing, by number of presets (reports/MISSING_FEATURES.md)
Modulation effects (47 presets: Chorus 42, HyperDimension 41, Flanger 10, Phaser 6) are one width stage; measured in Serum, removing
them barely changes the spectrum's fine detail in 44 of 47, so they are mostly stereo wideners. Not carried: Comp 34, Filter FX that are
envelope/LFO-moved 5, second filters 15, sample/multisample oscillators 9, FM/PWM/filter warps 11, velocity routes, a second delay (9).
Duplicates of one effect are merged or dropped today (EQ into four bands, one delay, one reverb, one distortion).
