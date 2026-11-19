# Third-party code

## Vital (the Vital import path)

The Vital preset importer is a derivative of [Vital](https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, licensed under the GNU General Public
License version 3 or later. Where it helps accuracy or speed, the importer's algorithms (spectral morph, wavetable keyframe interpolation and others) are
implemented from Vital's source, then checked against the plugin's own output.

These files are therefore **GPL-3.0-or-later** (each carries an SPDX header), and the full licence text is in [LICENSE-GPL-3.0](LICENSE-GPL-3.0):

- `converter/vital_import.py`, `converter/vital_morph.py` and the data file `converter/vital_pd_table.npz`
- `calibration/vital_*.py`, the `calibration/vital_*_laws.json` measurements and `calibration/test_vital_import.py`

Nothing else in the repository imports these files. Everything else stays BSD-3-Clause (see [LICENSE](LICENSE)). A build or a pack that includes the Vital
importer together with other code is subject to the GPL for the combined work; the Serum importer, the engine and the remote page do not depend on it. The one exception is the remote page's import bundle: `import_bundle.zip` contains the Vital importer (with this file and the licence text under `licenses/`) so the page can convert `.vital` files; build it with `--no-vital` for a bundle that is not GPL.

"Vital" is a trademark of its owner and is named here only to identify the origin of the plugin format and algorithms. TinyHouse is not endorsed by, affiliated
with or supported by Matt Tytel or Vital Audio. This repository contains no Vital presets, wavetables or renders.

The engine's Warp Chain option (an oscillator that is a warp source keeps its own warps, and ring modulation at Vital's depth) is BSD-3-Clause code written from behaviour measured on the plugin (`calibration/vital_rm_probe.py`, itself GPL-3.0-or-later); it contains no Vital source code. It is off in every Serum preset.

**Engine code derived from Vital.** Six parts of the engine are ports of Vital's own algorithms, written with Vital's source in front of us (Copyright 2013-2019 Matt Tytel, GPL-3.0-or-later): the comb filter law (`CombFilter::processVital` in `schwung-tablor/src/dsp/comb.h`, from `synthesis/filters/comb_filter.cpp`), the compressor law (`Fx::processCompVital` and its helpers in `schwung-tablor/src/dsp/fx.h`, from `synthesis/effects/compressor.cpp` and the Linkwitz-Riley crossover) the chorus (`ModFx::processChorusVital` in `fx.h`, from `synthesis/modules/chorus_module.cpp` and the mono delay line of `synthesis/effects/delay.cpp`), the flanger (`processFlangerVital`, from `synthesis/modules/flanger_module.cpp` and `delay.cpp`) the phaser (`processPhaserVital`, from `synthesis/effects/phaser.cpp` and `synthesis/filters/phaser_filter.cpp`) and the reverb (`Fx::processReverbVital`, from `synthesis/effects/reverb.cpp`). All are off unless a preset sets them (Serum presets never do), but they are in the source, so a built engine (`dsp.so`) that includes them is **GPL-3.0-or-later** as a combined work. Release that build, or a pack containing it, under the GPL.
