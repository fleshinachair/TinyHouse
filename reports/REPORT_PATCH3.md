# Engine Patch 3: the FX are in the synth

Follows `REPORT_PATCH2.md`. `tablor-dh` 1.6.0-dh, installed on the Move with the 70 presets rebuilt.

**Nobody has heard this.** All of the evidence is measurement, against Serum 2 rendering the same presets.

## What was built

A master FX block between the voices and the output clipper, in this order: **width, delay, reverb, four-band EQ**. It is off by default and a strict bypass when off.

| Stage | What it is |
|---|---|
| EQ | Four bands. Each is Off, Bell, Low shelf, High shelf, High-pass or Low-pass, with freq, gain and Q. Gain does nothing on a cut, as in Serum |
| Width | A chorus-style stage that adds side signal only (+side left, -side right), so the mono sum of the output is exactly the input |
| Delay | Stereo or ping-pong, a left and a right time, feedback, and a band-limited feedback path |
| Reverb | Eight-line feedback delay network behind four diffusers, with an explicit decay time, damping, low cut and width |

Controls: 32 new parameters (71 -> 103) on four new pages (SPACE, SPACE 2, EQ 1, EQ 2). **SPACE** is the last cell of the main page, where WT1 Pos was (WT1 Pos is still the first cell of OSC). It scales delay and reverb together; 100 is the preset as made, 0 is dry.

## Where your brief and the measurements disagreed

| Brief | What I found |
|---|---|
| "Hyper/Dimension and Chorus give the width" | True for 45 presets (audible width measured on Serum). 14 more have a chorus or Hyper in the chain that adds none |
| "Multiband/Compressor dip the 3-5 kHz fizz" | **Not verified, and not carried.** 34 presets have a compressor and 36 a distortion, neither is in the engine. The tonal shaping I could measure and carry is the EQ: 63 presets have one in the main rack, 61 are carried (the other 2 have EQs only inside split branches) |
| FX would cost "0.2-0.4 ms" | **0.04 to 0.05 ms** a block on the Move with every effect on (table below) |
| "Encoder 7 or 8 for the combined wet/dry" | Done: SPACE replaced WT1 Pos in cell 8. The other seven main-page cells are unchanged |
| "Reverb mix/decay, EQ cutoff... extracted by the script" | The script did not extract them, it only listed FX names from rack 0. Serum stores FX values in its own units and omits any left at default, so the converter now measures instead of reading. See below |

## How each effect was matched

Nothing was guessed. Each law below was measured on Serum 2 with `calibration/fx_probe.py`, and each preset's own reverb, delay and width was then measured on Serum with `calibration/fx_fingerprint.py`.

**EQ (a law).** Types: 0 is a shelf (low on band 1, high on band 2), 1 a bell, 2 a cut (high-pass on band 1, low-pass on band 2). Q comes from Reso through a table (Reso 0, 50, 75, 100 gives Q 0.10, 1.25, 5.5, 14 on a bell; 0.40, 0.64, 1.13, 2.10 on a shelf). A missing key means a low shelf at about 200 Hz and a high shelf at about 2.1 kHz. All of it fits real biquads to **0.5 to 0.7 dB rms**, which is the noise floor of the measurement.

**Delay (a law, plus a measured mix).** Time is 5030 ms x the stored value (unwritten: 0.1). Mode 1 is ping-pong with the right echo at left time + right time. Feedback is an amplitude (unwritten: 40 %). The feedback filter is a high-pass at f/2^(bw/2) and a low-pass at f x 2^(bw/2), which matched Serum within 2-3 dB across the spectrum. A tap at wet 100 is -0.5 dB re its input. (My first measurement said -6.4 dB. It was my burst-peak method, not Serum. The energy of a 0.3 s burst settled it.)

**Wet and dry (a law).** Wet is a smoothstep (0.027, 0.15, 0.51, 0.87 of full at 10, 25, 50, 75 %). Dry holds full level to 25 % and then fades out by smoothstep (-2.1 dB at 50 %, -11.7 dB at 75 %).

**Reverb (closed loop).** Serum's two reverb algorithms are too different to model: its default type is narrow (side/mid -14 dB) and uses Feedback, its Hall is fully decorrelated and its decay depends on Size and Delay together, reaching infinite at Size 100. So the converter does not read them. It measures the preset's reverb on Serum (decay, tail brightness, width, level, wet), then searches the engine's decay and damping until the same measurement on the real Tablor engine agrees.

