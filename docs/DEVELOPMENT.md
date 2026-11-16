# TinyHouse: how it is built and how to work on it

The user guide is the [top-level README](../README.md). This page is for people working on the converter, the engine and the calibration.

TinyHouse converts Serum 2 presets (`.SerumPreset` / `.SerumPack`) into presets for **tablor-dh**, a modified build of
[Tablor](https://github.com/athousanddetails/schwung-tablor) (a wavetable synth module for the Move's [Schwung](https://github.com/charlesvestal/schwung)
host).

**The initial aim** was narrow: make the Loopsy *DEEP HOUSE* Serum preset bank (70 presets) play faithfully on the Move. That bank is the
reference the engine, the converter and the calibration were built and measured against, and it is still the main test of whether a change helps.
The project then grew into a general goal: a **drop-in importer** that takes a folder of any Serum presets and gives back a Move bank, with every
setting translated from what Serum itself does, not tuned by ear per preset. That is why no preset is fitted individually: a rule that only fixes
one Deep House preset would not carry over to other packs.

```
 Serum 2 preset ──► deephouse2move.py / import_pack.py ──► tablor-dh preset (.json) + wavetables + sample sets
   (.SerumPreset)        (translate, measure, level)                       │
        │                                                                  ▼
        └── rendered headlessly in Serum (DawDreamer) ──► compared with the engine's render ──► Ableton Move
```

## Where it stands

