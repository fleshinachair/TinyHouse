# Roadmap: what is left to make the Loopsy Deep House pack play faithfully

State on 2026-11-04. Everything described here is committed; the measured Serum facts behind each item are in [TRANSLATION_RULES.md](TRANSLATION_RULES.md). Work the way DEVELOPMENT.md says:
find the rule with a Serum probe, put it in the converter or engine, check with the audits, then judge by many presets, not one.

## Heard close to Serum (by ear, on the Move or in renders)
Bass - BUNT, Lead - Sleepless, Keys - Catching Flies, Lead - I'll Be Yours, Keys - Loop, Keys - Can't Find You.

## Known gap, cause known (do these first, biggest effect first)

| # | Gap | Presets hit | What it takes |
|---|---|---|---|
| 1 | **Effects rack with SplitMS, EQ inside split branches, and bus-rack effects** | Keys - Other Side, Lead - Other Side (about 13 presets have some bus-rack FX that is not carried) | engine: a mid/side splitter and per-branch EQ stage, then carry the bus rack's remaining effects; converter: map the branches (`fxmap.py`). Largest single item. Lead - Other Side sounds "totally different" today |
| 2 | **Formant voice filter** (FormantONE / TWO / TWB) | Lead - Kisaya, and 10 of 626 factory presets | engine: a filter with three moving formants, key tracked; the shape is measured in `calibration/probe_formant.py` (peak tables in TRANSLATION_RULES). The importer already drops presets that use it |
| 3 | **Tempo-synced LFOs** | Lead - Loop, Pluck - Deep In The Water (the sync flag is often unwritten, so more may be hidden) | probe Serum: LFO Rate at several settings, with and without Triplets, at a known tempo, to learn the Rate-to-beats mapping; add an LFO sync option to the engine that follows the host BPM (the delay already does); have `map_lfos` set it only where the file clearly says synced |
| 4 | **Second distortion / compressor / delay stage, a filter before the distortion** | scattered | engine stages are single today; extend `fx.h` and `fxmap.py` |
| 5 | **Reverb build-up** | every preset with a reverb, mildly | tails are now a median 1 to 3 dB under Serum; Serum's wet also holds its peak 50 to 100 ms longer. A build-up stage in `processReverb`, sized from Size. Use `calibration/reverb_engine_only.py` to measure it without re-rendering Serum |

## Known gap, cause not found

- **Lead - Let It Go, Lead - Rollin, Lead - Space & Time**: spectral centroid 1.5 to 2.5 times Serum's on the pack's own MIDI (`reports/2026-11-04-demo-midi-ab.md`).
- **Sub-bass 15 to 28 dB too strong at 31 / 62 Hz** on Lead - Let It Go, Lead - Loop, Lead - Rollin, Lead - Self Control. Let It Go and Self Control were routes saved with `kParamOut` (an output scale in percent) that the converter ignored: fixed (`calibration/probe_route_out.py`; Let It Go 40-80 Hz -19 -> -84 dB re body against Serum's -57, band rms 22.5 -> 12.8). Loop and Rollin are not that cause (both now within 4-10 dB). What is left of Self Control's sub-bass is the reverb: its Freq (a low cut in Hz, steeper than the engine's one-pole `rev_lo`, never mapped) matters, but a three-pole `rev_lo` was neutral across the pack (`calibration/probe_reverb_lowcut.py`), so it was not adopted.
- **Quieter than Serum by 3 dB or more**: Lead - Other Side (-6.9, see item 1), Lead - Can't Find You, Lead - Let It Go, Lead - Self Control. Not the reverb.
- **Bass - Gravity** "deeper on Serum": the engine is 5 to 7 dB under at 0.5 to 1.3 kHz and 4 to 11 dB over at 2.5 to 5 kHz; no cause found.
- **Lead - Lynnic** voice-section balance (hiss against horn).
- **Rapid-note click** heard on a Move render: the cause is not known. The attack floor was raised to 3 ms as a guess. Find which preset and moment first.
- Quick-note presets step harder at note starts than Serum does (I'll Be Yours 9.4 vs 2.6).

## Housekeeping

- Install the staged build on the Move (reverb gain, the 3 ms attack floor, Keys - Loop) with `deploy_all.sh`.
- Remove the `.vital` intermediate in `deephouse2move.py` (`to_vital`, the `out/vital` files) and `import_pack.py`: the converter no longer uses Vital's values, only the `serum2move` dict carried in it. (The old Vital check scripts are already gone.)
- Prune `schwung-tablor/src/wavetables` (21 MB, Tablor's bundled third-party packs) after checking `factory.tbl` and the licences.
- Before making the repo public: use a proper fork / upstream remote for the Tablor code, and keep Serum content out (already ignored).
- `reports/FX_COVERAGE.md` and `MISSING_FEATURES.md` predate the compressor, distortion, LFO and bus work.

## Beyond the Deep House pack

Other packs and the factory presets are limited by the same list: the importer's own skip notes say why each preset was dropped. The largest groups among the 626 factory presets
are unsupported warps (DistTube, FM, PD, RM), MultiSample oscillators, and filter models other than the common ladder / H / L.
