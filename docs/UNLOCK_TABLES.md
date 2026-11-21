# What is left to unlock: Serum and Vital, ranked

Measured on 2026-11-19 with the converter as it stood after the formant filters (Vital Formant and Serum Formant ONE / TWO / TWB, uncommitted at the time). Use it to pick the next piece of engine work: take the top row whose "what it takes" you can build, and check the table again afterwards.

**Read "unlocks" as "has no converter note left", not "cannot be installed".** The census is the converter's verdict only. The real install gate (`converter/import_pack.py`) also renders the preset in Serum with the left-out effects switched off and keeps it when the difference is inaudible, and keeps a Bode-only preset even when it is audible (rule 7b61e4e, 2026-11-15: 21 of 25 Bode-only presets installed; the Factory bank on the Move holds 384). So the rows that are only left-out effects (Bode first, also parts of rows 5, 8, 9) overstate what is blocked; the rows that name an unsupported oscillator, warp or voice filter are real. Tier C (87 presets) is the better guide to audible gaps.

## Serum factory presets (626)

A fresh census (`python calibration/census.py "<Serum 2 Factory folder>" --name factory_now`, convert only, no Serum renders): **364 presets are clean, 262 have at least one "not carried" reason.** The reasons are grouped by me into the pieces below, and a preset often has several, so "touched" is more than "unlocks".

**Unlocks** is greedy: fix the piece that clears the most presets, take those out, repeat. "Cumulative" is the running total of presets with nothing left blocking them.

| # | Missing piece | Presets touched | Unlocks | Cumulative | What it takes |
|---|---|---:|---:|---:|---|
| 1 | **Filter FX models that are not a magnitude response** (Comb, Allpass, Phase, RM, SNH, P12, and others) | 62 | 30 | 30 | A probe per model, then engine stages in the FX filter. Some of the voice-filter comb and phase code can be reused. |
| 2 | **Random modulators as a source** (sample-and-hold, perlin): **done in the 0.9.8 work.** 21 of the 33 presets that use one now carry it live (Perlin, Sample and Hold, Sine, Lorenz; a new value per note). What is left of the 33: routes to a baked destination (spectral morph or distortion amount), the Filter FX, and a route into another LFO's tempo | 24 | Closed, bar the baked destinations (row 1) |
| 3 | **Routes to pitch** (random, note-in-octave): random is live now; note-in-octave is not | 20 | A note-in-octave source |
| 4 | **Filter cutoff routes** (11 from random, 3 from note): random is live now | 19 | A note source for the remaining 3 |
| 5 | **Split / Split3 / SplitMS branches** (HyperD, Delay, Comp, Bode, Reverb or Utils inside a band) | 41 | 21 | 133 | Allow more effect types in a band. |
| 6 | **Convolution / Space reverb** | 40 | 17 | 150 | Measure Space reverb. The convolver already exists. |
| 7 | **Granular gaps** (warp on the grains, routes into Warp / Pitch / Position) | 19 | 15 | 165 | Probes P8 and the others in [GRANULAR_PROBE_NOTES.md](GRANULAR_PROBE_NOTES.md). |
| 8 | **Utils** moved by an LFO or envelope, mono-below in a bus, or in a branch | 21 | 15 | 180 | Extend the Utils stage. |
| 9 | **A second Distortion / Comp / Delay / mod effect**, other rack gaps | 21 | 12 | 192 | A second instance of each stage. |
| 10 | **Filter FX model: Diffuser** | 17 | 14 | 206 | One model probe plus a stage. |
| 11 | **Filter FX moved by an LFO or velocity** | 17 | 13 | 219 | The engine's FX filter follows the filter envelope only today. |
| 12 | **MultiSample oscillator** (was: beyond the three sample slots) | 10 | 1 | 228 | Not a slot-count problem: the stored SFZ path no longer exists in Serum 2's library (kits moved into subfolders). 0.9.12 finds the file by name and recovers DR - IDM Beat; the other 9 are blocked by other things (convolution reverb, modulated Utils, level, two SFZ files missing from this install). |
| 13 | **Distortion filter morph before the distortion** | 9 | 9 | 237 | The engine's pre-filter has only the two ends. |
| 14 | **Routes to wave frame** (random, note): random is live now | 8 | A note source |
| 15 | **Single warp types** (FM_FILT, PD_FILT, DistTapeSat, SelfPD amounts, Flip, ...) | 19 | 19 | 262 | A probe per warp, one or two presets each. |

