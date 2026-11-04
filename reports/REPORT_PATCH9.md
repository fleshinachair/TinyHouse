# Patch 9: the faithful build (translate Serum's settings, calibrate only units, no loss veto)

**Rule change (from the user):** the parsed Serum values are ground truth. Every LFO route, Filter effect, Distortion and effect setting
is enabled unconditionally. The optimizer is limited to engine calibration (unit conversions, global) and each preset's loudness.
No preset can veto a feature. `deephouse2move.FAITHFUL = True` turns every per-preset fit off (they stay on disk).

## What was wrong with the gate, and what the data in the file already said
- Serum stores a **per-route bipolar flag** (`kParamBipolar`) that the converter ignored; macro folding ignored it too (now honoured).
- A route's curve (`flex`) is still ignored.
- The LFO curve **bend was the wrong way round**: Serum's stored bend is the segment's value at its midpoint (0.5 straight, near 1 a step
  up at once). Every custom LFO shape with a bend was wrong; Pluck - Selected's LFO (a step to 1, used as a constant level) became a
  flat zero and the preset was SILENT. Fixed.

## Measured on Serum itself (probes: calibration/probe_*.py)
- **LFO**: the default (unwritten) LFO is a sine at about 2 Hz; rates in Hz are exact (2/5/10 Hz measured 1.85/4.81/9.98). Depth on the
  oscillator volume: a unipolar route spans 0.5 of the knob per 100 % of amount, a bipolar one +-0.25. Cutoff, wavetable position, pitch
  and pan depths are one global constant each, found by playing every preset that has such a route against Serum as a GROUP
  (`calibrate_lfo_scales.py`): cutoff 0.5, position 1.0, pan 1.0, pitch 0.125.
- **Filter effect** (`probe_filterfx.py`): an unwritten type is a 6 dB/octave low-pass with its -3 dB point at the stated cutoff
  (8.18 Hz x 2697^x); the ladder models' stated cutoff is where they are ~12 dB down, their -3 dB point being 0.47x (24 dB), 0.53x (18 dB),
  0.67x (12 dB) of it. The mapping uses these.
- **Distortion** (`probe_dist.py`, `calibrate_dist.py`): the default mode is an odd-harmonic saturator; the clippers (SoftClip, HardClip,
  Overdrive) are clean until the drive passes a threshold; Asym and the diode modes make even harmonics from low drive. The unwritten
  drive is 25. `dist_scales.json` converts each mode's drive to the engine's by matching harmonics.

## Engine
- Per-route LFO polarity (`lr{r}_bip`); LFO level routes land on the volume KNOB before its taper (Serum's order); everything else as before.

## Results (the cost, measured): 70 presets against Serum's full chain
|  | on the Move before (fits + vetoes) | faithful |
|---|---:|---:|
| full-chain loss, fitted notes | 14.48 | 20.73 |
| full-chain loss, notes +5 st | 15.29 | 21.20 |
| 3-8 kHz band error, \|dB\| | 7.85 | 14.32 |
| >8 kHz band error, \|dB\| | 12.52 | 17.67 |
| RMS error, \|dB\| | 0.95 | 1.36 |
Better in 5 presets, worse in 61 (median loss +5.4). An ablation on the eight worst (Tonic Walter, Lynnic, BICEP, Black Out, Keys - Loop,
MOBY, Nu Aspect, Aquatiq) shows it is NOT the Filter effect (their loss is the same with it off): it is the per-preset voice-filter
fits that are gone. The closed-form translation of the voice filter and its envelope is off per patch (the Patch 4 finding: held-out
loss 25 -> 13 with the fits). The faithful way to remove that gap is to calibrate the voice filter's cutoff law and the envelope curves
by probe, like the filter effect above, not to fit each preset.
Three presets that take ALL their level from an LFO (Lead - Loop, Pluck - NOTSOBAD, Pluck - Selected) are 9-15 dB short of Serum's level
at maximum volume.
Bass - A Classic is kept exactly as approved.

## Not done
- Probes for the voice filter's cutoff law, resonance and envelope curves (the likely cause of the remaining gap).
- A route's response curve (`flex`), a second filter, sample oscillators, FM/PWM warps, Comp, real chorus/flanger/phaser.
