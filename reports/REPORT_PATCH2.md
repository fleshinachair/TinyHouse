# Engine Patch 2 and recalibration

Follows `REPORT.md`. Module is now `tablor-dh` 1.5.0-dh, on the Move and restarted.

**Nobody has heard this.** All of the evidence is measurement.

*Update: the reverb, delay, width and EQ the presets depend on are now in the engine itself. Everything below was measured with the effects stripped from Serum's reference, so it stands as the dry-engine result. See `REPORT_PATCH3.md`.*

## Result on the 12 validation presets

Median absolute error, as shipped, clip guard included. None of the constants were fitted to these presets, and none were chosen using them. They have now been scored three times, so they are not unseen in the strict sense.

| Metric | Neutral | Patch 1 | **Patch 2** | Better / worse than Patch 1 |
|---|---|---|---|---|
| RMS level | 14.9 dB | 12.6 dB | **6.7 dB** | 12 / 0 |
| Peak | 14.2 dB | 12.6 dB | **8.1 dB** | 12 / 0 |
| Brightness (centroid) | 4.4 st | 8.4 st | **5.6 st** | 10 / 2 |
| Rolloff | 6.0 st | 6.7 st | **8.3 st** | 6 / 5 |
| Third-octave band error | 16.6 dB | 13.6 dB | **12.0 dB** | 8 / 4 |
| Envelope shape | 5.8 dB | 6.0 dB | **5.9 dB** | 8 / 4 |
| Decay time (log2 ratio) | 0.81 | 0.43 | **0.57** | 6 / 6 |
| Stereo side/mid | 2.1 dB | 1.6 dB | **3.5 dB** | 3 / 6 |

Serum against itself: band 1.2 dB, RMS 1.2 dB. **Tablor is still well outside that.** Level and brightness improved a lot. Brightness is still worse than neutral, rolloff is the worst of the three versions, and stereo got worse on these presets.

Stereo is worth a second look. On the isolated 16-voice probe the new pan law matches Serum within 1.1 dB. On real presets it overshoots on some: `Pluck - HotLap` +7.8 dB, `Pluck - NOTSOBAD` +6.1 dB. I suspect small stacks, where Serum may not pan as hard, but I have not tested that.

## Anchor patches, Tablor minus Serum: neutral -> Patch 1 -> Patch 2

| Metric | Donk bass (`Bass - A Classic`) | Chord stab (`Keys - Catching Flies`) |
|---|---|---|
| RMS | -13.9 -> -10.2 -> **-2.8 dB** | -8.9 -> -5.3 -> **-0.2 dB** |
| Peak | -11.7 -> -7.3 -> **-4.1 dB** | -5.8 -> -4.5 -> **-0.1 dB** |
| Centroid | +8.1 -> +12.4 -> **+8.0 st** | -2.1 -> +1.6 -> **+2.5 st** |
| Rolloff | +10.9 -> +13.5 -> **+9.2 st** | -1.3 -> +1.4 -> **+1.5 st** |
| Decay ratio | x0.68 -> x1.04 -> **x0.98** | x0.47 -> x0.73 -> **x0.84** |
| Envelope shape | 7.7 -> 9.9 -> **8.9 dB** | 26.3 -> 11.9 -> **9.4 dB** |
| Band error | 24.5 -> 27.7 -> **17.9 dB** | 11.7 -> 8.0 -> **9.4 dB** |
| Side/mid | -7.4 -> -0.7 -> **+0.2 dB** | -4.4 -> -2.3 -> **-0.6 dB** |
| Correlation | +0.39 -> +0.02 -> **-0.04** | +0.53 -> +0.27 -> **+0.07** |

## What changed in the engine

| Change | Measured effect |
|---|---|
| Tune to +/-48, sub included | Serum and Tablor agree: 29.6 Hz at -3 octaves, 16.1 Hz at -4. No preset clamps any more (was 15) |
| Output soft clipper | Linear below 0.7, no flat tops up to about +4 dB over full scale |
| Filter drive | Level error with Serum's drive at 100%: -16.5 -> -4.8 dB. Band error improves only above about 35 |
| Unison hard-panned left and right | 16-voice probe: Tablor -1.1 dB, Serum -1.0 dB (was -4.7 dB at the limit) |

Existing patches stayed bit-identical in all 7 golden tests (5 single-oscillator, plus unison and two-oscillator patches at spread 0). Patches with unison 4 or more and spread above 0 sound wider, on purpose.

The soft clipper is **not a limiter**. It has no gain reduction, and its shoulder runs out at about +4 dB over full scale.

## Clip guard: smaller, not gone

| | Patch 1 | Patch 2 |
|---|---|---|
| Presets trimmed | 47 of 70 | **34 of 70** |
| Worst trim | -11 dB | **-12.5 dB** |
| Median trim | not recorded | -4.7 dB |

The guard only acts now if a full-velocity 4-note chord runs out of the shoulder. That is deliberately cautious. Raising its threshold or testing at a lower velocity would recover level at the cost of some flat-topping on the hardest hits. That is a decision to make by ear.

## Is the plugin's 1,200 s render limit corrupting the reference? No

