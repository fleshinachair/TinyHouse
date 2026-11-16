# calibration/

Most of this folder is the workbench that the converter's rules came from. **You do not need any of it to import presets with the Python scripts.** This page says which files matter.

## Needed to import with `import_pack.py`

These are loaded when you run the importer, together with the converter in `../converter/` (`import_pack.py`, `deephouse2move.py`, `fxmap.py`, `fxmetrics.py`, `sample_export.py`, `serum_paths.py`) and `test_render_calibration.py`, the render harness that lives here:

| File | What it does |
|---|---|
| `fit_chain.py`, `fit_level.py`, `fit_perceptual.py`, `post_fit.py` | match a preset's loudness to the Serum render and score how close it is |
| `comp_rescale.py` | keeps compressor thresholds right when the volume moves |
| `voice_survey.py` | the octave-band measure the sound check uses |
| `fx_fingerprint.py`, `fx_probe.py` | measure a preset's reverb, delay and width in Serum |

It also reads the fitted-law files (`*.json`: filter models, LFO scales, warp curves, reverb and width laws and so on). They are results, already in the repo, and are not rebuilt when you import.

The plugin goes silent after about 1200 s of rendered audio per instance, so the importer reloads it before then. That is built into these files; you do not run anything extra. If a run is stopped for any reason, run the same command again with `--resume`.

## Also useful

- `census.py DIR --name NAME` checks a folder of presets without rendering anything in Serum and writes `census_NAME.json`: how many would import cleanly and what blocks the rest. It is quick (a few seconds per preset the first time, much less after).
- `add_macros.py` adds macro routes to an installed bank (used by `deploy_macros_bank.sh`).

## Developer tools: `calibration/dev/`

Everything in [dev/](dev/) is for changing the converter or the engine, not for using it:

- `probe_*.py` play controlled test patches through Serum and record what it does (one setting at a time).
- `fit_*.py`, `calibrate_*.py`, `hyperd_fit.py` turn those measurements into the `*.json` laws.
- `validate_*.py`, `*_audit.py`, `check_*.py`, `stage_audit.py` check a rule against Serum after a change.
- `fill_refs.py`, `fill_fx_refs.py`, `midi_ab.py`, `demo_compare.py`, `transcribe.py` and similar fill the Serum render cache or compare against a pack's own MIDI or recordings.

Run any of them from the repo root, for example `python calibration/dev/probe_lfo.py`; each one's header says what it writes.

## Glitch harness (CPU on the Move)

`glitch_harness.cpp` renders several TinyHouse slots one after another on one thread, as the Move's audio callback does, and times every 128-sample frame. A glitch is a frame
that runs past the 2667 us budget (less what the host takes, about 200 us), so it reports the worst frames, not just the mean.

    schwung-tablor/scripts/zig-build.sh arm            # builds the dsp; for the harness:
    zig c++ -target aarch64-linux-gnu.2.35 -O3 -std=c++17 -Ischwung-tablor/src -Ischwung-tablor/src/dsp -Ischwung-tablor/src/ported -Ischwung-tablor/src/host \
        calibration/glitch_harness.cpp schwung-tablor/src/dsp/engine.cpp schwung-tablor/src/ported/wavetable.cpp schwung-tablor/src/ported/audiofilter/ParametricCreator.cpp -o glitch_harness
    ./glitch_harness 10 all "/data/UserData/schwung/presets/tinyhouse/Factory/SC - Sitar in the Mist.json@48,55,60" ...    # on the Move; each argument is one slot

Scenarios: `idle`, `steady`, `retrig` (a chord every 0.5 s), `stack` (notes added up to the whole list), `arp`, `split` (each frame in three short calls, as the demo mode does).
`GH_TRACE=2200` lists the frames slower than 2200 us. `tablor_cli` accepts a preset `.json` as well as a raw blob, so the same files render to audio for A/B null tests.
Two things learned with it (2026-11-16): an idle slot with effects costs about 180 us a frame (now asleep after silence, see `gatedFx` in engine.cpp), and two renders of
the same preset from builds that differ only in float contraction already differ by -60..-70 dB, so a null test only shows errors above about -60 dB.
