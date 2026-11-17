# Roadmap

State on 2026-11-17. The measured Serum facts behind each item are in [TRANSLATION_RULES.md](TRANSLATION_RULES.md); how to work on one is in [DEVELOPMENT.md](DEVELOPMENT.md): find the rule with a Serum probe,
put it in the converter or engine, check with the audits, then judge by many presets, not one.

The project began as "make one third-party pack (Loopsy Deep House) play faithfully" and is now a general Serum 2 importer; most users will not have that pack, so the general test is Serum's own Factory presets. Installed on the development Move: Deep House (70), Factory (363 of
Serum's 626, merged and gated), SynthHacker (48), OPS Hardstyle (14).

## Built since the first roadmap (2026-11-04)

Distortion, compressor, tempo-synced delay, chorus / flanger / phaser, HyperDimension and both FX buses; EQ, distortion, modulation and reverb inside Split / SplitMS branches;
FM / PD / RM / AM warps and static warps; comb, flange and phase voice filters; sample, multisample and **granular** oscillators (loops, scan range, tempo modes, random, unison);
LFO and envelope routes into warps, EQ bands and sample levels; a synced LFO's rate (a 16-beat period at 120 BPM); the arpeggiator's transpose shape; the remote UI (import in the
browser, Osc / Sample / Macros / FX tabs, macros you assign yourself); the Move's Main, Tone, Macros, Effects, Mod FX, Voice and Grain pages; and a long performance pass
(NEON oscillators, grains and convolver, idle racks asleep, a glitch harness).

## Next, biggest effect first

| # | Item | What it takes |
|---|---|---|
| 1 | **Formant voice filter** (FormantONE / TWO / TWB): about 10 of 626 factory presets, and Lead - Kisaya | Measured (`calibration/dev/probe_formant.py`, peak tables in TRANSLATION_RULES); needs an engine filter with three moving formants, key tracked. The importer drops these presets today |
| 2 | **Tempo-following LFOs.** A synced LFO runs at its 120 BPM rate, not at the Move's tempo | An LFO sync option in the engine that follows the host BPM (the delay already does); `map_lfos` sets it only where the file says synced |
| 3 | **Granular gaps**: a warp on the grains (11 of 58 granular presets), a route into Random Pitch / Warp / Position, Random Start, key and velocity zones, a fourth sample slot | probes P8 and the rest in [GRANULAR_PROBE_NOTES.md](GRANULAR_PROBE_NOTES.md); 38 of the 58 have no granular gap left, 32 no gap at all |
| 4 | **Effects**: a second distortion, compressor or delay stage; a filter before the distortion; HyperD, Delay, Comp or Bode in a Split branch; Bode; sends moved by an LFO or envelope | extend `fx.h` and `fxmap.py`. A preset whose only gap is a Bode already imports without it |
| 5 | **Noise- and Filter-1-driven warps**, DLM, spectral tables, Sync Var; the `Comb2` / `Combs` / `L6` comb variants, Diffuser, Reverb and RM filter models | the largest groups among the factory presets the importer drops; each needs a probe first (`census.py` lists what blocks what) |
| 6 | **Reverb build-up**: Serum's wet holds its peak 50 to 100 ms longer; tails are 1 to 3 dB under | a build-up stage in `processReverb`; `calibration/dev/reverb_engine_only.py` measures it without a Serum render |
| 7 | **Performance**: a granular preset in slot 2 was the heaviest on the Move (about 1.3 ms of the 2.67 ms block) | name the preset, then an on-device profile (see the overnight notes in the reports) |
| 8 | **Macro min / max per control** (as Ableton racks): the macro would set the control to a fixed range instead of moving it by the knob's change | state format change, a min / max editor on the Macros tab, and a rule for a control moved by hand. Gives exact part-way ranges and inversion; today's Match knob button goes away |

## Still open from the Deep House pack, cause not found (not rechecked since 2026-11-04)

- Lead - Let It Go, Lead - Rollin, Lead - Space & Time: spectral centroid 1.5 to 2.5 times Serum's on the pack's own MIDI (`reports/2026-11-04-demo-midi-ab.md`).
- Sub-bass 15 to 28 dB too strong at 31 / 62 Hz on Lead - Loop and Lead - Rollin; Self Control's remainder is the reverb's low cut (a three-pole `rev_lo` was neutral across the pack, not adopted).
- Quieter than Serum by 3 dB or more: Lead - Can't Find You, Lead - Let It Go, Lead - Self Control.
- Bass - Gravity "deeper on Serum" (5 to 7 dB under at 0.5 to 1.3 kHz); Lead - Lynnic voice balance; a rapid-note click on one Move render (the attack floor was raised to 3 ms as a guess: find the preset first).

## Housekeeping

- Push the local commits (12 ahead of origin/main).
- Remove the `.vital` intermediate (`to_vital`, `out/vital`) from `converter/deephouse2move.py`, `import_pack.py`, `import_web.py` and `refresh_sample_sets.py`: the converter no longer uses Vital's values.
- Delete the A/B test bank `Deep House UI` on the Move (its presets and the two `Deep House UI` support folders).
- Before making the repo public: a proper fork / upstream remote for the Tablor code, and keep Serum content out (already ignored).
- `reports/` are dated snapshots; `FX_COVERAGE.md` and `MISSING_FEATURES.md` predate most of the effects work.
