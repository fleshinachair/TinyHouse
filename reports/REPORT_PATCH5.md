# Patch 5: Serum's own demos, full-chain fits, level match

Status: presets regenerated into `out/` and checked; **not installed on the Move** (installing is the user's
call; `./deploy_presets.sh [host]` now backs up to `/data/UserData/tablor-dh-rollback-p5`, so Patch 4's
rollback copy is kept). No engine change, `dsp.so` (1.6.0-dh) untouched. Patch 4's deployed presets and fits are
in `calibration/p4_record/` (git-ignored).

## 1. Why the presets do not sound like the product page's demos

The 11 demos on the Loopsy page are produced loops: several are tagged lead + keys or bass + lead (several
presets layered), all peak at 0 dBFS with RMS -8 to -17 dB (mastered), some probably carry drums. They cannot be
matched as played and the demo MIDI is not in the pack. Four are tagged with a single preset, so those were
rendered through Serum (all FX) and Tablor (converter output) on the same chord and compared on
pitch-independent measures (the chord is mine, not the demo's, so only gaps over about 5 dB count):

- **Level**: Tablor's full-chain RMS was 0.7 to 7.8 dB under Serum's (Can't Find You 7.5, Gravity 7.8).
- **Top end**: +7 to +13 dB at 3-8 kHz and +27 to +37 dB above 8 kHz *relative to each render's total*. In
  absolute terms the >8 kHz band is quiet (Gravity: about -70 dBFS), so this is a spectral-shape gap, not an
  audible wall of hiss, which an earlier summary of mine overstated. Dry, Tablor's bright patches (Can't Find You,
  Catching Flies) are +4 to +12 dB over Serum from 3 to 20 kHz; separately Tablor's dry output has a flat floor
  around -71 dB re total above 5 kHz that Serum's float output does not, and a big EQ high boost lifts it
  (Gravity's mapped EQ is +10 dB at 6 kHz, +25 dB at 14 kHz; EQ flat removes about 25 dB of its top end).
  The source of that floor is **not found** (the oscillators are band-limited).
- **Width**: Can't Find You is far wider in Tablor (side/mid -4 to -6 dB against Serum's -14.7).
- **Not carried**: Gravity and Sleepless have distortion and compressors.

## 2. What was done

- **EQ boost cap tested, not adopted** (`calibration/eq_cap_test.py`): only 4 of 70 presets map a boost above
  +12 dB. Capping at +12 dB helped 3, hurt 1 (Pluck - all things break, already too dark), and left Gravity +25 dB
  over above 8 kHz: the floor, not the boost size, is the problem.
- **Full-chain fit** (`fit_chain.py`, new refs from `fill_fx_refs.py`): filter cutoff, envelope depth, decay and
  resonance plus the FX width and reverb width, with the converter's FX block on, against Serum's full chain.
  Starts from what shipped in Patch 4. 25 of 70 kept (at least 10 % loss cut; median 21 % among them).
  Resonance is in the fit; it changes little (a separate dry resonance fit over 65 presets: median change 0).
- **Level match** (`fit_level.py`): volume bisected so the full-chain RMS equals Serum's, capped so the output
  limiter takes at most 2 dB. Replaces the clip guard's volume. 33 of 70 are now within 1.5 dB RMS (was 11).
  Bisect bug found and fixed (it could return 128); wording of the converter note fixed.
- Converter: `apply_chain_fit` (after the FX block) and `apply_level_fit` (after the clip guard), both with the
  same stale-fit guard as the earlier fits (the level fit compares a signature of every other parameter).

## 3. Results against Serum's full chain (`chain_report.py`)

The metric's own noise (identical presets scored twice, random unison phases): about 0.3 dB on a 70-preset
average, closer/further splitting 50/50, so **per-preset and 14-preset deltas are noise; read the 70-preset rows**.

| 70 presets, mean | Patch 4 | Patch 5 | better / worse | held-out (+5 st) |
|---|---|---|---|---|
| RMS error, \|dB\| | 5.74 | 3.43 | 42 / 7 | 5.57 -> 3.56 |
| 1-3 kHz band error, \|dB\| | 7.18 | 5.11 | 30 / 18 | 7.42 -> 5.23 |
| 3-8 kHz band error, \|dB\| | 12.12 | 8.78 | 35 / 21 | 12.17 -> 8.33 |
| >8 kHz band error, \|dB\| | 18.19 | 15.18 | 35 / 14 | 17.25 -> 14.38 |
| side/mid error, \|dB\| | 10.53 | 10.03 | 24 / 11 | 11.28 -> 10.84 (noise-level) |
| full-chain loss | 19.68 | 17.07 | 51 / 8 | 20.20 -> 17.94 |

Held-out split: on the 25 chain-fitted presets the loss goes 21.9 -> 16.1 (22 better, 1 worse), 3-8 kHz error
18.8 -> 8.8, >8 kHz 23.2 -> 15.8. On the other 45 only RMS moves (5.7 -> 3.5); spectrum and width are unchanged.
The 14-preset test group of earlier patches (13 here) is not significant: its 3-8 kHz signed error went +7.0 ->
+11.1 dB (7 of 11 further) and it should not be read as an improvement there.

## 4. Open

- **Level cannot reach Serum on 38 presets**: the volume is already at 127 (engine master = (pot/127)^2 x 0.8, so
  maximum is -2 dBFS-ish) and they are still 1.5 to 17 dB quieter (worst: Bass - oskar med k 17, Pluck - NOTSOBAD 17,
  Pluck - Selected 14). Serum's missing loudness is mostly FX not carried (distortion, compressor make-up) and sample
  oscillators. 34 presets are still more than 1.5 dB under. Fixing it needs gain the engine does not have: a larger
  master range in the dh build (engine change, rebuild, install of `dsp.so`) with `pot_volume` changed in step.
- **Top end still +7.5 dB signed / 15 dB absolute at >8 kHz** over 70 presets, and the dry floor is unexplained.
- Distortion (36 presets), compressor (34), filter FX (27) still not carried; mono-bass crossover (side-channel
  high-pass in `fx.h`) noted for later if the bass sounds phasey on speakers.
- Width is barely improved (side/mid |error| 10 dB, noise-level change).
- Nobody has compared this against Serum by ear; the demos are layered and mastered.
