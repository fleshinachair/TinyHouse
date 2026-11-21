# Roadmap

State on 2026-11-20 (0.9.11 is released, 0.9.10 the one before; 0.9.9 before that; 0.9.9 lost saved sounds in Schwung sets, fixed in 0.9.10). The measured Serum facts behind each item are in [TRANSLATION_RULES.md](TRANSLATION_RULES.md); how to work on one is in [DEVELOPMENT.md](DEVELOPMENT.md): find the rule with a Serum probe,
put it in the converter or engine, check with the audits, then judge by many presets, not one.

The project began as "make one third-party pack (Loopsy Deep House) play faithfully" and is now a general Serum 2 importer; most users will not have that pack, so the general test is Serum's own Factory presets. Installed on the development Move: Deep House (70), Factory (363 of
Serum's 626, merged and gated), SynthHacker (48), OPS Hardstyle (14).

## Road to 1.0

1.0 means a stranger can install the release, import their own Serum 2 or Vital presets, and trust it. It does not mean every Serum feature. The gates:

1. **Saved state keeps loading.** From 1.0 a format change only adds keys (their defaults keep the old sound) and keeps reading the old ones; nothing already saved changes meaning. Macro min / max (#8 below) fits that rule, so it no longer blocks 1.0: a `mac` route converts on load. Each release is checked by loading saved presets from earlier ones.
2. **Correct at any tempo.** A synced LFO follows the Move's BPM (#2), not 120.
3. **Safe to upgrade and to run.** An upgrade from 0.9.1 and 0.9.5 with old saved presets still loading, and the heaviest preset profiled with a CPU ceiling (#7). The release tarball is unpacked and listed before it is published; the CPU ceiling is in the README's Known limits.
4. **The importer is honest.** A dropped preset or feature is reported with its reason; the formant filter (#1) closes the largest group of silent drops.
5. **Docs and licence.** README, in-module help and a how-to-import walkthrough with screenshots; the GPL text and THIRD-PARTY.md stay in the package.

| Release | Content |
|---|---|
| 0.9.6 | Done: tempo-following LFOs, Vital fixes (filter drive law, Filter FX drive, Bit Crush, fine-pitch routes, 64-point LFO shapes, oscillator start phase), `tablor_loadtest` dropped from `deploy.sh` |
| 0.9.7 | Done, shipped inside 0.9.8: formant voice filter, Vital and Serum models (new filter types and params only, so old presets load unchanged) |
| 0.9.8 | Done: the cheap Vital leads: attack power, live filter Blend routes and random modulators as live sources (it also carries the 0.9.7 formant filters); the Phaser "regression" is not the filter (see below); `lfo_N_stereo` is not cheap (left and right need separate oscillator renders). The importer lists every dropped preset and feature: the browser report now lists Vital's small details too |
| 0.9.9 | Released (it has the saved-state bug, fixed in 0.9.10). In it: Vital voice-pitch routes, Key and Key-in-octave route sources, live comb blend transpose, an oscillator's Key Track switch (moved up from 1.1: they were the largest measured Vital gaps). Also done: Velocity and Aftertouch as live route sources, the CPU note in the README, a plainer Installing section, the import screenshot. 1.0 follows when nothing in the gates above is open |
| 1.0 | Every gate above holds |

After 1.0: the plan below, until every row of [UNLOCK_TABLES.md](UNLOCK_TABLES.md) is closed or marked not worth it.

## After 1.0: closing the unlock tables

Each release takes the top rows of [UNLOCK_TABLES.md](UNLOCK_TABLES.md) it can build, then the census is run again (`calibration/census.py`, `calibration/dev/vital_unlock_table.py`) and the table is rewritten, so the counts stay true. Format changes only add keys (gate 1). A row is closed when the census no longer lists it, or marked "not worth it" with the measurement that says so (Bode is the first: those presets already import without it). Order is by presets affected, then by how much of the work is shared. Rows that are only left-out effects overstate what is blocked (see the note at the top of the table): check an A/B render before building one.

| Release | Theme | Table rows |
|---|---|---|
| 1.1 | **Vital modulation, in the order the measured error gives** (the table's "error with / without"): a route that scales another route, effect-mix and EQ routes, then the rest. Random sources, voice pitch, the Key and Key-in-octave sources and live blend transpose are done (0.9.8 and 0.9.9); the spectral morph amount is not worth building yet (a trial changed nothing) | Vital 4 to 10 |
| 1.2 | **Serum filters and warps.** Voice filter models still unsupported (Reverb1, LPH24, FlangePhase), the warps driven by noise or Filter 1 (DLM, AM_NOISE), the single warp types; a probe each | Serum 3, 4, 15 |
| 1.3 | **Effects.** Filter FX models that are not a magnitude response (a probe each), Diffuser, Filter FX moved by an LFO or velocity, the distortion filter morph, a second Distortion / Comp / Delay, Utils moved by a modulator, more effect types inside a Split band; for Vital the chorus / flanger / phaser order and routes to an effect mix or EQ cutoff; tempo-following mod effects | Serum 1, 5, 8, 9, 10, 11, 13, 14; Vital 6, 10 |
| 1.4 | **Sample and space.** Convolution / Space reverb, a fourth sample slot (MultiSample), the granular gaps (warp on the grains, routes into Warp / Pitch / Position), reverb build-up | Serum 6, 7, 12 |
| 1.5 | **Per-channel and per-note detail.** `lfo_N_stereo` (oscillators rendered separately for left and right), macro min / max (#8), noise-driven warps left over, the Deep House leads | Vital 15; roadmap #8 |
| Ongoing | **Single-preset leads** (Vital row 16, the Deep House leads below): taken when a preset is reported, never in a release of their own | Vital 16 |

## Built since the first roadmap (2026-11-04)

Distortion, compressor, tempo-synced delay, chorus / flanger / phaser, HyperDimension and both FX buses; EQ, distortion, modulation and reverb inside Split / SplitMS branches;
FM / PD / RM / AM warps and static warps; comb, flange and phase voice filters; sample, multisample and **granular** oscillators (loops, scan range, tempo modes, random, unison);
LFO and envelope routes into warps, EQ bands and sample levels; a synced LFO's rate (a 16-beat period at 120 BPM); the arpeggiator's transpose shape; the remote UI (import in the
browser, Osc / Sample / Macros / FX tabs, macros you assign yourself); the Move's Main, Tone, Macros, Effects, Mod FX, Voice and Grain pages; and a long performance pass
(NEON oscillators, grains and convolver, idle racks asleep, a glitch harness).

## Vital import (0.9.5): open leads

The Vital front end (`converter/vital_import.py`) runs on the same engine; 85 presets from the development folder convert with no errors and sound close. Measured laws and the A/B method are in `docs/VITAL_COVERAGE.md`. Still open:

- Phatt_Bass: the multiband compressor's per-band boost differs from Vital's (top end 3 to 6 dB under) and the delay's side signal is about 5 dB low. KEYS - The House: more reverb and delay in Vital. Space Melody: envelope routes into reverb / delay wet are not carried. PD type 9 (FM from the sample) on Faded Chords / Haven Chords. The Diode filter's drive is still about 22 dB hot.
- The Phaser filter matches the plugin to 1 to 2 dB on its own (`vital_vfilter_probe.py --model 7`); Thumpus (8.5 to 12 dB octave rms) is wrong elsewhere: its 125 to 250 Hz band is 25 dB low and its highs 10 to 16 dB hot with the second filter off as well, so the lead is in the oscillators or a modulation, not the filter. Thumpus also has a Filter FX wet-level lead.
- Unison morph spread, frame / distortion spread with unison, and Space Station's comb 2 path and dry oscillators.
- Filter FX steepness, `lfo_N_stereo` (the left and right LFO phases differ by the stereo amount; needs per-channel oscillator renders), the spare-envelope route limit, the delay-time route (Whipple), Diode and other model blends.
- Waterfall card legibility when waves nest (the scrolled-to frame is highlighted; fewer rows or dotted back rows are options).

## Next, biggest effect first

For the measured ranking of what still blocks the Serum factory presets (and what Vital gaps remain), by presets unlocked, see [UNLOCK_TABLES.md](UNLOCK_TABLES.md); pick engine work from its top rows.

| # | Item | What it takes |
|---|---|---|
| 1 | **Formant voice filter** (FormantONE / TWO / TWB): about 10 of 626 factory presets, and Lead - Kisaya | Measured (`calibration/dev/probe_formant.py`, peak tables in TRANSLATION_RULES); needs an engine filter with three moving formants, key tracked. The importer drops these presets today |
| 2 | ~~**Tempo-following mod effects.**~~ Done in 0.9.12: `chor_sync` / `flan_sync` / `phas_sync` scale the rate by host BPM / 120; the Vital converter sets them where the file says synced | |
| 3 | **Granular gaps**: a warp on the grains (a real one: FM / PD / AM / filter types have no phase to act on in a sampled grain, so each needs a probe), a route into Warp / Window Param / Random Pan, Random Start, key and velocity zones. LFO / envelope routes into Start, Position and Random Pitch are in 0.9.12; the fourth sample slot is dropped (the MultiSample presets were unresolved SFZ paths) | probes P8 and the rest in [GRANULAR_PROBE_NOTES.md](GRANULAR_PROBE_NOTES.md); 38 of the 58 have no granular gap left, 32 no gap at all |
| 4 | **Effects**: a second compressor or delay stage; HyperD, Delay, Comp or Bode in a Split branch; Bode; sends moved by an LFO or envelope (a second distortion and the Distortion filter's LP-BP-HP morph are in 0.9.12) | extend `fx.h` and `fxmap.py`. A preset whose only gap is a Bode already imports without it |
| 5 | **Noise- and Filter-1-driven warps**, DLM, spectral tables (**Spectral oscillator resynthesis**: 29 factory presets still wait for it; 9 that hardly scan are installed as 0.9.12 approximations, playing the sample they hold with no scan or spectral filter, and need redoing once the stage exists), Sync Var; the `Comb2` / `Combs` / `L6` comb variants, Diffuser, Reverb and RM filter models | the largest groups among the factory presets the importer drops; each needs a probe first (`census.py` lists what blocks what) |
| 6 | ~~**Reverb build-up**~~ Tried in 0.9.12 and dropped: a longer input diffusion moves the median tail (-3.0 to +3.9 dB) but the median error stays 4 to 7 dB, because the spread is between presets, not a bias. `calibration/dev/reverb_engine_only.py` measures it; the Serum reference is `calibration/reverb_stage_audit.json` (re-rendered 2026-11-20) | |
| 7 | **Performance**: a granular preset in slot 2 was the heaviest on the Move (about 1.3 ms of the 2.67 ms block) | name the preset, then an on-device profile (see the overnight notes in the reports) |
| 8 | **Macro min / max per control** (as Ableton racks): the macro would set the control to a fixed range instead of moving it by the knob's change | after 1.0, additive: a new `macr` key (`macro:key:min:max`), old `mac` routes converted on load from the saved macro and control values (identical sound), a min / max editor on the Macros tab, last touch wins for a control moved by hand, a control on several macros still adds each macro's change (about 13% of presets). Gives exact part-way ranges; today's Match knob button goes away. Inversion already works (negative travel) |

## Not on the roadmap

Ideas that were looked at and put aside; each is taken only if someone asks for it.

- **Wavetable lists past 128 on the Move.** The host caps an enum at 128 options, so the WT1 to WT3 Table knobs on the Move show only the first 128 tables of the library or pack (the development Move has 346). Imported presets load tables by name and the remote UI's list is separate, so only choosing a table by hand on the hardware is affected. Fix if ever wanted, additive: a page param per table knob (default page 0, so old presets load unchanged), or split big packs into sub-packs by letter.
- **A browser demo.** The engine built as WebAssembly with the remote UI and importer, so people could import presets and play them without a Move. Feasible (the importer already runs in the browser); needs synchronous loads instead of the loader threads, an in-memory file layer, an AudioWorklet and licence-clean demo presets. Cheapest first step: build the engine to wasm and render one note in Node. It would also give the remote UI a real play level.

## Still open from the Deep House pack, cause not found (not rechecked since 2026-11-04)

- Lead - Let It Go, Lead - Rollin, Lead - Space & Time: spectral centroid 1.5 to 2.5 times Serum's on the pack's own MIDI (`reports/2026-11-04-demo-midi-ab.md`).
- Sub-bass 15 to 28 dB too strong at 31 / 62 Hz on Lead - Loop and Lead - Rollin; Self Control's remainder is the reverb's low cut (a three-pole `rev_lo` was neutral across the pack, not adopted).
- Quieter than Serum by 3 dB or more: Lead - Can't Find You, Lead - Let It Go, Lead - Self Control.
- Bass - Gravity "deeper on Serum" (5 to 7 dB under at 0.5 to 1.3 kHz); Lead - Lynnic voice balance; a rapid-note click on one Move render (the attack floor was raised to 3 ms as a guess: find the preset first).

## Housekeeping

- The repo is public. A proper fork / upstream remote for the Tablor code is still to do. History checked on 2026-11-17: 222 commits by one author, no Serum pack, preset, wavetable, sample or render ever committed (only screenshots), no keys or tokens; the Serum content stays out through `.gitignore`.
- `reports/` are dated snapshots; `FX_COVERAGE.md` and `MISSING_FEATURES.md` predate most of the effects work.
