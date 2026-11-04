# Tablor Deep House: unattended run report

> Superseded for current numbers by `REPORT_PATCH2.md`. The engine limits listed here have since been partly addressed.

## Summary

| Phase | Outcome |
|---|---|
| 1. Commit, rename, rebuild | Done. Two local commits, nothing pushed |
| 2. Deploy and benchmark | Passed. All test suites pass on the Move, and the 64-stream budget is safe for one instance |
| 3. Calibration | Harness works, and envelopes improved clearly. **The target was not met: Tablor does not yet match Serum, and brightness got worse** |

**Nobody has heard this.** All of the evidence is measurement. The module has not been loaded into a slot by the real host; `tablor_loadtest` loads it the same way in a separate process.

## Phase 1: checkpoint and rename

- `1d69822` Deep house engine: third oscillator, unison to 16, hard sync, stream budget
- `69a3dc9` Rename to tablor-dh so it installs beside stock Tablor

Local branch `deephouse-engine`. `origin` points at upstream, so I did not push.

- Module id `tablor-dh`, name "Tablor Deep House", version `1.4.0-dh`, presets in `schwung/presets/tablor-dh/`.
- Beyond the brief: the fork's legacy preset folder is now `Tablor DH Presets`. The migration deletes what it copies, so sharing stock's folder would have taken its presets. `tools/loadtest.cpp` was also repointed, because it writes and removes files in the store it names.
- `release.json` and `docs/catalog-entry.json` still describe the upstream release. They are not used on the device.
- Still not built with the project's own Docker/GCC toolchain, because Docker is not installed here. This is a Zig/clang build.

## Phase 2: on the Move

Device: 4 cores at 1.5 GHz, PREEMPT_RT, firmware running idle, no slot loaded.

| Check | Result |
|---|---|
| `tablor_loadtest` | 74 passed, 0 failed |
| `tablor_test` | 77 passed, 0 failed |
| `tablor_engine_test` | 35 passed, 0 failed |
| All 70 final presets played with real wavetables | None silent, none clipping, 64 streams at most |
| Stock Tablor | `dsp.so` hash unchanged, preset files unchanged |
| MoveOriginal | Same PID throughout, never restarted |

### The real deadline

The deadline that matters is not the 2902 us block. Schwung's host header gives modules **about 2370 us of slack per block, on one callback** (SCHED_FIFO 70, core 3) shared with every other slot and Master FX.

Mean cost per block, 3 runs:

| Patch | Streams | Mean | Share of 2370 us |
|---|---|---|---|
| Stock Tablor, 8 voices, 2 oscs, unison 4 | 64 | 0.738 ms | 31% |
| Patched, same patch | 64 | 0.682 ms | 29% |
| Patched, same patch with sync (true worst case) | 64 | 0.754 ms | 32% |
| 8 voices, 3 oscs, unison 16 | 48 | 0.575 ms | 24% |
| 4 voices, 2 oscs, unison 7 | 56 | 0.452 ms | 19% |
| Mono, 3 oscs, unison 16 | 48 | 0.240 ms | 10% |

Final presets on the device (4-note chord, or one note for mono, with release tail):

| Category | Mean | Heaviest | Heaviest as share of 2370 us |
|---|---|---|---|
| Bass (15) | 0.051 ms | 0.134 ms | 6% |
| Keys (15) | 0.162 ms | 0.265 ms | 11% |
| Lead (20) | 0.117 ms | 0.205 ms | 9% |
| Pad (10) | 0.296 ms | 0.425 ms | 18% |
| Pluck (10) | 0.149 ms | 0.255 ms | 11% |

### Verdict on the 64-stream budget

- **One instance is safe:** 0.75 ms worst case, 32% of the slack.
- **The cap does not protect the whole device.** Four worst-case instances would need about 3.0 ms, which is over the slack. Three would need 2.26 ms, leaving nothing for FX.
- **A real set fits:** one of each category averages 0.78 ms (33%). The heaviest of each is 1.28 ms (54%). Schwung has four chain slots, so five sounds would not all fit in slots anyway.

### Spikes in the max column are not DSP

Lone blocks of 7 to 10 ms appeared in some runs, including on the lightest patch. A control loop with no audio code (`jitter`) shows the same pattern: median 0.74 ms, p99.9 1.55 ms, max up to 10.9 ms. The benchmark is a normal-priority process being preempted by the firmware's real-time threads. The real callback runs at FIFO 70, so these columns cannot answer the deadline question. Only the mean can.

**To confirm:** the timings above are not from inside the callback. Schwung's manager CPU page (`http://move.local:7700`) shows real per-module numbers from `/schwung-perf`. Check it with a real set and your FX.

## Phase 3: calibration

Serum 2 renders headlessly through DawDreamer, with no dialogs. Preset FX are stripped from the reference, because Tablor is dry.

Each constant was fitted on an isolated probe through the real converter. The canonical patches and a held-out set then decided what shipped, and a final fresh set of 12 presets, unused for any of those choices, gave the verdict.

### Patch selection differs from the brief

**The pack has no FM bass.** No bass has an active FM warp, and Tablor has no FM. I used:

