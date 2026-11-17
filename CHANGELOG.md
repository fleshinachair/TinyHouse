# Changelog

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
- Move pages (Main, Tone, Macros, Effects, Mod FX, Voice, Grain 1 to 3, Demo), a remote page laid out after Serum for fine-tuning, and a demo mode that plays a part of a MIDI song on a slot.
- Of Serum 2's 626 factory presets, 363 import well enough to install.

**Known limits** (full list in the README)
- Formant filters, a second distortion / compressor / delay, Bode, and a few warp sources are not carried yet; such presets are skipped or import with that part left out.
- Synced LFOs run at their 120 BPM rate, not the Move's tempo.
- The Move's wavetable knobs list at most 128 tables.
- Some presets come out loud: turn Master Gain (VOL on the Tone page) down if one distorts.

**Install**: download `tinyhouse-module.tar.gz`, unpack it and copy the `tinyhouse` folder to `/data/UserData/schwung/modules/sound_generators/` on the Move (see the README, "Installing"), or build it yourself with `zig`.

TinyHouse is not affiliated with or endorsed by Xfer Records, Ableton or the Schwung project. No Serum content is included; you convert your own.
