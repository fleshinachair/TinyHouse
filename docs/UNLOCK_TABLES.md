# What is left to unlock: Serum and Vital, ranked

Measured on 2026-11-19 with the converter as it stood after the formant filters (Vital Formant and Serum Formant ONE / TWO / TWB, uncommitted at the time). Use it to pick the next piece of engine work: take the top row whose "what it takes" you can build, and check the table again afterwards.

**Read "unlocks" as "has no converter note left", not "cannot be installed".** The census is the converter's verdict only. The real install gate (`converter/import_pack.py`) also renders the preset in Serum with the left-out effects switched off and keeps it when the difference is inaudible, and keeps a Bode-only preset even when it is audible (rule 7b61e4e, 2026-11-15: 21 of 25 Bode-only presets installed; the Factory bank on the Move holds 384). So the rows that are only left-out effects (Bode first, also parts of rows 5, 8, 9) overstate what is blocked; the rows that name an unsupported oscillator, warp or voice filter are real. Tier C (87 presets) is the better guide to audible gaps.

## Serum factory presets (626)

A fresh census (`python calibration/census.py "<Serum 2 Factory folder>" --name factory_now`, convert only, no Serum renders): **364 presets are clean, 262 have at least one "not carried" reason.** The reasons are grouped by me into the pieces below, and a preset often has several, so "touched" is more than "unlocks".

**Unlocks** is greedy: fix the piece that clears the most presets, take those out, repeat. "Cumulative" is the running total of presets with nothing left blocking them.

| # | Missing piece | Presets touched | Unlocks | Cumulative | What it takes |
|---|---|---:|---:|---:|---|
| 1 | **Filter FX models that are not a magnitude response** (Comb, Allpass, Phase, RM, SNH, P12, and others) | 62 | 30 | 30 | A probe per model, then engine stages in the FX filter. Some of the voice-filter comb and phase code can be reused. |
| 2 | **Bode** (frequency shifter): **not worth building.** These presets already install with the Bode left out (see above); measured, it moves the score little. | 64 | 31 | 61 | Nothing |
| 3 | **Warps driven by noise or Filter 1**, DLM, spectral tables, Sync Var | 42 | 25 | 86 | A probe per warp. |
| 4 | **Voice filter models still unsupported** (Reverb1, LPH24, FlangePhase, ...) | 41 | 26 | 112 | A probe per model, mapped to existing or new engine filters. |
| 5 | **Split / Split3 / SplitMS branches** (HyperD, Delay, Comp, Bode, Reverb or Utils inside a band) | 41 | 21 | 133 | Allow more effect types in a band. |
| 6 | **Convolution / Space reverb** | 40 | 17 | 150 | Measure Space reverb. The convolver already exists. |
| 7 | **Granular gaps** (warp on the grains, routes into Warp / Pitch / Position) | 19 | 15 | 165 | Probes P8 and the others in [GRANULAR_PROBE_NOTES.md](GRANULAR_PROBE_NOTES.md). |
| 8 | **Utils** moved by an LFO or envelope, mono-below in a bus, or in a branch | 21 | 15 | 180 | Extend the Utils stage. |
| 9 | **A second Distortion / Comp / Delay / mod effect**, other rack gaps | 21 | 12 | 192 | A second instance of each stage. |
| 10 | **Filter FX model: Diffuser** | 17 | 14 | 206 | One model probe plus a stage. |
| 11 | **Filter FX moved by an LFO or velocity** | 17 | 13 | 219 | The engine's FX filter follows the filter envelope only today. |
| 12 | **MultiSample oscillator** beyond the three sample slots | 10 | 9 | 228 | A fourth sample slot or a different structure. |
| 13 | **Distortion filter morph before the distortion** | 9 | 9 | 237 | The engine's pre-filter has only the two ends. |
| 14 | **Filter FX inside a Split branch** | 6 | 6 | 243 | Part of the Split work in row 5. |
| 15 | **Single warp types** (FM_FILT, PD_FILT, DistTapeSat, SelfPD amounts, Flip, ...) | 19 | 19 | 262 | A probe per warp, one or two presets each. |

Rows 1 to 6 clear 150 of the 262 blocked presets. Serum's Formant ONE / TWO / TWB are done and no longer in the list. Tier counts from the same run: A 211, B 328, C 87.

## Vital presets (100 local: 85 in the Documents folder, 15 in presetshare)

Nothing is blocked: all 100 import. These are gaps in the sound, found from the import notes (`notes` in the converter's result: "not carried: source->destination", "with nothing carried to raise it", and similar). **87 of the 100 have at least one gap**, and most are small. Ordered by presets affected.

| # | Missing or approximate piece | Presets | What it takes |
|---|---|---:|---|
| 1 | **LFO or envelope moving the spectral morph amount.** It is baked at its saved amount, so the route does nothing. | 28 | Sweep the baked table along the amount (`AMOUNT_SWEEP` in `converter/vital_import.py`, off today) or build a live spectral stage. |
| 2 | **Random modulators as a source** (sample-and-hold, perlin) | 24 | Random sources in the LFO engine. |
| 3 | **Routes to pitch** (random, note-in-octave) | 20 | Pitch routes from those sources. |
| 4 | **Filter cutoff routes** (11 from random, 3 from note) | 19 | Follows from random sources in row 2. |
| 5 | **Live route to oscillator phase-distortion amount** | 15 | Same approach as row 1. |
| 6 | **Routes to an effect mix or EQ cutoff** | 15 | Extend the engine's routable effect controls. |
| 7 | **Silent oscillator**: level 0 and the route that raises it is not carried | 14 | Carry those routes. |
| 8 | **Meta-modulation** (a route that scales another route), live | 13 | A modulation-amount destination. |
| 9 | **Filter blend-transpose routes** (the blend route itself is live since the 0.9.8 work) | 13 | Make the blend transpose a live destination. |
| 10 | **Chorus, flanger and phaser order** (the engine runs them as one block) | 12 | Separate stages in Vital's order. |
| 11 | **Envelope routes past the two spare envelopes** | 12 | More envelope slots. |
| 12 | **Live route to unison detune** | 12 | Make detune a live destination. |
| 13 | **Live route to filter drive** | 10 | Make drive a live destination. |
| 14 | **Routes to wave frame** (random, note) | 8 | Follows from random sources. |
| 15 | **Smaller groups of 5 to 7 presets each:** audio-rate keyboard-following LFOs (approximated), master-volume routes, vocode and Shepard morph types, osc-level routes, LFO-frequency routes, sample random start phase | 5 to 7 each | Mostly small additions. |
| 16 | **Single-preset fit leads:** Phatt_Bass compressor and delay, KEYS reverb and delay, Space Melody envelope to reverb and delay wet, PD type 9, Diode drive about 22 dB hot, Phaser filter regression, Space Station comb, Sad_Peaky's Analog band-pass at 120 Hz leaks above its passband | 1 to 2 each | Per-preset measurement. |

The other route destinations (osc detune range, unison voices, LFO tempo, EQ and phaser / flanger centres, pan and so on) each touch five presets or fewer; together they reach 59 presets.

VITAL_COVERAGE.md now lists filter-FX key tracking and drive, envelope delay / hold and attack power as carried.

## Reproducing

- Serum: `calibration/census.py` as above writes `calibration/census_<name>.json`; the grouping into pieces is a regex list over the `why` strings (the local script `calibration/dev/unlock_table.py`).
- Vital: convert each `.vital` with `converter/vital_import.py` and read the `notes` list (the local script `calibration/dev/vital_unlock_table.py`).
