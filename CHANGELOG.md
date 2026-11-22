# Changelog

## 0.9.15

**Sets keep their presets.** Importing through the Remote UI while a Set was open could move its slots off their presets: each slot remembered its place in the preset list rather than the preset, so when an import changed the list, a slot pointed at another preset or at the bank's first one. The sound stayed, but the Set saved the wrong preset, and after a restart the slots showed the first preset. Slots now stay on their preset by name. If a slot's preset is missing for a while (while its bank is being rewritten, or removed by an update), the slot keeps its sound and keeps naming that preset, and goes back to it when it returns. Only choosing another preset moves a slot.

**Saved Sets follow updates.** A slot playing a preset you have not changed now remembers which converter made it. When you open the Set after an update has converted that preset again, the slot takes the new sound and stays on the same preset. A sound you have edited is never replaced. Sets saved before this version do not have that record: choose the preset once more in those slots and they follow updates from then on.

**Serum 2's renamed factory samples are found.** Serum 2 renamed some factory multisamples ("Ah Both (Xfade)" is now "Ah Both", "Auxiliary Kit" is now "Perc Auxiliary"). When a sample is not found, the importer now looks in the same folder for the one file whose name shares the most words with it, ignoring a part in brackets. If two files match equally, neither is used. KIT - Auxillary Kit, FX - Creepy CB Radio Chatter and VOX - Choir Ascension now import with their samples. Import those three again: their samples were never copied to the Move. No Serum 2 factory preset misses a sample now.

## 0.9.14

**An update only converts the presets it changes.** Each preset imported through the Remote UI now keeps a small record (about 20 KB, beside its original) of which parts of the converter its conversion went through. After an update, the Move checks that record against the new converter: a preset whose path is unchanged would come out the same, so it is kept as it is (about a tenth of a second) instead of being converted again. Only the presets a fix can change are converted. How many that is depends on the fix: replaying the last eleven converter fixes over Serum's 626 factory presets, a fix to one feature reached between 0 and 420 of them, while a fix to how every preset's parameters are read still reaches all 626. In that replay no preset was kept whose sound would have changed.

- Presets imported through the Remote UI in 0.9.13 have no record yet: the Move converts each of them once more after this update (in the background, at low priority) and records it; from the next update on they are checked instead. Presets imported before 0.9.13 are still left alone until they are imported again.
- Importing through the Remote UI takes about 40 % longer, while the record is made.

**Serum 2's factory drum kits come in from the Remote UI.** Serum 2 moved its factory multisamples into sub-folders and its kits play files from the Samples folder; the Remote UI looked for neither, so 8 kits and loops (808 Basic Kit, IDM Beat, GrittyKitCommittee, Machine Basic Kit, Breakwave, BSOD Big Convolver, New Dawn, On The Couch) were silent, and LOOP - Funk Practice missed a string sample. Import them again to get the sound: their audio has to come from your Serum folder. Three factory presets whose sample files Serum 2 renamed still miss them.

**A Sample or Granular oscillator with no file loaded is not reported as a gap** (Serum plays nothing there either; BA - Bubba).

## 0.9.13

**Presets upgrade themselves on the Move.** A preset imported through the Remote UI now keeps its original Serum or Vital file on the Move (in `UserLibrary/TinyHouse Sources/<bank>`), stamped with the converter that made it. After a module update, the Move converts those presets again with the new converter: in the background after start-up, and at once when you choose one that has not been done yet (the header reads "Upgrading to v0.9.13" for a moment). Fixes to the importer therefore reach your presets without importing them again. The conversion runs at low priority on the cores the audio does not use; a preset whose sound did not change is left untouched. The module now ships the converter (with numpy for the Move), so the download is about 40 MB larger.

**Import once more.** Presets imported before this version have no original kept, so they cannot upgrade. The Remote UI shows how many there are per bank, and the Move's header says "Re-import: see web UI" once after an update when you choose one. Import them again through the Remote UI (same bank name, so the wavetables and samples already on the Move are reused); from then on updates reach them.

