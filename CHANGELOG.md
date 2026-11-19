# Changelog

## 0.9.9

Every new parameter defaults to the old behaviour; saved presets load and sound the same, and a Vital preset gains the new behaviour when it is imported again.

**Vital's Key and Key-in-octave sources are live.** A route from `note` or `note_in_octave` (13 of the 100 local presets) used to be dropped. An LFO now has two more modes, Key and Key Oct: instead of running, it looks the note up in its Hi-Res table, so a route keeps its own curve (Touch Tone's twelve-step pitch tables, a cutoff that opens with the key). They take an LFO each, ranked with the real LFOs by the strength of their routes. Against the plugin over 100 presets the mean band error went from 11.02 to 10.84 dB: 8 presets better (Touch Tone 42.9 to 31.9, FM Mode 19.7 to 16.1), 2 slightly worse.

**Velocity and Aftertouch are live route sources.** Two more sources on the eight routes: the note's velocity (0 to 1, fixed for the note) and the pad pressure (the Move's channel pressure or a pad's own, 0 when nothing is sent; a new note starts it at 0). Vital routes from either (29 of 100 local presets use velocity, one uses aftertouch) now follow the pad while route slots last; before, velocity was added at a fixed 100 and aftertouch was dropped. Against the plugin, a velocity route to the cutoff follows the same curve across velocities 25, 64 and 127 (centroid 563 / 887 / 2244 Hz against 446 / 764 / 1860) and one to the oscillator level is linear in velocity as in the plugin. Routes that do not fit in the eight slots, or whose control has no live destination, are still added at velocity 100.

**Two more routable envelopes for Vital presets.** A preset's envelope routes used to be ranked onto the engine's two spare envelopes and the rest dropped (12 of the 100 local presets lost some). There are now four (envelopes 3 and 4 are hidden: the converter sets them, no page shows them, and they cost nothing unless a preset points them somewhere). Mean error over the 100 presets 10.84 to 10.79 dB: Piano from the yard sale 27.5 to 23.4, Super Nice Pluck 7.9 to 6.1, Railgun 12.9 to 12.0; DIY (5.7 to 6.8) and BS_Dark_House (11.2 to 11.9) are a little worse.

**Vital's comb filter Blend Transpose moves live** (destinations Filter Comb Trans and Filter 2 Comb Trans, appended; a fraction of Vital's 0..84 semitone range, held to it as Vital does). A macro or velocity on it is added to the stored transpose. On a saw through a comb with an LFO on the transpose: 17.4 to 9.7 dB.

**An oscillator with Key Track off plays one pitch** (Vital's constant note 48 plus its transpose and tune) instead of following the key: Touch Tone, Kick Drum 1, Squish Clicker.

**Vital's voice transpose and voice tune are live routes** (Voice Pitch, Voice Fine): the whole voice moves, the plugin's pitch is matched to 0.3 semitones.

**Eight LFOs and sixteen routes for Vital presets.** Vital has eight LFOs and 64 routes; the engine had four LFOs and eight routes. LFO 5 to 8 are route sources 11 to 14 (hidden banks Vital LFO 5-8 and Vital LFO Routes 9-16, appended: saved presets and the module's parameter list are unchanged apart from a few hundred bytes).

**Vital's Analog filter is the engine's own now.** The Sallen-Key filter (12 or 24 dB, with the Blend knob between low, band and high-pass) is ported from Vital's source in place of the biquad: its response matches the plugin to about 0.3 dB at any resonance and 1 dB at heavy drive (the biquad was 2 to 4 dB off near the cutoff, and 1.5 dB under the real passband at no resonance). Vital's resonance passband loss and drive law come with it. 71 of the 100 local presets use this model.

**Vital's EQ is Vital's EQ.** The three bands are two shapes each: the low band a shelf or a high-pass (mode 1), the band a bell or a notch, the high band a shelf or a low-pass. Mode 1 used to be read as a shelf with a gain; it now cuts. The engine gains six EQ band types (V LoSh, V Bell, V HiSh, V HP, V LP, V Notch) and a hidden fine-frequency control, exact against the plugin to 0.1 dB. Fun Pulse went from 13.4 to 7.4 dB of error, corrupted_...+=boot-_ from 9.7 to 5.6.

**Smaller Vital fixes.** The engine's level against Vital's was 2.75 dB low (median preset 3.9 dB); a Line Source wavetable without a nested "line" plays its initial ramp as Vital does (it rendered as silence); a Key route sees the voice transpose and tune; the amplitude knob is not capped at 1 and glides in over the first 100 ms of a note; filter and spare envelopes keep Vital's own decay curve; a modulation envelope takes one stage a block, as Vital's control-rate envelopes do; a silent phase-distortion table falls back to the LFO mean.

## 0.9.8

The formant filters planned as 0.9.7 and the Vital leads planned as 0.9.8 ship together. Saved presets from earlier versions load and sound the same: of 112 presets rendered with the 0.9.6 engine and this one, 64 are bit-identical and the other 48 (noise and random phases make them differ on every render) differ by no more than their own run-to-run noise. Every new parameter defaults to the old behaviour, so a Serum preset is unchanged; a Vital preset gains the new behaviour when it is imported again.

**Fixed: the module's parameter list stays inside the host's 64 KB limit.** The host refuses to load a synth whose list does not fit. The new parameters would have crossed it, so keys that only the converter or a preset load sets are no longer published (the list is 56.7 KB, from 64.7 KB in 0.9.6). Nothing on the device or the remote page used them.

**Formant voice filters.** Serum's three Formant models (ONE, TWO, TWB) and Vital's Formant model are now their own engine filter, so presets that use them import instead of being skipped or read as a low-pass. Serum's are measured against the plugin (a sum of parallel band-pass resonators; Freq, Var, Reso and key tracking all move them), Vital's follows its own vowel tables with the X / Y, Spread, Transpose and Resonance knobs. They are closer to the originals than a low-pass but not exact: Serum's TWB is least exact at its highest Var, and a formant preset plays a few dB quieter than Serum until its level is refitted.

**The Move card and the remote UI draw the same wavetable lines.** The remote page now keeps the module's wavetable digest, the one the Move's card draws, instead of replacing it with its own read of the file (a different set of frames, with a guessed frame size).

**Vital's random modulators are live.** Sample-and-hold, Perlin, Sine and Lorenz (`random_lfo.cpp`) run in the engine as four extra sources on the eight LFO routes (Rnd1 to Rnd4, with a rate and a Tempo switch, so a synced one follows the Move's tempo), with a new random value drawn for every note, as Vital does. 21 of the 33 local presets that route one now carry it; before, the route was dropped, which left its cutoff, pitch, wave-frame or pan fixed. Against the plugin (a saw through a low-pass, 12 notes), the spread of the cutoff effect between and within notes has the same shape for all four styles. New hidden params `rn1..4_style / _rate / _tempo`; saved presets are unchanged. A route from a sixth or later LFO is now reported as not carried instead of vanishing.

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
