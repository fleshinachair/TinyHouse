# Changelog

## Unreleased

**Formant voice filters.** Serum's three Formant models (ONE, TWO, TWB) and Vital's Formant model are now their own engine filter, so presets that use them import instead of being skipped or read as a low-pass. Serum's are measured against the plugin (a sum of parallel band-pass resonators; Freq, Var, Reso and key tracking all move them), Vital's follows its own vowel tables with the X / Y, Spread, Transpose and Resonance knobs. They are closer to the originals than a low-pass but not exact: Serum's TWB is least exact at its highest Var, and a formant preset plays a few dB quieter than Serum until its level is refitted.

**The Move card and the remote UI draw the same wavetable lines.** The remote page now keeps the module's wavetable digest, the one the Move's card draws, instead of replacing it with its own read of the file (a different set of frames, with a guessed frame size).

**Vital imports: the envelope attack follows Vital's curve, and a route into a filter's Blend moves it live.** Vital's attack stage is a power curve (`env_N_attack_power`, read from its source: the curve is `powerScale(x, -power)`, squared into the gain for the amp envelope); the engine's attack was one fixed curve, 29 dB off at the start of a slow attack, now 0.7 to 3.8 dB (new hidden `vca_apow`, `flt_apow`, `me1_apow`, `me2_apow`; 0 keeps the old attack, so saved presets are unchanged). An LFO or envelope route into the Blend knob of the Analog, Phaser or Comb filter used to be folded in at the LFO's mean (an envelope not at all); it is now a live destination (Filter Blend, Filter 2 Blend, appended): 10 to 13 dB error against the plugin before, 1 to 2 dB after. The remote import report now lists the small details of Vital presets (they were hidden) in a closed group.

## 0.9.6

**Fixed: synced LFOs now follow the Move's tempo.** A tempo-synced LFO (Serum BeatSync, Vital Tempo / Dotted / Triplets) used to run at its 120 BPM rate whatever the tempo; it now scales with the Move's BPM. Vital's tempo-synced chorus, flanger and phaser still run at 120 BPM.

**Vital imports sound closer.** The engine gained, all off in Serum presets:
- Vital's filter drive law (its drive saturates even at 0 dB and costs passband with resonance), also on the Filter FX.
- Bit Crush (it was read as Soft).
- A fine pitch route, so a Vital oscillator Tune vibrato of a few cents is no longer rounded away.
- 64-point LFO shapes, so Vital's stepped and gated LFOs keep their edges.
- The oscillator start phase and random phase (a preset with fixed phases, such as Phatt_Bass, no longer starts from a random phase).

**Reimport your Vital presets** to get these; presets saved with 0.9.5 still load and sound as they did, because every new setting defaults to the old behaviour. Serum presets need no reimport, except ones with a synced LFO if the Move's tempo is not 120 BPM.

**Update**: replace the `tinyhouse` folder on the Move with the one in `tinyhouse-module.tar.gz` and restart the Move, so the new engine is loaded.

## 0.9.5

**New: Vital presets import.** TinyHouse now accepts [Vital](https://vital.audio) presets (`.vital` files, loose or zipped, and `.vitalbank`) the same way as Serum ones, in the remote page's Import or with the scripts. They carry their own wavetables and samples. Expect a similar sound, not an identical one; Vital imports are not level-matched, so set the volume by ear.

**Engine changed.** It gained Vital's filter Blend, Phaser filter, comb filter, compressor, chorus, flanger, phaser and reverb behaviour (all off in Serum presets), plus a wavetable card on the Move's screen while you hold a Tone-page position knob.

**Licence**: the Vital import and the engine parts derived from Vital are GPL-3.0-or-later, so this release's `dsp.so` and the remote page's import bundle are GPL as a whole (the licence texts are in the package). The rest of the source stays BSD-3-Clause.

**Update**: replace the `tinyhouse` folder on the Move with the one in `tinyhouse-module.tar.gz` and restart the Move, so the new engine is loaded.

## 0.9.1

**Fixed**
- A tick on presets that sweep the position of a stepped (no-interpolation) wavetable, such as the PWM pads (SY - Dual PWM Poly). The engine picked the frame once per block, so it changed in the middle of a cycle and put a step in the wave; it now changes only where a cycle begins. It showed with one voice and no unison, and went away once the filter was closed.
- Remote page: effect slots without a display (the EQ) grow to fit their controls instead of showing a scrollbar; the other slots get a thin scrollbar in the theme's colour.

**Changed**
- Imports now level presets to a common play loudness by default (the converter's `play_level.py`; `import_pack.py --no-play-level` keeps Serum's own levels). Remote-page imports are not levelled yet.

**Update**: replace the `tinyhouse` folder on the Move with the one in `tinyhouse-module.tar.gz` and restart the Move, so the new engine is loaded.

## 0.9.0 (first release)

TinyHouse turns Serum 2 presets into native patches for the Ableton Move (a Schwung module): a 3-oscillator wavetable, sample and granular synth with a full effects
chain, plus a converter that imports your own `.SerumPreset` files and packs, in the browser or with the Python scripts. It sounds similar to the Serum 2 original, not
identical.

**What is in it**
- Three wavetable oscillators with unison to 16, a sub and a noise oscillator, sample and multisample oscillators, and a granular oscillator.
- Warps (bend, PWM, sync, FM, phase-distortion, ring-mod, AM and the filter warps), two filters in parallel or series with the ladder, comb, flange, phase and other models.
- Envelopes, four LFOs, the arpeggiator, and routes from macros, velocity, note number, mod wheel and random sources.
- The whole effects chain and both FX buses: EQ, distortion, compressor, delay, chorus / flanger / phaser, HyperDimension, reverb and convolution reverb, also inside Split bands.
- Eight macros per slot. Imported presets bring theirs; you can assign your own from the remote page (right-click a knob). Assigning never changes the sound.
- Move pages (Main, Tone, Macros, Effects, Mod FX, Voice, Grain 1 to 3, Demo), a remote page for fine-tuning, and a demo mode that plays a part of a MIDI song on a slot.
- Of Serum 2's 626 factory presets, 363 import well enough to install.

**Known limits** (full list in the README)
- Formant filters, a second distortion / compressor / delay, Bode, and a few warp sources are not carried yet; such presets are skipped or import with that part left out.
- Synced LFOs run at their 120 BPM rate, not the Move's tempo.
- The Move's wavetable knobs list at most 128 tables.
- Some presets come out loud: turn Master Gain (VOL on the Tone page) down if one distorts.

**Install**: download `tinyhouse-module.tar.gz`, unpack it and copy the `tinyhouse` folder to `/data/UserData/schwung/modules/sound_generators/` on the Move (see the README, "Installing"), or build it yourself with `zig`.

TinyHouse is not affiliated with or endorsed by Xfer Records, Ableton or the Schwung project. No Serum content is included; you convert your own.