**Remote UI import.**
- Every preset is imported, including ones with something missing. The option to import only complete presets is gone: what was left out before now comes in and gets better with each update. The report still lists what each preset misses.
- Choosing a folder with several banks (such as Serum's Presets folder, which holds Factory, User and any installed packs) and ticking more than one imports each into a bank of its own name. They all went into the one bank before.
- Presets that could not be converted are kept on a waiting list and tried again after each update.

**Serum import.**
- Ratio and Harmonics pitch modes, a wavetable oscillator's Fine tune, and the global Transpose are carried.
- Velocity and note routes folded into a fixed knob use the route's curve (multisample instruments crossfade their velocity layers correctly).
- All four engine mod envelopes are used (was two): a kick's pitch drop on its loudest oscillator was dropped, for example.
- The FX bus Volume knobs are carried.
- Clip Player presets whose active clips do not span the keyboard play those clips on any key, untransposed, as Serum does.
- A noise or sub oscillator lifted by a macro no longer counts as silent when deciding whether the voice filters are bypassed.
- The EQ fitting runs about a third faster, with the same result.

**Engine.** A multisample key that no region holds now plays nothing, as in Serum (drum kits played a cymbal or tom on every key above the kit).

**Vital import.** The per-note random source is folded in at its mean, like Vital's random modulators.

## 0.9.12

Serum 2 import: 62 more of Serum's own Factory presets are imported (453 to 515 of 626 on the development Move): 28 pass the importer's gate, 12 miss it by 0.5 to 2.6 dB of band error, 13 use the nearest available voice filter where Tablor lacks Serum's model, and 9 play a Spectral oscillator's sample without its scan. All 62 were auditioned and accepted. Heavy sets no longer glitch: each TinyHouse slot now renders on a CPU core of its own. Every new parameter defaults to the old behaviour, so saved presets load and sound the same.

**Each slot renders on its own core.** Schwung runs every slot one after another on one core, so four heavy slots added up: a four-slot set measured 76 % of the 2.9 ms audio block on average and went over it at its peaks (the glitches). Each TinyHouse slot now renders its next block on a thread of its own (real-time priority 20, on the three cores the audio callback does not use) while the callback hands Move the block that thread finished, so the slots render side by side. On the Move, four slots of that set went from 2.74 ms of callback time per block (712 overruns in 9 s) to 0.04 ms (none). The sound is the same sample for sample, one block (2.9 ms) later. If the Move refuses real-time priority, the module renders as before; creating `/data/UserData/schwung/tinyhouse_sync_render` and restarting turns the thread off.

**The Remote UI importer flags fewer gaps.** Three general rules, not per-preset fixes: a Convolve effect left dry is not a gap, a Bode frequency shifter is shown as a small detail, and Serum's kSpace reverb size is translated by a law fitted from Serum (rough, five presets). Of Serum's 626 factory presets all import and play in the browser; 447 now show nothing notable missing (419 in 0.9.11) and 73 are audibly compromised (81). The browser has no Serum to compare against, so it imports these without the gate the Python importer uses.

**Vital's compressor costs less.** It works on left and right together, and the low band is recombined with one all-pass filter where it took four (the same response; the output matches the old one to -86 dB). Super Pluck renders 8 % faster on the Move.

**Other audio-thread work.** A cloud of grains is capped at 64 live grains per voice, with the level kept (no factory preset gets near it; the densest uses 22). The delay, width and reverb memory that a switched-off stage wipes is cleared 64 KB per block instead of all in one block. Wavetables, sample sets and convolution plans are freed by the loader thread instead of wherever the last note lets go of them. Vital's delay and reverb memory is made when the preset is set, not on the first note, and the envelope curves no longer compute the same exponential twice per sample.

**Chorus, flanger and phaser follow the tempo.** A Tempo Sync switch on each scales its Rate (stored at 120 BPM) by the host tempo; the Vital importer sets it where the file says synced.

**A second distortion.** A Serum rack with two Distortion nodes now carries both (the second runs right after the first, or right before it when the rack has it first), and the Distortion's own filter before the curve can sit anywhere between low-pass and high-pass (it only had the two ends). 9 factory presets that were stopped by these pass the gate.

**Granular.** An LFO or envelope can move a granular slot's Start, Position (Manual Position) and Random Pitch. A warp type with no amount on the grains is no longer reported as a gap where Serum leaves the grain untouched at amount 0 (FM, AM, PD, Dist SoftSat / Tube / Diode, HPF; DistAsym and LPF do change it, so they stay reported).

**Multisamples find their SFZ again.** Serum 2's library moved the factory drum kits into subfolders, so the stored path no longer exists; the importer now looks the file up by name (DR - IDM Beat comes in). The browser importer does not do this yet.

**Spectral still needs more work.** The 9 Spectral presets that came in play the oscillator's sample as it stands, with no scan and no spectral filter, so they are approximations. About 29 more Spectral presets stay blocked until a spectral resynthesis stage exists (see docs/ROADMAP.md).

Looked at and left alone: a reverb build-up stage. A longer input diffusion shifts the tail level but not the spread between presets (median error 4.2 against 4.9 dB), so there is nothing to fix with a common stage.

## 0.9.11

Serum 2 import: 69 more of Serum's own Factory presets now convert and pass the importer's gate (384 to 453 installed on the development Move, of 626), and 33 already installed ones were redone with the fixes below. Every new parameter defaults to the old behaviour, so saved presets load and sound the same. Serum presets need importing again to get the fixes.

**Comb, flange and phase filters.** Serum's CombP/N, FlangeP/N and Phase12 to 48 P Filter FX models, and the L6 / H6 / HL6 comb and flange voice filters, are in the engine, measured against Serum (0.1 to 0.9 dB per harmonic). A comb's tuning no longer runs off the end of its knob.

**Voice filter Wet is a real dry / wet mix.** Serum's Wet on a voice filter mixes the dry and filtered signal linearly; presets with Wet under 100 % were too dark.

**Sample slots take Octave, Pitch and Fine routes** (BA - Fonk went from 15.1 to 5.9 dB of band error), and a Diffuser Filter FX is skipped (it is flat in level in Serum).

**Spectral oscillators.** A Spectral oscillator imports when it hardly scans (Scan Rate at the default or at most 5 %, or the oscillator is silent): the engine plays the sample it holds, without Serum's scan or spectral filter. The 29 factory presets that scan stay blocked until a spectral resynthesis stage exists.

**A voice filter model the engine does not have** (Reverb1, DistComb, Phase48 and others) can fall back to the nearest filter when the importer is run with `TH_FILTER_FALLBACK=1`; the Serum gate judges the result. 12 factory presets passed (band error 2.3 to 13.6 dB rms). The importer's effects check also accepts a bus Filter FX or Utils that is inaudible in Serum, and a level difference up to 4 dB.

Known gaps: the remote page still imports at the converter's own volume (no common play level), and a few of the new presets approximate their filter or leave the spectral scan out; the roadmap lists what each needs.

## 0.9.10

**Important: 0.9.9 lost saved sounds. Update if you use Schwung sets.** Schwung drops any saved module state longer than 16,384 bytes, and 0.9.9's state had grown to about 21 KB (it listed every hidden setting). After a reboot or a set switch the slot loaded back as the default sound (the first Deep House preset), and the next save wrote that default over your set. The state now lists only the settings the host shows (about 7 KB for a default sound) and falls back to the compact form if it would ever pass 15.8 KB; the on-device test fails if the state goes over the limit again. Sounds already overwritten cannot be recovered: pick them again and save the set.

Everything below comes from comparing the engine with the Vital plugin preset by preset (100 local presets; the mean band error went from 7.9 to 6.0 dB, the median level error from -1.7 to -0.3 dB). Vital presets need importing again to get it.

**Vital's own filter models.** The Analog (Sallen-Key), Ladder, Dirty, Digital and Diode filters are ported from Vital's source for the voice filters and the Filter FX, 12 and 24 dB with the Blend knob, and the Analog model's notch / pass swap, dual notch / band and band / peak / notch styles. Measured against the plugin they agree to about 0.5 dB at any cutoff and resonance (the biquads were 2 to 4 dB off near the cutoff). The Filter FX also runs Vital's comb with its style, Blend and Blend Transpose.

**Wavetable levels.** A wavetable is normalised as Vital does it (the groups are averaged and the table scaled by its widest peak-to-peak span, so an asymmetric frame can pass 1), and an Audio File Source frame is normalised by its own peak when the component says so (Gorgled was 14 dB low, Feeder's error went from 23 to 17 dB).

**Volume and Velocity Track.** The master Volume knob follows its real square-root scale (86 dB per unit of sqrt(v / 7399): presets at the ends of the knob were 1 to 5 dB off), and Velocity Track is carried (a positive track as the engine's amp velocity sensitivity, a negative one as its gain at velocity 100).

**Delay and EQ routes.** The delay is a port of Vital's own: mono, stereo, ping pong and mid ping pong, a separate Aux time and sync switch, an equal-power dry / wet (the ping-pong styles had the wrong echo times). An LFO or envelope route into an EQ band's cutoff or gain is carried (13 of the 100 local presets used one and lost it). An Audio File Source wavetable takes its Clear / Vocode phase style, which sets its level (Gorgled 12.3 to 8.4 dB of error, Ah Eh Ee Oh 12.8 to 9.8).

**Vocode morph.** Vital's Vocode spectral morph (Formant scale read at the note's absolute frequency) is baked at middle C, exact against the plugin on a wave source (Text To Wavetable Template went from 12.2 to 4.4 dB of error, Ah Eh Ee Oh from 10.2 to 6.4); it does not follow the key, so other notes keep the middle-C envelope.

**Fixes found by testing properly.** A Vital preset has no level glide at note-on any more (it was the plugin's own start-up smoothing, which only shows when a note starts within 0.1 s of loading, and it made low-level oscillators 2 to 10 times too loud for 100 ms of every note) and no 3 ms attack floor; the distortion's level against Vital's is the re-measured 9.45 dB (soft and hard clip were 3 dB too gentle at high drive).

**Distortion runs at 2x like Vital's.** Vital runs its whole effect chain at twice the sample rate (its Oversampling setting, 2x unless a preset says otherwise), so a clip at high drive, a fold or a bit crush makes much less aliasing there. A Vital preset's distortion now does the same (a new "Vital 2x" value on Distortion Calibrated: a 47-tap half-band up and down around the curve), and Down Sample is a port of Vital's own (the hold length follows its drive law and works on pairs of samples). A soft clip of a note an octave and a half up at 25 dB drive went from 3.4 to 0.1 dB of error, Float Chords from 12.7 to 6.9, Snowcrash 13.0 to 11.9; Fun Pulse (8.0 to 9.2) and FM Drum Circle (7.4 to 9.3, a chaotic preset) are a little worse. Over the 32 local presets with a hard distortion the mean went from 6.83 to 6.65 dB.

**One LFO moves another.** A route from an LFO or an envelope to another LFO's Tempo, Phase (or, while it stays below audio rate, Frequency) was dropped (Staggered Phrases, Thumpus, LORN Style Lead, Strings Section, Abbysun, 6 of the 100 local presets). The engine has sixteen more route destinations, LFO 1..8 Rate and Phase (the Tempo knob steps by octaves as Vital's does); isolated, a phase route follows the plugin (Thumpus 3.2 to 2.5 dB), inside a full preset the results are mixed within the scan noise.

**A route's own curve on an LFO.** A Vital route can carry a drawn transfer curve (and a power) between its source and its destination. For a macro or velocity that was folded in; for an LFO it was ignored, so the LFO swept the wrong shape (Squish Clicker's filter sweep was a rising ramp in the engine and a hump in the plugin). The LFO now takes an engine slot of its own with its shape put through the route's curve (any LFO moves that way together: rate and phase routes reach all its copies). Squish Clicker 12.1 to 8.3 dB, Thumpus 12.0 to 11.1.

**Vital wavetables are no longer dull at the top.** The engine reads a wavetable with a linear interpolation from mip levels stored at the smallest size that holds their harmonics, which costs the top octave: a plain saw was 1.8 dB down in the 8 to 16 kHz octave and 8 dB down at 21 kHz, and the bias showed on the 100-preset average (8 to 16 kHz -0.9 dB, 2 to 8 kHz -0.5 dB). Tables the Vital importer writes (vital_*.wav, installed ones too, no reimport needed) are now pre-emphasised to cancel it: the saw is within 0.2 dB up to 17 kHz. Serum tables are untouched.

**A live morph or distortion amount.** An LFO or envelope on an oscillator's spectral morph or phase-distortion amount (58 oscillators in 42 of the 100 local presets) ran at its saved amount only. When the oscillator's frame is not moved, the table is now rebuilt with the amount along its frame axis and the route drives the position (A happy ending of the world 12.5 to 8.3 dB, Boot Scre3n -3.5, VLT Future Gun -2.1, Haven_Chords -2.0; Moving Harmonics, Sad_Peaky_House_Synth and Satellites are 2 to 3 dB worse). A macro or velocity route to the master Volume is added to the stored value, and an LFO route to a reverb, chorus, flanger, phaser or delay control sits at the LFO's average instead of being dropped; a tempo index of 0 is a frozen LFO (Thumpus 11.1 to 10.4). Mean error over the 100 presets for the whole release: 7.9 to 6.0 dB.

**Envelope curves.** The amplitude envelope's decay and release, the filter envelope and the spare envelopes draw Vital's own curve at every power (the analog fall was 2 to 10 dB off over a tail); Vital's control-rate envelopes take one stage a block.


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
