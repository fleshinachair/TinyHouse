# Overnight work, 2026-11-04 (staged, NOT installed on the Move)

Staged set: `calibration/g15_final` (= `g54_final`), copied to `out/move/data/UserData/schwung/presets/tinyhouse/` (70 presets).
Module: `schwung-tablor/build/dsp.so`, md5 `24066037809d8edd40f914d37e4289fc`. Previous staged set: `calibration/g15_before_dist`.
Install when ready: `BANK="Deep House" sh ./deploy_all.sh move.local` (back up the live dsp.so first; the deploy runs the on-device load test).

## Numbers (chain report, 70 presets, against Serum's full chain)
| | before | after |
|---|---|---|
| mean full-chain loss, notes as fitted | 15.35 | 14.08 (38 better, 20 worse) |
| mean loss, notes +5 semitones (never fitted) | 16.00 | 14.65 |
| level error, mean abs | 1.15 dB | 0.89 dB |
| 1-3 kHz band error, mean abs | 4.53 dB | 3.93 dB |
Per-preset loss moves by +-4 between runs of the same state (random unison phases), so the stage audits below are the better evidence.
Stage audits (Serum with one stage vs the engine with one stage, on the preset's own note): distortion level error 5.8 -> 1.5 dB, compressor level error 2.9 -> 1.8 dB.

## Biggest per-preset gains (loss before -> after)
Lead - Ari 26.8 -> 12; Lead - Self Control Noise 23.6 -> 12; Bass - The Selected 21 -> 11; Lead - I'll Be Yours 16 -> 7.6; Lead - Can't Find You 13.8 -> 6; Keys - Other Side 18 -> 11;
Keys - Billboard 17 -> 10.5; Keys - Loop 26 -> 21; Keys - Can't Find You 12.6 -> 7.7; Keys - Nyday 16 -> 11. Worse: Bass - Rivo (SineShaper distortion: Serum's folds are not reproduced).

## What changed (all rule-level, measured on Serum)
Engine: calibrated distortion (transfer curves, wet = smoothstep with dry 1-s^2, LevelOut), noise Brown + colour filters + random per-note pan, delay tempo sync + delay filter
make-up gain, Serum velocity law, DC removal after the distortion. Converter: comp thresholds follow the master, stage levels referenced to the voice (`finalize_level`),
velocity / note / random / mod-wheel route semantics, bipolar = source - 0.5, macro-driven detune / reso / drive / envelope sustain, PWM / sync / EvenOdd warps, noise type + colour,
H24/H12 cutoff law, delay filter scale 1.6, strongest distortion node, velocity law and depth. Details are in docs/TRANSLATION_RULES.md and in the comments next to each rule in `deephouse2move.py`, `fxmap.py` and `fx.h`.

## What to listen to first
Ari, The Selected (bass), Nyday (keys), Billboard, Can't Find You (keys and lead), Self Control Noise, I'll Be Yours, Sleepless, Loop, plus anything with a delay
(its times now follow the Move's tempo). Soft pads should now sound as loud as hard ones: only four presets respond to velocity in Serum, and the engine did for all 70.

## Open
Reverb build-up (engine tails ~5 dB under Serum's on played notes, structural), bus-rack FX (13 presets, e.g. Keys - Loop's reverb), second distortion / comp stage,
filter-before-distortion, unsupported warps (FM, filter, DLM) and Filter 2 models, Serum's SineShaper, Sync Var, AVAION onset HF.