**Width (closed loop).** The preset's chorus, Hyper, flanger and phaser together are matched on the side energy they add to a noise burst.

Not mapped, and listed per preset in the report: distortion (36 presets), compressor (34), filter FX (27), stereo utilities (11), bit/frequency shift (6), FX in the two bus racks (10 presets), and any modulation of an FX parameter.

## Result

### The mapping itself (60 reverbs, 45 widths, 44 delays, 61 EQs)

| | Median | Worst |
|---|---|---|
| Reverb decay, Serum vs engine | 2 % off (log2 0.023) | 0.62 (1.5x), on a tail near infinite |
| Reverb brightness | 0.7 semitones | 44.9 st, on two tails that are almost all bass (centroid 3 and 78 Hz) |
| Width (side energy added) | 0.1 dB | 0.5 dB |
| EQ: more than 4 bands reduced to 4 (5 presets) | 0.2 dB rms | 1.0 dB rms |

56 of the 61 carried EQs fit in four bands exactly. 7 reverb levels are limited to 12 dB from Serum's, because a level that far off is a measurement to doubt. 4 tails are quieter than Serum's, which needs more than the engine's maximum. 7 delay mixes come from the stored Wet because the measurement was silent (presets with two chained delays).

### Against Serum with its own FX: the 12 validation presets and the 2 anchors

Median absolute error, Serum with all its FX against Tablor **with** the FX block and against the same Tablor with the block **off**. Nothing was fitted to these. Tail levels below -80 dB count as -80 (two kinds of silence are not an error).

| Metric | With FX block | Without | Better / worse with it |
|---|---|---|---|
| **Tail level** (0.3-1.3 s after note-off) | **4.7 dB** | 28.2 dB | 11 / 1 |
| **Tail brightness** | **9.5 st** | 38.2 st | 12 / 1 |
| Held-note RMS level | 2.7 dB | 5.8 dB | 5 / 9 |
| Held-note brightness (centroid) | 4.1 st | 5.3 st | 8 / 6 |
| Spectral band error | **14.6 dB** | 18.5 dB | 9 / 5 |
| Stereo side/mid | 3.5 dB | 6.2 dB | 8 / 5 |
| Decay time (log2 ratio) | **0.94** | 0.71 | 6 / 8 |

**What this says.** The block fixes what was missing: without it the tail is absent, with it the tail is about 5 dB and 9 semitones from Serum's. The spectral fit and stereo width improve modestly. **The decay-time figure got worse, and the held-note level moved in both directions**, so I would not claim either improved. The two anchors: `Keys - Catching Flies` band error 18.9 -> 12.4 dB; `Bass - A Classic` 12.0 dB against 9.6 with no FX, so it is still slightly worse with them (it also has a distortion and a compressor that are not carried).

Four presets are still far off, as before: `Pad - BICEP` (-21 dB level), `Keys - Zerb` (-15), `Pluck - NOTSOBAD` (-16), `Lead - Can't Find You` (-7). Their gaps are synth-engine ones, not FX.

**One mapping fault found and fixed during validation.** `Bass - A Classic` first scored worse with the FX (band error 17.1 dB). It has a mid/side split in its chain and the EQs after it, a 248 Hz high-pass and a 1971 Hz low-pass, belong to a *branch* of that split. I had applied them to the whole signal. After a split node come ModuleCount1 + ModuleCount2 modules (each 1 when unwritten) that are branch modules, so EQs inside a branch are no longer carried (a note says so, on 8 presets).

**Stereo.** `Pluck - HotLap` (+8.6 dB side/mid), `Lead - Can't Find You` (+8.7) and `Bass - A Classic` (+9.6) are still wider than Serum. That is the unison panning noted in the last report (hard-panned unison overshoots on real presets), not the FX, and I have not changed it.

### Clip guard

35 of 70 presets are trimmed (34 before), worst -12.5 dB (was -12.5), median -4.7 dB. The guard now measures the output with the FX, since reverb and EQ add peaks.

## Engine and device

