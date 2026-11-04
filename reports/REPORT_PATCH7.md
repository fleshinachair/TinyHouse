# Patch 7: preset page, denormal fixes, Filter FX and Utils, per-preset width, demo recreation

Installed on the Move: module 1.9.0-th (checksum verified) and the 70 presets (checksums verified, rollback copies in
`/data/UserData/tinyhouse-rollback-p11/`, `-p12/`, `-p12b/`). Reload the slot to load the module. Nothing is committed.

## Engine / module (schwung-tablor)
- **Preset page** (TinyK pattern): the hierarchy root declares `list_param preset`, `count_param preset_count`, `name_param
  preset_name`; the DSP serves them from the .json files in Schwung's store (parsed tolerant of the converter's spaces),
  choosing one loads its state. Pages are levels under it: Main, Tone, Effects. On-device load test covers it (count, load,
  clamp). The stock-sound checks in `tools/loadtest.cpp` are skipped when the stock Tablor sounds are absent.
- **Denormals / crackle**: FPCR.FZ set at the start of each audio callback and restored on return (`tablor_plugin.cpp`);
  `sanitize()` (flush below 1e-15) on the delay/reverb feedback, reverb allpasses and every biquad state (`fx.h`). **Measured, it was not
  denormals in the old build**: `tools/tailbench.cpp` on the Move showed no deep-tail slowdown (0.03-0.18 ms mean per block against
  2.9 ms), and no click at release in the engine's signal on any preset. `-ffast-math` is NOT active (`zig c++ ... -O3 -std=c++17`)
  and was not added: it removes the NaN/inf checks several tests rely on and adds nothing once FZ is set.
- **Master width and Mono Below** (`mwidth`, `monohz`; no page, set by the converter): scales the side channel (0 mono, 64 as is,
  127 about 2x); the side high-passed at 40-500 Hz keeps the low end central. Neutral skips the stage.
- Tests: native and ARM, 0 failures (new: stereo; the mono-below threshold was loosened because it depends on random unison
  phases, which differ between this PC and the Move).
- **Bass - InDeep click on quick notes**: reproduced (`calibration/retrig_probe.cpp`) and Serum does the same: step at the new
  note 55x the normal step in Serum, 60x here, same at the same pitch. It is the patch's fast filter/mod envelopes retriggering over a
  held note. A change to the mono retrigger measured worse and was reverted by hand (a `git checkout` would have lost the
  uncommitted work in those files).

## Converter / fits
- **Filter FX -> EQ bands** (`fxmap.filter_bands`): static LP/HP Filter FX (Serum cutoff scale 8.18 Hz x 2697^x, macros folded in at
  the saved position, wet >= 50 %) become EQ bands. 27 presets have a Filter FX in the main rack (29 nodes): 15 carried, 5 skipped
  (moved by envelope/LFO), 5 not needed (wet 0/40 % or fully open), 2 not possible (Comb/DJ-mixer, inside a Split branch).
  An untyped Filter was assumed to be a 24 dB low-pass; that assumption is probably wrong (Keys - Zerb and Lead - Sleepless got worse).
- **Utils -> master width / mono-below** (`fxmap.utils_stereo`): 11 presets.
- Fit pipeline re-run from scratch (`calibration/run_fits.sh`: fit_chain, fit_tune, fit_level). New fit scripts: `fit_tune.py` (amp
  envelope, shelves, Drive), `fit_stereo.py` (converter-side width), `band_profile.py`, `fx_contribution.py` (what each missing effect
  is worth, asked of Serum), `chain_report.py` (before/after against Serum's full chain), `eq_cap_test.py`.
- The shipped set is a per-preset choice between two runs (`pick_best.py`: new only if it wins on the fitted notes AND on notes +5 st
  by >= 1 loss): 15 new, 55 old, Bass - A Classic kept exactly as approved. Then `post_fit.py` (width by the side/mid difference, one
  re-render to check, then a volume nudge) on the files themselves: 32 widths changed (9 wider, 23 narrower), 34 volumes nudged;
  A Classic excluded again after the fit narrowed it to 14/64 (Serum's bass image is near mono).
  **The shipped set is not reproducible from the converter alone**: converter -> pick_best -> post_fit, with the p10/p11/p12 records
  (git-ignored) as inputs.

## Results against Serum's full chain (70 presets, `chain_report.py`)
| | before this patch (p10) | after pick_best (p11) | after width fit (p12) |
|---|---|---|---|
| full-chain loss, fitted notes | 15.60 | 14.91 (vs p10) | 14.29 (vs p11 15.11) |
| full-chain loss, notes +5 st | 16.55 | 16.12 | 15.54 (vs p11 16.09) |
| side/mid error, \|dB\| | 10.3 | 10.1 | 7.2 (shifted 8.3) |
| RMS error, \|dB\| | 0.82 | 0.83 | 0.53 |
The choices were made on the same measurements, so the gains are optimistic. The 1-3 kHz band reads slightly darker than before (-1.3 -> -2.1 dB).

## What the website demos are (calibration/transcribe.py, demo_compare.py)
The pack has no MIDI. A rough multi-pitch transcription of a demo, played through the real Serum preset and the TinyHouse preset, shows
how it was made. Lead - Tonic Walter: transcribed notes on the plain Serum preset match the demo's timing (loudness-shape correlation
0.91) and stereo width (side/mid -4.1 vs demo -4.3 dB); the demo is 5 dB louder (mastering). TinyHouse: timing 0.94, but spectrum 9.0 dB
from the demo (Serum 6.2) and too wide (side/mid +0.2 dB). Gravity and Can't Find You: timing 0.92-0.97 but a 10-11 dB spectral gap for
Serum itself, so the demos carry other layers or processing; Sleepless is a dense arpeggio the transcriber gets wrong (timing 0.46).
WAVs of demo / Serum / TinyHouse are in `calibration/demo_renders/` (git-ignored). The demos are other people's recordings: for private comparison only.

## Open
- Missing effects, by what Serum says they are worth: Filter moved by envelope/LFO (5 presets, up to 26 units), Distortion (6 presets
  over 5 units), Comp (5), Utils inside a Split branch (Bass - Let It Go, 25). Distortion, Comp, Bode and SplitMS are still not in the engine.
- Filter FX mapping: skip untyped filters and re-fit; check the ten carried filters that did not improve.
- Nobody has compared any of this against Serum by ear.
