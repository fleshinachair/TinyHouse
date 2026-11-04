# Patch 6: TinyHouse rename, +18 dB master range, per-stage FX switches

Installed on the Move (module `tinyhouse` 1.7.0-th, 70 presets, every checksum verified). Slot reload or a Shadow UI
restart is needed to load the new `dsp.so`; the old one stays mapped in a running slot (installed by atomic rename).
Not committed. Presets were installed first and `dsp.so` second so a reload can never pair old presets with the
louder engine (the old presets are 18 dB too loud on the new range; new presets are 18 dB too quiet on the old).

## Rename
- id `tinyhouse` (lowercase: Schwung requires `^[a-z0-9][a-z0-9-]*$` and slot files store the id, as TinyK's scripts
  note), name "TinyHouse", abbrev TINYH. Module dir `sound_generators/tinyhouse/`, presets `presets/tinyhouse/`.
- Stock Tablor factory sounds are gone: the module ships no `factory.tbl` (the engine seeds a preset folder from it
  on first load; a missing file seeds nothing). No engine change for that.
- `tablor-dh` was moved, not deleted, to `/data/UserData/removed-modules/tablor-dh/{module,presets}`. A saved Set
  that used it has lost that synth until it is moved back.

## Engine (all in src/dsp)
- Master range: `master = (pot/127)^2 * 0.8 * 10^(18/20)`, so 127 is +18 dB over the old 127. The default volume pot
  is 36 (the old 100). Converter `pot_volume` and clip guard use the same constant. Verified: five presets at old
  pot v on the old engine and v/2.82 on the new one differ by -0.7 to +0.5 dB.
- Five switches (enum Off/On, default On): `fx_on` (all), `eq_on`, `wide_on`, `dly_on`, `rev_on`, in their own
  "Switch" bank straight after Space (they must stay contiguous with the FX pots: `Engine::refreshFx` watches
  TB_P_DLY_MIX..TB_P_REV_ON). Off sets that stage's mix to 0 (the stage fades out and goes quiet, existing
  smoothing); the EQ bands keep running and only their output is crossfaded to the dry signal over about 10 ms, so
  switching never starts a filter from silence. With the EQ on the samples are unchanged.
- Tests (native and on the Move's ARM CPU, 0 failures): each stage off equals the stage taken out; FX Enable off
  equals every stage taken out; all-On equals default; switching off and on mid-note adds no step (compared with the
  steady sound on each side); volume 127 is 18.07 dB over volume 45. Two old tests were adapted to the new range
  (their volume pots divided by 2.82).
- The switches are part of each preset: the converter writes all five, On only for the stages the sound uses (EQ if
  it has a band, width/delay/reverb if their mix is above 0), and `fx_on` On. A preset load only sets the keys its
  state holds, so loading any preset resets the switches. Of the 70: EQ on in 61, width 45, delay 42, reverb 60. The
  sound is unchanged (a stage that is Off has no mix or no bands); `params_sig` leaves the switches out for that reason.
- The first install had no Switch page: the page list comes from `PAGE_MAP` in `tools/gen_params.py`, not from the bank
  table, so the five parameters existed but had no page. Fixed (page "Switch" after the EQ pages, before Global).

## Level
Re-levelled all 70 on the new range (`fit_level.py`): all 70 within 1.5 dB RMS of Serum's full chain (was 33),
mean RMS error 3.67 -> 0.74 dB (|dB|, 70 presets), limiter cost at most 1.3 dB, volume pots 24 to 127 (median 47);
only Bass - oskar med k still needs 127. Spectrum, width and loss are unchanged (a level-only change).

## Three pages and four new knobs (1.8.0-th)
Installed (presets, then `dsp.so`; checksums verified; native and ARM tests 0 failures). Slot reload needed.
- Pages on the device are now the root (Main: Cutoff, Resonance, Filter Env, Decay, Sub Level, Width, Space), Tone (WT1 Pos, WT2 Pos,
  Attack, Release, Drive, Low Shelf, High Shelf, Master Gain) and Effects (EQ, Width, Delay and Reverb Enable, Delay Time, Delay
  Feedback, Reverb Decay, FX Enable). The old pages (Osc, Unison, Shape, Osc 3, Filter, Env 1-3, Space, EQ, Global) are out of
  `PAGE_MAP` only: every parameter still exists, is saved in a preset's state and is on the web UI. There is no Preset knob:
  presets are Schwung's own store, browsed from the slot.
- New engine parameters, neutral at their defaults and written as neutral into every preset so a preset load resets them:
  `decay` (64 = as programmed; offsets the filter and amp decay by the same number of pot steps), `drive` (0 = off; a blend to a
  tanh of the signal after the master gain, ahead of the output clipper), `lo_shelf` / `hi_shelf` (+/-12 dB at 150 Hz and 5 kHz,
  64 = flat; a shelf at exactly 0 dB is skipped, so neutral is bit-exact). `volume` is labelled Master Gain.
- Tests added: Decay moves both envelopes and is the default at 64; shelf and drive behaviour; none of the knobs steps when thrown
  mid-note. Not yet done: Master Gain is the existing volume pot (no separate gain stage).
