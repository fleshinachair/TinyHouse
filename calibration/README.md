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

- `add_macros.py` adds macro routes to an installed bank (used by `deploy_macros_bank.sh`).

## Not in the repository

The probe scripts that produced the measured laws (the Serum and Vital probes, the fitting and audit scripts, the glitch harness) are kept local. Their results are the `*.json` files here.