| | |
|---|---|
| Engine module | `schwung-tablor/`, version `1.11.0-th`, 315 parameters, ARM `dsp.so` built with zig |
| Deep House bank | 70 presets (the Loopsy *Deep House* pack), all converted; none dropped |
| Factory Picks bank | 25 presets from Serum's 626 factory presets (37 tried, 12 dropped by the quality gate) |
| Fidelity, Deep House | mean full-chain spectral loss about 14 (lower is better; it moves by +-4 between renders of the same state because unison phases are random); level within 0.9 dB of Serum on average |
| Fidelity, Factory Picks | octave bands 9.2 dB rms from Serum on average, level within 1.2 dB |
| By ear | a first ear A/B against Serum (same-player WAV pairs, `calibration/dev/midi_ab.py` for the pack's demo MIDI): Bass - BUNT and Lead - Sleepless sound great, Catching Flies and I'll Be Yours are close; Lynnic is less brassy and Kisaya less vocal than Serum (Kisaya's formant filter is not built; Lynnic's LFO pitch route was over-deep and is fixed in the staged set, not yet auditioned) |

## What is supported

**Presets.** Serum 2 only (decoded with `serum2-preset-loader`; tested on a 2.0.24 pack, unit curves measured on 2.1.5). A preset is converted only
if the engine can carry everything audible in it (`unsupported_reasons` in `deephouse2move.py`); `import_pack.py` writes a `skipped_<bank>.md`
saying why each rejected preset was dropped. Packs with most of their presets in the supported set import best: basses, leads, keys, plucks and pads
built on wavetables.

**Oscillators and voice.**
- Three wavetable oscillators with unison up to 16 voices, plus a sub and a noise oscillator (white, pink, brown and Serum's colour filters).
- Wavetable position is stepped or interpolated per table, as Serum does it (the WAV `clm ` chunk).
- Warps: bend, PWM, sync, even/odd, the filter warps and the FM, RM, PD and AM warps driven by the Sub or by another oscillator, with two warp slots per oscillator. Not carried: warps driven by the noise or by Filter 1, DLM, spectral tables, Sync Var.
- Sample and multisample oscillators, from SFZ or audio, as `.tbs` sample sets (`sample_export.py`). A Sample oscillator's level follows the wavetable law with no offset (measured within 0.3 dB over six files spanning 22 dB of recorded level); a Multisample plays 7.3 dB louder in Serum, carried in each region's volume (`calibration/dev/probe_sample_level.py`, `probe_multisample_level.py`). Warps, unison and their own release are not modelled.
- Two voice filters, in parallel with a balance control or in series. The common ladder / H / L models are matched, and so are the notch, band-reject, Exp, Scream, RM and DJMixer models (`calibration/dev/fit_filter_model.py` finds the closest engine filter and cutoff; the importer's A/B gate judges the rest). Formant, comb, flange, phase and reverb models are not: they are not a plain low / high / band / notch response, so they need new engine filters (comb and flange / phase are the largest group, about 17 of the 52 presets that name an unsupported model; only 15 of those 52 are blocked by it alone).
- Envelopes, 4 LFOs, and the modulation routes of macros, velocity, note number, mod wheel and random sources. Macros, velocity and note are folded
  at their saved values (there is no live macro); a route gated by something that is closed at rest is switched off, as it would be in Serum.
- Source routing: filter, master, direct (past the effects) and none, and Serum's outputs of Filter 1 / Filter 2 as sources.

**Effects.** The engine's effect chain runs in this order. Each stage is calibrated on Serum with controlled probe patches (`calibration/probe_*.py`).

| # | Stage | Serum effect | Notes |
|---|---|---|---|
| 1 | Distortion | Distortion | tanh / biased tanh / hard clip / sine curves measured per mode and drive; Serum's wet law (smoothstep, with dry kept) and level out; one stage only, the strongest in the rack. Poor fits: Diode2, Asym at high drive, SineShaper |
| 2 | Filter FX | Filter | static or following the filter envelope; LP / HP / band / notch with Serum's pole counts |
| 3 | Compressor | Comp | single band and 3-band multiband; thresholds follow the master gain; one stage only |
| 4 | Mod FX | Chorus, Flanger, Phaser | real modulated stages; rate / depth defaults were guessed, not measured |
| 5 | Width | HyperDimension | spread and level |
| 6 | Delay | Delay | tempo-synced to the Move's BPM, band-pass in the feedback, ping-pong; one delay only |
| 7 | Reverb | Reverb, convolution reverb | measured per preset (size, decay, damping). Tails on played notes now run about 1 to 3 dB under Serum's (see Open issues) |
| 8 | EQ | EQ | four bands, with Serum's static Filter FX folded in as bands |
| 9 | Tone and master | Utils | master width, mono-below, soft output clipper |

Two **FX buses** each hold a full copy of this rack. Sources and filter outputs have per-bus sends (`level^3`, as in Serum); the bus output joins the
main path ahead of the main rack.

**Not carried:** a second distortion or compressor stage, a second delay, SplitMS branches holding anything but EQs (EQs in the Mid / Side branches are the engine's mid/side EQs), band-split (Split) branches holding anything but a Distortion or Chorus / Flanger / Phaser (those are the engine's band stage),
Bode frequency shifter, Utils width inside a bus rack, a filter placed before the distortion, sends moved by an LFO or envelope.

## Repo layout

| Path | What it is |
|---|---|
| `converter/deephouse2move.py` | the converter: Serum preset to engine parameters. Every translation rule has a comment saying what was measured |
| `converter/import_pack.py` | import a folder of Serum presets into a bank: convert, drop what cannot be carried, set loudness from a Serum render, gate on the result |
| `converter/fxmap.py`, `converter/fxmetrics.py` | effect mapping (rack to engine stages) and the metrics used to fingerprint Serum's effects |
| `converter/sample_export.py` | Serum sample / multisample oscillators to `.tbs` sample sets |
| `calibration/` | the measurement harness: probes against Serum, per-stage audits, chain report, level fits. `probe_*.py` find Serum's rules; `fx_audit.py`, `stage_audit.py`, `chain_report.py` check the result |
| `schwung-tablor/` | the engine (a fork of Tablor): `src/dsp/` is the voice, FX and plugin; `tests/` are native tests |
| `deploy_all.sh`, `deploy_presets.sh`, `deploy_wavetables.sh`, `deploy_samples.sh` | install a bank on the Move over SSH, with checksums and a rollback copy |
| `docs/TRANSLATION_RULES.md` | the measured Serum facts and the translation rules built on them |
| `converter/serum_paths.py`, `requirements.txt` | where Serum is found (environment variables override), and the Python packages |
| `calibration/dev/midi_ab.py` | play a preset's own demo MIDI through Serum and through TinyHouse and compare (writes paired WAVs); `reports/2026-11-04-demo-midi-ab.md` has the results for the Loopsy pack |
| `reports/` | what each round of work changed and measured. `2026-11-04-*.md` are the newest; `FX_COVERAGE.md` and `MISSING_FEATURES.md` predate the compressor, distortion, LFO and bus work and are out of date |

## Working on it

**The rule.** Presets are translated from Serum's own settings. No per-preset fits against a loss function, no special-casing a preset by name. Allowed:
a global constant measured with a controlled Serum probe patch, and a per-preset loudness fit. A fix that helps one preset and does not transfer to a
new pack is not a fix. The workflow is: find the rule with a probe in `calibration/probe_*.py`, put it in the converter or engine, check it with the stage
audit, then look at the chain report across all presets (judge that by many presets, not one: it is noisy).

**You need** Windows, Serum 2 (the plugin, for the reference renders), Python with the packages in `requirements.txt` (`pip install -r requirements.txt`), a zig compiler for the
ARM build, and an Ableton Move running Schwung. Serum's location is found by `serum_paths.py`: it looks in `~/Documents/Xfer/Serum 2 Presets` (and the
OneDrive-redirected equivalent). If yours is elsewhere, set the `SERUM_ROOT` environment variable to that folder, and `SERUM_VST` to the plugin if it is
not at `C:\Program Files\Common Files\VST3\Serum2.vst3`.

**Working with others:** see [CONTRIBUTING.md](../CONTRIBUTING.md) (branches, what a change must show, what never to commit).

**Read first:** [docs/TRANSLATION_RULES.md](TRANSLATION_RULES.md) holds every fact measured on Serum so far (units, curves, routing, effect laws) and
why each rule exists. Read it before changing a rule.

**Typical loop**
```
python converter/import_pack.py "<folder of .SerumPreset>" --bank "My Pack"      # writes out_import/My Pack/ and its wavetables
BANK="My Pack" sh ./deploy_wavetables.sh
sh ./deploy_samples.sh
SRC="out_import/My Pack" BANK="My Pack" sh ./deploy_presets.sh          # the bank then shows on the Bank knob (8th knob of the Main page)
bash schwung-tablor/scripts/zig-build.sh arm                            # after an engine change; then schwung-tablor/scripts/deploy.sh
```
`deploy_all.sh` installs a whole bank and the module, and runs the on-device load test. Never overwrite the live `dsp.so` in place (the host maps it):
`schwung-tablor/scripts/deploy.sh` uploads under a temporary name and renames.

## Open issues

Ordered by how much they matter for the Deep House pack; [docs/ROADMAP.md](ROADMAP.md) has the full list with what each one takes.

- **Effects racks with a Comp / Distortion inside a SplitMS branch, band-split (Split) branches and bus-rack Mono-below**: not carried. EQs inside SplitMS branches are carried (mid/side EQ stage).
- **Formant voice filters** (FormantONE / TWO / TWB) are measured (`calibration/dev/probe_formant.py`, `docs/TRANSLATION_RULES.md`) but not built; the importer drops presets that use them.
- **Tempo-synced LFOs** are read as Hz (Lead - Loop, Pluck - Deep In The Water); needs a Serum probe of the Rate scale and an engine sync option.
- Unsupported warps (the noise-driven and Filter-1-driven FM, RM and PD, DLM), non-wavetable oscillator kinds, Bode, a filter before the distortion, and filter-model gaps are the largest groups among the factory presets the importer drops.
- Second distortion / compressor / delay stage; chorus / flanger / phaser default rates and depths; delay and reverb corner cases are listed in the notes next to each rule.
- **Reverb tails (mostly done).** The wet gain is now 2.25 (was 1.50), which brings the median tail of a played note to within 1 to 3 dB of Serum's (it was 2 to 5 dB under). A hotter
  gain would centre it but puts steady noise too loud. Still open and minor: Serum's wet holds its peak 50 to 100 ms longer, and the spread between presets is 4 to 6 dB, so a
  build-up stage in `processReverb` (sized from Size) may still help. `calibration/dev/reverb_engine_only.py` measures it without re-rendering Serum.


## Licences and what is not here

The engine is BSD-3-Clause (Tablor, FigBug's Wavetable and gin, AudioFilter, dr_flac: see `schwung-tablor/LICENSE`).

Serum presets, packs, wavetables and samples are Xfer's and the pack authors', and **are not redistributed**. The repo does not contain any `.SerumPack`,
`.SerumPreset`, Serum wavetable, `.tbs` sample set or Serum render. Bring your own and keep the outputs (`out/`, `out_*/`, `calibration/refs/`) local.
Check the licence of a pack before sharing the presets you convert from it.