- Donk bass: `Bass - A Classic`
- Poly chord stab: `Keys - Catching Flies`
- FM control (known gap, not tuned to): `Pluck - all things break`

### Fresh set, exactly as shipped

"Neutral" is the mapper as it was with neutral constants, before the routing and key tracking fixes. "Shipped" includes the clip guard. Values are median absolute error over 12 presets.

| Metric | Neutral | Shipped | Better / worse |
|---|---|---|---|
| RMS | 14.9 dB | 12.6 dB | 5 / 7 |
| Peak | 14.2 dB | 12.6 dB | 6 / 6 |
| Spectral centroid | 4.4 st | **8.4 st** | 4 / 8 |
| Spectral rolloff | 6.0 st | **6.7 st** | 3 / 9 |
| Third-octave band error | 16.6 dB | 13.6 dB | 8 / 4 |
| Envelope shape | 5.8 dB | 6.0 dB | 7 / 5 |
| Decay time (log2 ratio) | 0.81 | 0.43 | 8 / 4 |
| Stereo side/mid | 2.1 dB | 1.6 dB | 5 / 4 |

For scale, Serum against itself: median band error 0.8 dB, RMS spread 0.9 dB. Tablor is well outside that on every metric, so **the measurements did not align**.

`Keys - Hans Deeper` uses a sample osc whose file Serum cannot open, so its reference is unreliable. It is left in the table above.

### Canonical patches, Tablor minus Serum, neutral -> shipped

| Metric | Donk bass | Chord stab | FM control |
|---|---|---|---|
| RMS | -13.9 -> -10.2 dB | -8.9 -> -5.3 dB | -11.0 -> -3.9 dB |
| Peak | -11.7 -> -7.3 dB | -5.8 -> -4.5 dB | -11.3 -> -5.2 dB |
| Centroid | +8.1 -> **+12.4 st** | -2.1 -> +1.6 st | -2.2 -> -3.3 st |
| Rolloff | +10.9 -> **+13.5 st** | -1.3 -> +1.4 st | -2.5 -> -3.5 st |
| Decay time ratio | x0.68 -> x1.04 | x0.47 -> x0.73 | x0.79 -> x1.06 |
| Envelope shape | 7.7 -> 9.9 dB | 26.3 -> 11.9 dB | 4.4 -> 3.4 dB |
| Band error | 24.5 -> 27.7 dB | 11.7 -> 8.0 dB | 9.4 -> 12.2 dB |
| Side/mid | -7.4 -> -0.7 dB | -4.4 -> -2.3 dB | -0.4 -> -0.8 dB |
| Correlation | +0.39 -> +0.02 | +0.53 -> +0.27 | n/a |

The chord stab is close. The donk bass is not, largely because it is clamped an octave high (see below). `A Classic` also moved about 5 dB between runs, so read its level figures loosely.

### Probe results and what shipped

| Law | Measured in Serum | Probe error | Shipped |
|---|---|---|---|
| Osc level | Linear (exponent 0.99). Tablor squares its pot | Exact by construction | `level_exp` 0.494 |
| Master volume | Linear, default 0.25 | 0.05 dB worst | Yes, but capped by Tablor's volume (below) |
| Filter drive | +3 dB at 10, +10 at 35, +17 at 100. 42 of 70 presets use it | Measured | Gain only: Tablor has no saturator |
| Cutoff vs MG Low 24 | -6 dB corner about an octave below nominal | 8.3 -> 3.2 st with -8 | **-4 st, not the fitted -8** (see below) |
| Resonance | Peak 17.5 dB at 80, 30.4 dB at 95 | Within 2 dB up to 80 | **Not shipped**: scored worse end to end |
| Key tracking | 1.0 means on. Pivot near note 36, Tablor's is 60 | 21.7 st -> 1.7 st | Yes |
| Envelope curves | Same 1 s decay: t20 is 1.04 s at curve 30, 0.04 s at curve 100 | Within 0.2 log2 | Yes, as lookup tables |
| Sustain | Gain is norm squared | 0.3 dB | Yes, confirmed |
| Filter envelope depth | Fit hit the grid limit with 18 dB left | Poor | **Not shipped**: stays 1.0 |
| Unison detune | `kParamDetuneWid` is detune spread, not stereo | 1.11 -> 0.31 log2 | Yes |
| 16-voice stereo | `kParamUnisonStereo` is the width control | 5.7 -> 3.3 dB | Yes, but limited (below) |
| Sub level | | 4.5 -> 0.6 dB | Yes, 0.75 |
| Routing | Only osc A reaches the filter by default | Measured directly | Yes |

Cutoff is a judgement call. The probe supports -8 st, but -4 scored better on the patches. The residual comes from filter shape: Serum's ladder rolls off more gently near the corner than Tablor's cascade, so no single offset fits. **Selecting on the patches means brightness tuning is the weakest part of this table.**

Each fitted table is in `calibration/cal_final.json`, and the same values are in `CAL` in `deephouse2move.py`.

### Bugs found in my earlier work

My earlier converter had these wrong, and all are fixed:

