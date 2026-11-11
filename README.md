# TinyHouse

**A wavetable synth for the Ableton Move, inspired by Serum 2, that plays your own Serum 2 presets.**

TinyHouse is a sound-generator module for [Schwung](https://github.com/charlesvestal/schwung) (the Move's open module host). It is built for people who already
have Serum 2 presets they like and want to take them on the Move. You bring the presets; TinyHouse converts them, and they play on the Move as native
wavetable patches: polyphonic, with unison, filters, modulation, eight macros and a full effects rack.

It is a re-creation, not Serum. Every setting is translated from what Serum itself does, and the filters, effects and levels were measured against the real
plugin, but it is a different engine on a small ARM box: **expect presets to sound close, and a little different.** Some features are not built yet
(see [What is supported](#what-is-supported)), and a preset that leans on one of them will lose that part of its sound.

**This is experimental.** TinyHouse is young and the converter is still being tuned. Importing in the browser is the easy route, but it has no copy of
Serum to measure against, so it sets loudness, reverb and width from general rules. If you have Serum 2 on a Windows computer and want a preset to come
out as close as possible to the original, use the Python scripts instead: they render each preset through the real plugin and match the level, reverb and width to it
(see [More accurate conversion with the Python scripts](#more-accurate-conversion-with-the-python-scripts)).

![The TinyHouse remote UI: three wavetable oscillators, with the tabs for the macros, filters, envelopes and effects below](docs/img/remote-ui.png)

## Getting started

**1. Install the module.** TinyHouse installs beside any other Schwung modules, under its own name (`tinyhouse`). Build it and copy it to the Move
(you need the Move on your network, SSH access to it, and a zig compiler; see [Installing](#installing)).

**2. Open the remote UI.** On a computer on the same network, open the Schwung manager at `http://move.local:7700`, go to **Remote UI**, and put TinyHouse
in a slot. This page is where you import and fine-tune sounds. (Use a normal browser window: the import needs one.)

**3. Import your Serum 2 presets.** Click the preset name in the header to open the browser, then **Import**:

![The import dialog](docs/img/remote-import.png)

- The easiest way: press **Find my Serum 2 folder…** and choose `Documents\Xfer\Serum 2 Presets` (or the `Xfer` or `Documents` folder above it; the Serum 2
  folder is found from there). Chrome and Edge remember it, so next time the dialog opens with it already read (at most one click to allow it again). Tick
  which presets to bring in (Factory, User, each pack). The wavetables, samples and multisamples they use are found in that folder for you, and only the
  ones the presets use are copied to the Move. Browsers only allow this on a secure page and the Move's page is plain http, so unless you add its address to `chrome://flags/#unsafely-treat-insecure-origin-as-secure`, or use another browser, you choose the folder each time (the dialog says which applies).
- Or choose single `.SerumPreset` files, a `.SerumPack` or any folder (or drop them on the dialog). Then, if the presets use Serum's own wavetables, point
  **Tables folder** at the `Tables` folder, and for sample or multisample oscillators point **Samples** (and **Multisamples**) at the matching folders.
  Presets in a pack bring their own wavetables.
- Press **Import**. The presets are converted in your browser and stored on the Move, loose presets in a bank called *Imports* and a pack or folder in a
  bank of its own name. A report lists every preset as *imported*, *imported with parts missing* (and which parts) or *not imported* (and why).

**4. Play.** The new bank is in the preset browser. Pick a sound and it loads on the Move.

Your presets go nowhere but your Move: the converter runs inside the browser page (which downloads the Python runtime it uses from a public CDN the first time).

## Tweaking and playing

**Fine-tuning is on the remote UI.** It is laid out after Serum: three oscillators with 3D wavetable displays, then tabs for macros, filters, envelopes,
effects, mix and globals. Every control is live in both directions with the Move. Drag the filter curve and envelopes, pick wavetables from the
list, set the effects, and save the result as a preset of your own. The import report's loudness, reverb and width are set from general rules (the
importer has no Serum to compare against), so set **Volume** and the effects to taste.

**Playing live is on the Move itself.** The pages are built for performance, with no menus:

| Page | What is on it |
|---|---|
| **Main** | filter freq, res and envelope amount, decay, sub, width, space, bank |
| **Tone** | the two wavetable positions, attack, release, drive, shelves |
| **Macros** | the preset's eight Serum macros, as dials. A macro the preset does not use is shown as an empty slot. Touch a knob and the top bar shows its name and value |
| **Effects**, **Mod FX** | each effect on or off, with its main controls |
| **Demo** | plays a MIDI part from a folder on the Move so you can audition a bank: it waits for Play and joins on the next bar |

A preset's macros come across live: turning a macro moves the same parameters Serum's would (cutoff, effect mix, wavetable position and so on).

## The effects chain

Most Serum presets get half their character from the effects rack, so the rack is not an afterthought. TinyHouse has a full effects chain, and each stage was
measured against Serum with controlled test patches (not tuned by ear), so the effects follow Serum's own curves:

![The FX tab on a Deep House preset: the stages in use with their controls, and the bypassed ones listed underneath](docs/img/remote-fx.png)

| Stage | Serum effect | |
|---|---|---|
| Distortion | Distortion | tanh, biased, hard clip and sine curves, with Serum's wet law |
| Filter FX | Filter | static or following the filter envelope; low-pass, high-pass, band, notch |
| Compressor | Comp | single band and three-band multiband |
| Mod FX | Chorus, Flanger, Phaser | real modulated stages |
| Width | HyperDimension | spread and level |
| Delay | Delay | tempo-synced to the Move, band-pass in the feedback, ping-pong |
| Reverb | Reverb | size, decay and damping |
| EQ | EQ | four bands |
| Master | Utils | width, mono-below, soft output clipper |

On top of that there are **two FX buses**, each a full copy of the rack. Oscillators and filters have their own sends to each bus, as in Serum.

## What is supported

**Carried across**
- Serum 2 presets and packs (`.SerumPreset`, `.SerumPack`).
- Three wavetable oscillators with unison to 16 voices, a sub and a noise oscillator; Serum's wavetable position, stepped or interpolated.
- Warps: bend, PWM, sync, even/odd, the filter (low-pass and high-pass) warps, and FM, phase-distortion, ring-mod and AM warps driven by the Sub or another oscillator (two warps per oscillator).
- Sample and multisample oscillators (from your Samples folders).
- Two filters, in parallel or in series: the common ladder, high-pass and low-pass models, and the notch, band-reject, Exp, Scream, RM and DJMixer models (each matched to the closest engine filter, measured against Serum).
- Envelopes, four LFOs, and the routes from macros, velocity, note number, mod wheel and random sources.
- The whole effects chain above, and the two FX buses.

**Not carried yet**
- FM, ring-mod and phase-distortion warps driven by the noise or by Filter 1 (the Sub- and oscillator-driven ones are carried), DLM, spectral tables, Sync Var.
- Formant, comb, flange, phase and reverb filter models (no engine equivalent yet).
- The granular oscillator (left silent).
- A second distortion, compressor or delay; band-split (Split) branches; the Bode frequency shifter; a filter placed before the distortion; sends moved by an
  LFO or envelope.
- Tempo-synced LFOs are read as plain rates in Hz.
- Convolution (Space) reverbs.
- Serum 1 presets. Serum 2 only.

A preset using something in the second list still imports, with that part left out, and the import report says which. How closely it then sounds like
the original depends on how much of the sound that was.

## More accurate conversion with the Python scripts

The browser import is the easy way in, and it is deliberately approximate. The Python importer in this repo does the same conversion but also **plays each
preset through the real Serum 2 plugin** and measures it, so it can do three things the browser cannot:

- **Match the loudness** of every preset to what Serum produces, instead of using one general offset.
- **Match the reverb and width** to Serum's own (the browser sets them from fitted rules, so they are close but not measured per preset).
- **Check the result**: it compares octave-band levels against the Serum render and leaves out a preset that ends up too far from it, with the reason in a
  `skipped_<bank>.md` report. It is stricter than the browser: a preset that uses something TinyHouse cannot carry is dropped, not imported with a part missing.

You need Windows, the Serum 2 plugin (VST3),
Python 3.13 and a bash shell for the deploy scripts (Git Bash is fine).

```
pip install -r requirements.txt
python import_pack.py "C:\path\to\folder of .SerumPreset" --bank "My Pack"      # converts, renders in Serum, fits levels; writes out_import/My Pack/
BANK="My Pack" sh ./deploy_wavetables.sh                                         # copy to the Move over SSH (each step keeps a rollback copy)
sh ./deploy_samples.sh
SRC="out_import/My Pack" BANK="My Pack" sh ./deploy_presets.sh
```

To bring in Serum's own Factory presets, point it at `Presets\Factory` inside your Serum 2 folder with `--bank Factory`. It is the same three steps, and it keeps
only the presets that match Serum's render within the check above (about 290 of the 626). It takes hours, because every preset is rendered, so leave it running;
if it stops, run the same command again with `--resume` and it carries on where it left off.

Reload the slot afterwards and the bank is in the preset browser next to your imported ones. `deephouse2move.py` converts a `.SerumPack` directly and
`serum_paths.py` explains how Serum's folders are found (set `SERUM_ROOT` and `SERUM_VST` if yours are elsewhere). The first run on a pack is slow because
every preset is rendered; the renders are cached, so a second run is fast. [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) goes into the options and the rules behind them.

## Installing

TinyHouse is not in Schwung's module catalogue yet, so for now it is installed from this repo. You need Windows, macOS or Linux, a Move running Schwung that you
can reach over SSH, and [zig](https://ziglang.org) to build the ARM module.

```
bash schwung-tablor/scripts/zig-build.sh arm           # builds schwung-tablor/build/dsp.so
python schwung-tablor/tools/build_import_bundle.py     # the converter the remote UI's Import runs
bash schwung-tablor/scripts/deploy.sh ableton@move.local
```

Then reload the slot (or restart the Shadow UI) so the new module loads. `deploy.sh` replaces the module safely while the Move is running.

## For developers

The converter and the importer that runs on a computer with Serum 2 installed, which matches loudness, reverb and width against Serum, the calibration
harness, the translation rules and the roadmap are in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).

## Licences and what is not here

The engine is a fork of [Tablor](https://github.com/athousanddetails/schwung-tablor), itself a port of FigBug's [Wavetable](https://github.com/FigBug/Wavetable),
under BSD-3-Clause (see `schwung-tablor/LICENSE` for all the components).

This repository contains **no Serum presets, packs, wavetables, samples or renders**, and no presets converted from them: those belong to Xfer and to the
preset authors. You convert your own. Check the licence of a pack before sharing presets you converted from it.

TinyHouse is not affiliated with or endorsed by Xfer Records, Ableton or the Schwung project. "Serum" is a trademark of Xfer Records; "Move" is a trademark of
Ableton AG.
