# What Serum does in these 70 presets that TinyHouse does not (yet), most used first

> **Out of date.** Written on 2026-11-04 for the 70-preset Deep House pack, before the compressor, distortion, LFO, second filter, sample and bus work. See docs/ROADMAP.md for the current list.

Counted from the Serum files (calibration/fx_coverage.json, out/report.json). "Presets" is the number of the 70 that use it.

## Effects
| effect | presets | in TinyHouse |
|---|---:|---|
| Reverb | 68 | carried: measured and matched per preset |
| EQ | 64 | carried: 4 bands (EQ inside a Split branch dropped in 8 presets) |
| Delay | 44 | carried: one delay (a second is dropped in 9 presets) |
| Chorus | 42 | **approximated** by the width stage: spread and level only |
| HyperDimension | 41 | **approximated** by the width stage: spread and level only |
| Distortion | 37 | carried since this patch: soft / asym / hard curve + wet mix (20 presets) |
| Comp | 34 | **not carried** |
| Filter (as an effect) | 29 | partly: plain static low-pass / high-pass only (15 presets) |
| Utils | 14 | carried: master width and mono-below |
| Flanger | 10 | **approximated** by the width stage: no sweeping comb |
| SplitMS | 9 | **not carried** |
| Bode (frequency shifter) | 6 | **not carried** |
| Phaser | 6 | **approximated** by the width stage: no phase sweep |
| Convolution reverb | 4 | approximated as the reverb |
| Split (band split) | 1 | **not carried** |

There is no true flanger, chorus or phaser: one modulated-tap "width" stage stands in for all four modulation effects (47 presets have at least one).

## Everything else
| feature | presets | in TinyHouse |
|---|---:|---|
| macro-driven values | 68 | carried at the saved macro position (no live macro) |
| noise oscillator is a sample | 46 | approximated: plain white noise |
| LFO / velocity modulation routes | 36 (+5 where an effect is modulated) | **not carried**: no LFO in the engine |
| more envelope routes than the engine's two | 18 | dropped |
| filter model approximated (ladders etc.) | 16 | approximated |
| a second filter | 15 | **not carried** |
| a wavetable warp the engine lacks (FM, PWM, filter, even/odd) | 11 | **not carried** |
| sample / multisample oscillator | 9 | **not carried**: wavetable-only engine |
| sources that bypass the filter in Serum | 6 | approximated: everything is filtered |
| unison stacks that fit as fewer voices | 5 | approximated |

LFO routes by destination (>= 25 %): oscillator level 20, wavetable position 18, filter cutoff 11, fine pitch 5, phase 4, pan 3, warp 2.
Unsupported warps: FM_NOISE 4, PWM 2, FilterLPF 2, EvenOdd 2, DistZeroSquare, FilterHPF, DLM, FM_OSC 1 each.