- It read `kParamDetuneWid` as stereo width. It is detune spread. The real control is `kParamUnisonStereo`.
- It assumed Serum's level law. It is linear, not squared.
- It ignored filter drive, key tracking, master volume and routing.
- `Bass - BUNT` was at -35 dB because of key tracking. It is now -7 dB.

The first two calibration runs also had flawed probes. Their logs are kept in `calibration/` as `run1_flawed_probes.log` and `run2_floor_and_wrong_params.log`. Do not use those numbers.

### Clip guard: the trade-off to review

Matching Serum's level made 6 presets hard-clip, because Tablor's output clamps. The converter now renders each preset and lowers its volume until a full-velocity 4-note chord stays clean. It trimmed **47 of 70 presets, by up to 11 dB**, which is why the RMS gain above is modest. A soft limiter in the engine would allow both.

### Why it does not align: engine limits

Ordered by how much I expect each to matter. These cannot be fixed in the converter.

1. **Oscillator tune stops at +/-24.** 15 presets clamp. At -3 octaves Serum plays 29.6 Hz and Tablor 64.6 Hz, so they play an octave high. Playing `A Classic` an octave lower in Tablor improves rolloff from +9.9 to +5.0 st and band error from 35.1 to 28.5 dB. Widening the range is a small change.
2. **No filter saturation.** Drive is used by 42 of 70 presets. Its harmonics are absent, and the gain is capped by the volume ceiling: 51 presets report being more than 3 dB quieter than Serum.
3. **LFO and velocity routes not carried.** 36 presets lose a route of 25% or more, including sidechain pumping. Rebuild with Schwung slot LFOs.
4. **Unison stereo is narrower.** At full width Serum measures -0.1 dB side/mid and correlation -0.05. Tablor reaches -4.7 dB and +0.45, the limit of its pan law.
5. **Detune stops at 1 semitone.** Serum goes well past it (17.8 Hz spread against Tablor's 8.5 Hz).
6. **One filter, one filter shape, no per-source bypass.** 15 presets use Filter 2. 10 have sources that bypass the filter.
7. **No sample oscillators or FM.** Still the main reasons 20 presets are tier C.

### Still unexplained

- `Pad - BICEP` is 29 dB too quiet, with all its sources routed into a 42 Hz filter, yet Serum stays loud. I don't know why.
- `Pad - MIND` is about 20 st too bright. Removing its cutoff mods or Filter 2 changes nothing in Serum.
- `Keys - Can't Find You` is about 50 st too bright.
- Quietest presets on the device: `Pad - BICEP`, `Pluck - all things break`, `Pluck - NOTSOBAD`, `Pluck - Nu Aspect`, around -40 dB on the test chord. Some of these are pitched for other registers, so try them by ear first.

Tiers now read A 0, B 50, C 20, down from A 4, B 46, C 20. The difference is the new "quieter than Serum" gap, not a new loss.

## What is on the Move

| Path | What |
|---|---|
| `/data/UserData/schwung/modules/sound_generators/tablor-dh/` | The module |
| `/data/UserData/schwung/presets/tablor-dh/` | 70 tuned presets, 8 seeded factory sounds, 1 stamp file |
| `/data/UserData/UserLibrary/Wavetables/Deep House/` | 74 wavetables, 42 MB, including Serum factory tables: don't redistribute |
| `/data/UserData/UserLibrary/Tablor DH Presets/` | Empty, created by the module |
| `/data/UserData/tablor-dh-bench/` | Test binaries and logs, 43 MB. Safe to delete |

I did not restart anything or load the module into a slot. It may need a Schwung restart to appear.

## Suggested next steps

1. **Listen first.** Load Tablor Deep House in a slot, compare a few presets with Serum, and check the CPU page.
2. **Then a second engine patch**, in this order: tune range to +/-48, soft limiter, filter drive, wider stereo. I expect the first two to matter most and the first is a small change.
3. **Then re-run the calibration.** Brightness in particular should be refitted after that.

## Reproducing this

```
.venv\Scripts\python.exe -X utf8 test_render_calibration.py     # fit
.venv\Scripts\python.exe -X utf8 calibration\select_final.py    # choose constants
.venv\Scripts\python.exe -X utf8 calibration\finalize.py        # routing, key tracking, fresh check
```

`deephouse2move.py` and the harness are not in git, because `C:\Github\TinyHouse` is not a repository. Only `schwung-tablor` is. Running `test_render_calibration.py` starts from whatever is in `CAL`, so reset it to neutral values to reproduce the neutral scores.

## Logs

| File | Contents |
|---|---|
| `reports/move_deploy.log` | Pre-flight and install, with hashes |
| `reports/move_loadtest_full.log` | Loader test |
| `reports/move_test_dsp.log`, `move_test_engine.log` | Test suites on the Move |
| `reports/move_enginebench.log` | Benchmark runs and scheduling control |
| `reports/move_presets_render_final.log` | All 70 final presets on the Move |
| `calibration/run3.log` | Probe fits |
| `calibration/select_final.log` | Key, detune and filter variant scoring |
| `calibration/finalize.log` | Pivot fit and pre-trim scores |
| `calibration/shipped_scores.log` | Fresh set exactly as shipped |