Rows 1 to 6 clear 150 of the 262 blocked presets. Serum's Formant ONE / TWO / TWB are done and no longer in the list. Tier counts from the same run: A 211, B 328, C 87.

## Vital presets (100 local: 85 in the Documents folder, 15 in presetshare)

Nothing is blocked: all 100 import. These are gaps in the sound, found from the import notes (`notes` in the converter's result: "not carried: source->destination", "with nothing carried to raise it", and similar). **87 of the 100 have at least one gap**, and most are small.

**Count is not worth.** Measured on 2026-11-20: each preset played at note 60 for 2.6 s through the plugin and the engine (the engine's volume matched to the plugin's, see below), the octave bands compared in six time windows where the plugin is sounding. Mean error 11.0 dB over the 100 (median 10.0, 90th percentile 18.0). The table ranks by the gap in that error between presets that have the gap and presets that do not ("with" and "without", dB); it is a correlation, not an ablation, and the worst presets carry several gaps at once, so read it as an order, not as the size of the fix. The LFO / envelope route to a morph amount, the biggest by count, makes almost no difference (+0.9 dB), and a trial of sweeping the amount along the table position (`AMOUNT_SWEEP`) changed 35 presets by nothing on average (12.54 to 12.56 dB; 7 better, 7 worse): it stays off.

| # | Missing or approximate piece | Presets | Error with / without | What it takes |
|---|---|---:|---:|---|
| 1 | ~~**Routes to an oscillator's tune or transpose from a source the engine lacks** (the key, key in octave)~~ | 6 | 20.5 / 10.4 (Touch Tone alone is 43) | **Done in 0.9.9** (row 3). Touch Tone 42.9 to 31.9: what is left is its meta-modulation (row 4) |
| 2 | ~~**Filter blend-transpose routes** (a live comb or filter pitch shift)~~ | 8 | 15.9 / 10.6 | **Done in 0.9.9** (live destinations Filter Comb Trans / Filter 2 Comb Trans). Probe: a saw through a comb with an LFO on its transpose, 17.4 to 9.7 dB; across the 8 presets the band error moves by about 1 dB either way, the other gaps in those presets dominate |
| 3 | ~~**Uncarried sources: note (the key) and note in octave**~~ | 13 | 14.6 / 10.5 | **Done in 0.9.9**: an engine LFO in mode Key or Key Oct whose Hi-Res table is the route's curve over the note (point note / 2, or pitch class), so a route's own curve is kept. 8 of the 13 improve (FM Mode 19.7 to 16.1, Swiss Army Knife 7.1 to 5.8, Touch Tone 42.9 to 31.9), 2 get a little worse (Metal Head 10.1 to 12.7: the key now silences its sample at note 60, as the plugin does, which exposes its oscillators; Piano from the yard sale +0.7). Velocity, mod wheel and aftertouch could follow the same way; velocity is folded at 100 today |
| 4 | **Meta-modulation** (a route that scales another route), live | 13 | 13.3 / 10.7 | A modulation-amount destination |
| 5 | **Routes to an effect mix or EQ cutoff** | 38 | 12.3 / 10.2 | Extend the engine's routable effect controls |
| 6 | ~~**Silent oscillator**: level 0 and the route that raises it is not carried~~ | 13 | 12.8 / 10.8 | **Not a gap.** Checked in the presets (0.9.9): 12 of the 13 are oscillators at level 0 with no route at all (silent in Vital too, or used as an FM / RM source); only Complex Electro 1, Horror of Melbourne 1 and Piano from the yard sale have a route that is not carried (an LFO past the four, the mod wheel, an envelope past the slots). The correlation was other gaps in the same presets |
| 7 | **Chorus, flanger and phaser order** (the engine runs them as one block) | 12 | 12.5 / 10.8 | Separate stages in Vital's order |
| 8 | **Live route to oscillator phase-distortion amount** | 13 | 12.3 / 10.8 | Same approach as row 12 |
| 9 | ~~**Envelope routes past the two spare envelopes**~~ | 12 | 12.3 / 10.8 | **Done in 0.9.9**: four routable envelopes (two hidden). Mean 10.84 to 10.79: Piano from the yard sale 27.5 to 23.4, Super Nice Pluck 7.9 to 6.1; DIY and BS_Dark_House slightly worse. Presets with more than four envelope routes (Railgun, Destruction) still drop some |
| 10 | **Macro routes the engine cannot hold** | 28 | 11.8 / 10.7 | Mostly small additions |
| 11 | **LFO or envelope moving the spectral morph amount.** Baked at its saved amount | 27 | 11.7 / 10.8 | Not worth it today (the sweep trial above); a live spectral stage would be the real fix |
| 12 | **Live route to filter drive** | 10 | 11.5 / 11.0 | Make drive a live destination |
| 13 | **Live route to unison detune** | 17 | 9.9 / 11.2 | No measurable effect |
| 14 | **Smaller groups of 5 to 7 presets each:** audio-rate keyboard-following LFOs (approximated), master-volume routes, vocode and Shepard morph types, osc-level routes, LFO-frequency routes, sample random start phase | 5 to 7 each | not measured | Mostly small additions |
| 15 | **Single-preset fit leads:** Phatt_Bass compressor and delay, KEYS reverb and delay, Space Melody envelope to reverb and delay wet, PD type 9, Diode drive about 22 dB hot, Thumpus (its 125 to 250 Hz band is 25 dB low), Sad_Peaky's Analog band-pass at 120 Hz leaks above its passband | 1 to 2 each | per preset | Per-preset measurement |

**Done since the first version of this table:** the Key and Key-in-octave sources, live blend transpose and the oscillator's Key Track switch (0.9.9: mean error over the 100 presets 11.02 to 10.84 dB, the median unchanged at 10.0; the gain is in the few presets that use them), random modulators as a source (0.9.8; 21 of 33 presets use them live), a route into a filter's Blend (0.9.8), the envelope attack curve (0.9.8), a route to the whole voice's pitch (`voice_transpose`, `voice_tune`: live destinations Voice Pitch and Voice Fine, checked against the plugin to 0.3 semitones; 9 presets, which the band comparison cannot see because they are mostly vibrato).

**Re-measured after 0.9.9** (same 100 presets, same method, mean 10.84 dB; groups of at least 4 presets, error with / without the gap, read from the import notes): a filter mixed fully dry at its saved macro value 17.8 / 10.6 (4 presets); chorus, flanger and phaser run as one block 15.6 / 10.6 (4); an LFO into the phaser centre 14.6 / 10.7 (4); a macro into filter 1 drive 14.4 / 10.7 (4); an LFO into oscillator 2's distortion amount 12.9 / 10.7 (5); an audio-rate keyboard-following LFO 12.9 / 10.7 (6); envelope routes past the two spare envelopes 12.6 / 10.6 (12); LFO routes past the four LFOs 12.6 / 10.7 (7); an oscillator at level 0 with nothing to raise it 12.5 / 10.6 (13). The Key, blend-transpose and voice-pitch rows no longer appear. The meta-modulation row (4 above) is not re-counted: its note now also covers the static folds. Next by this order: the silent-oscillator routes (13 presets) and the envelope slots (12).

**How to measure a Vital gap without fooling yourself.** The engine's output is 16-bit with a dither floor of about -93 dBFS. Rendered at a fixed low volume (the harness default, about 30 dB under the plugin) a quiet or dark preset shows that floor as a flat 6 to 16 kHz band, which inflated Moog Pluck from 14.0 to 24.6 dB and 20 - BS - Squares and triangles from 15.9 to 20.7 dB (both among the worst before matching, ordinary after). Match the engine's level to the plugin's first (the volume pot, the master is squared), as `vital_ab.py` does, and compare only the windows where the plugin is sounding.

The other route destinations (osc detune range, unison voices, LFO tempo, EQ and phaser / flanger centres, pan and so on) each touch five presets or fewer; together they reach 59 presets.

VITAL_COVERAGE.md now lists filter-FX key tracking and drive, envelope delay / hold and attack power as carried.

## Reproducing

- Serum: `calibration/census.py` as above writes `calibration/census_<name>.json`; the grouping into pieces is a regex list over the `why` strings (the local script `calibration/dev/unlock_table.py`).
- Vital: convert each `.vital` with `converter/vital_import.py` and read the `notes` list (the local script `calibration/dev/vital_unlock_table.py`).