| Check | Result |
|---|---|
| `tablor_fx_test` (new) | 46 checks, 0 failures, native and on the Move |
| `tablor_engine_test` | 52 checks, 0 failures |
| `tablor_test` | 77 checks, 0 failures |
| `tablor_loadtest` on the installed `dsp.so` | passed, 0 failures, preset folders unchanged |
| Bypass | 27 of 27 presets whose previous render is deterministic are byte-identical to the previous commit. The other 43 use `random_device` noise, so no two renders of them are equal even without this change |
| Stock Tablor | `dsp.so` hash `ff89907f5fca` unchanged |

The FX tests assert numbers, not survival: a bell is its set gain at its centre (12.00 dB), a delay tap lands within one sample, the reverb's measured decay is within 25 % of its setting (the damping filter costs about 15 % even wide open, which the closed loop absorbs), the wet-100 reverb level is flat to 0.08 dB across 0.4-15 s, and 12 random settings of every control stay finite.

**CPU on the Move**, mean per 128-sample block (2.902 ms), 3 runs of 10 s of audio:

| Patch | Without FX | With every effect on |
|---|---|---|
| 8 voices, uni 4, sync + drive (heaviest) | 0.786 ms (27 %) | **0.824 ms (28 %)** |
| 8 voices, 3 oscs x uni 16 | 0.575 ms (20 %) | 0.626 ms (22 %) |
| FX alone, 1 voice 1 oscillator | | 0.130 ms (4 %) |

The p99.9 and worst-block figures did not move beyond run-to-run noise (heaviest: p99.9 1.63 -> 1.65 ms).

## What is installed

`/data/UserData/schwung/modules/sound_generators/tablor-dh/`: `dsp.so` (md5 `a007ab86...`, built with Zig for aarch64, glibc 2.34 or older, no GNU_UNIQUE symbols, entry `move_plugin_init_v2`), `module.json` 1.6.0-dh, `ui_pages.json`, `help.json`, `web_ui.html`. 70 presets in `/data/UserData/schwung/presets/tablor-dh/`, md5-checked against the build. The files were swapped in with `mv` over a new inode, never copied over the live `dsp.so`.

**No restart was needed or done.** No running process had the module loaded, so the next time a slot loads **Tablor Deep House** it maps the new file. If a slot already had it open before the swap, reselect the module.

**Rollback:** the previous module and presets are in `/data/UserData/tablor-dh-rollback/` (`dsp.so` md5 `c94a5153...`, the five module files, 78 preset files). Delete that folder when you are happy.

## Not done, and what I would do next

- **Distortion, compressor and filter FX** are not carried (36, 34 and 27 presets). A compressor is the most likely remaining reason a bass sounds thin next to Serum, but I have not measured that.
- **Bus-rack FX** (10 presets) and **modulated FX parameters** are not carried.
- **Presets with two or more delays** carry the strongest one only; 7 take their mix from the stored Wet and not from a measurement.
- The **decay-time** match did not improve. I did not investigate whether it is the metric (a 4 s window with echoes) or the mapping.
- **Unison stereo overshoot** (above) is untouched.
- I cannot hear it. A reverb that measures right can still sound wrong, and the width stage is a plain chorus, not Serum's Hyper. Listen first, especially `Keys - Catching Flies`, `Bass - A Classic`, `Pad - Aquatiq` and `Pluck - HotLap`.

## Reproducing this

```
.venv\Scripts\python.exe -X utf8 calibration\fx_probe.py eqfit | reverb | delay | delaydef | levels   # laws, one process each
.venv\Scripts\python.exe -X utf8 calibration\fx_fingerprint.py 0 35 ; ... 35 70                        # per preset, two processes (render limit)
.venv\Scripts\python.exe -X utf8 deephouse2move.py "Loopsy - DEEP HOUSE.SerumPack" -o out --serum-tables "<Serum Tables>"
.venv\Scripts\python.exe -X utf8 test_render_calibration.py --stage fx
schwung-tablor\scripts\zig-build.sh native | arm
```

Logs: `calibration/fx_probe.log`, `fx_fingerprint.log`, `calibration.log` (the `fx` stage), `scores_fx.json`. Measurements only are committed (`fx_fingerprints.json`, `fx_eq_q.json`, `fx_tablor_cache.json`), no Serum audio.
