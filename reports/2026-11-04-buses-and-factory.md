# FX buses, routing, and the factory bank (2026-11-04)

Nothing here is installed on the Move or committed.

## What was added
**Engine** (`schwung-tablor`, 315 params, native engine/fx/dsp tests pass, ARM `dsp.so` md5 `d7a78e830d7ec037d1f259f5642ff2c6`):
- Per-source sends to FX Bus 1 / 2 (`wtN/sub/noise/smp_bus1/2`, gain = pot/127), taken after the amp envelope, before the filter, not following velocity.
- Source routes: `*_post` 0 Filter, 1 Direct (= Serum's Master: past the voice filter, into the main rack), 2 None (buses only), 3 Out (= Serum's Direct: past the filter AND the effects).
- The voice filters' outputs as sources (Serum routing slots 5 and 6): `flt1_out` / `flt2_out` (Master / None / Out / Filter), `flt1_bus1/2`, `flt2_bus1/2`. `flt1_out` = Filter puts Filter 1 into Filter 2 in series.
- Two bus racks (`Engine::fxBus[2]`), state keys `b1.<fxkey>` / `b2.<fxkey>`; their output joins the main path ahead of the main rack. FX Enable and Space come from the main pots.
- Old presets are bit-identical (no sends, no new routes).

**Converter**: `CARRY_BUSES`, routing slot parse (`physical`: `route_none`, `route_out`, `bus`, `filter_dest`, `bus_nodes`), macro routes into send levels folded at the saved value, `map_bus_racks` (racks mapped like the main one, fingerprints under `<preset>#bus1/2`), a bus muted by an all-wet reverb with Level Out 0 drops its sends (Bass - Gravity), serial filter carried with Filter 2's envelope. `params_sig` ignores the new keys when zero.

**Tools**: `calibration/dev/probe_bus.py`, `probe_bus_sources.py`, `bus_audit.py`, `measure_bus_fp.py`, `run_g16.sh`; `import_pack.py` gained `measure_fingerprints` (covers bus racks), `quality()` and the gate `--max-level-gap 3` / `--max-band-rms 14`; NaN / '796s' crashes fixed; `deploy_presets.sh` takes `SRC=`.

## What Serum does (measured)
Slots 0-4 = osc A, B, C, noise, sub; 5, 6 = outputs of Filter 1, 2. Dest Filter / Master (into the main rack) / Direct (past the racks) / None. Send = level%^3 (all sources, any dest). A bus return goes through the main rack. Filter-dest on slot 5 = Filter 1 into Filter 2 in series: 169 of 626 factory presets (it was invisible in the census before).

## Results
- Bus audit, deep house (19 affected presets, closed-form volume, one note): octave-band error vs Serum 9.9 -> 8.3 dB (Rollin 12.8 -> 6.2, Nu Aspect 2 18.1 -> 5.9).
- Deep house chain report on the 16 presets the change touches: mean loss 17.09 -> 16.40 (10 better, 4 worse). **Shipped in the staged bank: the 9 that improved without a >15 dB HF error** (Billboard, Loop, HotLap, Lynnic, Rollin, Sleepless, oskar med k, Nu Aspect 2, Small Town Kid). Kept at the earlier version: Black Out (15.4 -> 21.2), Keys - Other Side (10.9 -> 19.4, HF -27 dB), Lead - Other Side (HF +34 dB), Space & Time, Tonic Walter, The Selected, Let It Go. The racks of Black Out / Other Side hold Split-branch EQs and SplitMS that are not carried, which is likely the cause.
- Factory held-out run (37 presets chosen from the 67 the census says carry with no gap; nothing tuned): 25 kept (mean octave-band error 9.2 dB rms, level within 1.2 dB after the loudness fit), 12 dropped by the gate (bands 11-30 dB off or level > 3 dB off). Deep house, same metric: 8.3 dB over the bus presets, 10-16 mean chain loss.
- Series filter on 10 factory basses: worse until Filter 2's envelope was carried; not re-audited since.

## Not done
Reverb build-up (engine tails ~5 dB under Serum's on played notes: structural), second distortion / compressor stage, filter before the distortion, Utils width in a bus rack, Filter 2 into Filter 1, sends moved by an LFO / envelope, curves on the send route, FM/RM/PD warps and the non-wavetable oscillator kinds (the census's biggest remaining groups).

## Install, when you want it
`BANK="Factory Picks" sh ./deploy_wavetables.sh`, `sh ./deploy_samples.sh`, `SRC="out_import/Factory Picks" BANK="Factory Picks" sh ./deploy_presets.sh`; Deep House presets are staged in `out/move/data/UserData/schwung/presets/tinyhouse` (= `calibration/g16_final`, previous set `g15_final`); the module needs the new `dsp.so` (`bash schwung-tablor/scripts/deploy.sh ableton@move.local`), and the new presets need it (a preset with bus keys on the old module just ignores them).