| Check | Result |
|---|---|
| When it mutes | Exactly 1,200 s of rendered audio per plugin instance (silent at render 599 of 2 s each). A new process resets it |
| Same patch at 10 s and at 1,160 s | RMS -25.3 +/- 1.3 vs -24.0 +/- 1.0 dB. Centroid 254 +/- 17 vs 219 +/- 20 Hz. Band error 3.35 dB, against 2.7 dB for ordinary repeats of this preset |
| Same saw in Serum and Vital | Harmonics within about 0.6 dB, centroid 256 Hz in both, both close to the ideal law |

The centroid gap is about 1.5 standard deviations on 5 renders, so a small drift cannot be ruled out, but nothing suggests one.

**That limit did corrupt one run of mine:** a 10-minute run scored against silence and reported RMS error of +163 dB. I discarded it. The harness now caches Serum's renders, runs each stage in its own process, and stops if a render is silent. The probe fits in that run came before the mute, so I kept them; the variant scoring and validation were redone.

### 3-way anchors

| Patch | Engine | RMS | Centroid | Rolloff |
|---|---|---|---|---|
| Donk bass | Serum | -25.7 dB | 82 Hz | 102 Hz |
| | Tablor | -28.7 dB | 134 Hz | 199 Hz |
| | Vital | -54.0 dB | 79 Hz | 70 Hz |
| Chord stab | Serum | -24.7 dB | 271 Hz | 318 Hz |
| | Tablor | -24.7 dB | 280 Hz | 393 Hz |
| | Vital | -33.8 dB | 834 Hz | 1249 Hz |

**Serum stays the reference.** Vital's gap measures my `.vital` export, which has no wavetables or modulation, and its filter is a different model. It says nothing against Serum. These exports are not good enough to be a tuning reference, and they have only been loaded here, by injecting them into Vital's saved state.

These single renders differ slightly from the 3-render validation averages above.

## On the Move

| Check | Result |
|---|---|
| `tablor_loadtest` | 74 passed, 0 failed |
| `tablor_test` | 77 passed, 0 failed |
| `tablor_engine_test` | 52 passed, 0 failed |
| All 70 final presets | None silent, no clipped samples, 64 streams at most |
| Stock Tablor | `dsp.so` hash unchanged, 9 store files unchanged |

The ARM-only "louder in is never quieter out" failure was one reversal of a single float ulp (6e-8), from rounding. The test now tolerates 1e-6.

Mean cost per block, 3 runs:

| Patch | Streams | Mean | Share of 2370 us |
|---|---|---|---|
| Patch 1 worst case (sync) | 64 | 0.754 ms | 32% |
| 8 voices, 2 oscs, unison 4 | 64 | 0.677 ms | 29% |
| Same with drive | 64 | 0.709 ms | 30% |
| Same with sync | 64 | 0.742 ms | 31% |
| **Same with sync and drive (worst case)** | 64 | **0.783 ms** | **33%** |
| 8 voices, 3 oscs, unison 16 | 48 | 0.571 ms | 24% |
| 4 voices, 2 oscs, unison 7 | 56 | 0.459 ms | 19% |
| Mono, 3 oscs, unison 16 | 48 | 0.239 ms | 10% |

The real presets: heaviest 0.42 ms (18%). One average preset per category 0.78 ms (33%), the heaviest of each 1.28 ms (54%).

## Deviations from the brief

- **Filter cutoff did not refit cleanly.** The probe says -9 st, but all 28 variants score about the same, so the chosen -4 st is within noise. I would not read much into that number.
- **Resonance now ships** with `res_exp` 0.3, which scored best in this run. It did not last time, and the table is flat, so this is a weak preference.
- **Filter envelope depth stays 1.0.** The fit hit its grid limit again, and scored clearly worse.
- **Drive is a simple model:** tanh in and out of the filter, not Serum's ladder. It matches level, not character.
- **The bench folder is gone.** To rerun device tests, rebuild and copy the binaries back.

## The restart

Done with Schwung's own `restart-move.sh`. It restarted the firmware (MoveOriginal 926 -> 4493) and stayed up across 75 seconds of checks. Two notes:

- That script does not bring `MoveLauncher` back, so nothing supervises Move until the next reboot. If Move crashes before then, power-cycle it.
- `schwung-manager` keeps its old process and still answers.

I could not see the screen, so I have not confirmed the module appears in the menu. From Schwung's source, the slot's module list reads the disk each time it opens, so the module should have been visible without a restart too.

## Still far off

From the validation set: `Keys - Can't Find You` (+53 st, -13 dB), `Keys - Zerb` (-25 st), `Pluck - NOTSOBAD` (-24 dB, sample oscillators), `Pad - BICEP` (-23 dB, cause unknown). `Keys - Hans Deeper` has an unreliable reference, because Serum cannot open its sample.

Remaining engine gaps: LFO and velocity routes (36 presets), Filter 2 (15), per-source filter bypass (10), sample oscillators and FM.

## Reproducing this

```
.venv\Scripts\python.exe -X utf8 test_render_calibration.py                   # fit, choose, validate
.venv\Scripts\python.exe -X utf8 test_render_calibration.py --stage validate  # one stage
.venv\Scripts\python.exe -X utf8 calibration\vital_check.py                   # Serum limit and same-patch checks
.venv\Scripts\python.exe -X utf8 calibration\vital_anchors.py                 # 3-way anchors
```

Logs: `calibration/calibration.log`, `calibration/vital_check.log`, `reports/move_deploy2.log`, `reports/move_restart.log`, `reports/move2_*.log`.
