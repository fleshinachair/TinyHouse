# Translation rules: what was measured on Serum, and how it is used

This is the running record of facts measured on Serum 2 (with controlled probe patches, DawDreamer renders and the scripts in `calibration/`) and of the
rules the converter and engine apply because of them. It is written as dated notes, newest topics last, and is meant to be read before changing a rule.
File and function names refer to this repo. Where a note says "not carried" or "NOT fixed", that is still open.

## The standing rule

The aim is that every preset is faithful to its Serum settings, so that other Serum packs can be imported and recreated automatically. So: no per-preset
fits against a loss function (`HYBRID_DRY_FITS = False` in `deephouse2move.py`), and no special-casing a preset by name. Allowed: global unit constants measured
with controlled Serum probe patches (`calibration/probe_*.py`), and a per-preset loudness (volume) fit plus the limiter-share cap. No preset is exempt:
`Bass - A Classic` was once kept from a hand-tuned file and is now translated like the rest, as every future import will be.

**Why:** the end goal is a drop-in Serum pack importer; per-preset tuning does not transfer to new packs.
**How to apply:** fix translation bugs at the rule level and verify with a probe against Serum before touching a preset. Judge changes on many presets, not one:
the chain report's per-preset loss moves by +-4 between renders of the same state.

## Measured facts and rules
Facts measured on Serum that are not obvious from the code (all now in `deephouse2move.py`):
- Modulation source ids: Env1..Env4 = 2..5 (not 1..4), LFO1..10 = 6..15, Macro1..8 = 25..32.
- LFO curve y is stored down from the top (value = 1 - y); a segment bend c gives travel x**p with p from a measured table.
- A route moves its destination by amount x modulator value (bipolar: half that, either way): level (in the sqrt/knob domain), cutoff
  (cutoff-knob units), coarse pitch 128 st per 100 %.
- Compressor: Serum's Makeup is a linear factor (gain = 20 log10(makeup), 1 is unity), omitted params default to threshold 0.5, ratio 4,
  attack 90, release 90; its knee widens with threshold depth and an infinite ratio is a limiter (lower threshold, narrow knee). The
  engine's Comp stage (after distortion) takes its threshold as a pre-master level that the converter sets once from the closed-form volume.
- Moog-type ladder filters (MgL24, MgL18) show no resonance peak up to 40 %; the converter's old resonance law was far too high.
- Source routing: RoutingSlot0..4 = osc A, B, C, noise, sub; absent means osc A to filter, the rest bypass; `kRoutingDestDirect` bypasses
  too. The engine has per-source `*_post` (Direct) params. A pack table with the same file name as a factory one ("Basic Shapes.wav" in
  Deep House vs Analog/) must lose to the exact path under Serum's Tables folder (`resolve_table`). Sub `kRoundRect` is nearly a sine
  (harmonics below -33 dB), not a square. Effects after a Split node (branch_nodes) work on one band: not master width/reverb.
- Filter FX moved by an envelope: the engine's FX Filter stage (`fxf_*`, after the distortion, follows the loudest voice's filter
  envelope) matches Serum within ~1 harmonic (calibration/probe_fxfilter_env.py). Modulator source 1 is not audible in a render.
- Filters with a Var knob (LH6) cannot be matched by any engine filter (7.5 dB off); `filter_model_key` looks up "MODEL@var" entries.
- Chorus/Flanger/Phaser are engine stages now (`ModFx` in fx.h, params `chor_*`, `flan_*`, `phas_*`, device page "Mod FX", converter
  `fxmap.modfx_params`; the old width fingerprint covers HyperD only). Measured on Serum: wet law as delay/reverb; chorus has 4 voices
  (2 per side), default Delay 5.0 ms / Delay2 ~5.2, default Filt is a 12 dB/oct LPF near 1 kHz, wet plateau about -3.5 dB; flanger Wet 100
  about -1.5 dB. NOT measured (guessed, marked in the notes): default Rate/Depth/Freq/Poles, depth-to-ms scale, flanger/phaser sweep
  ranges. Serum's noise osc can hold a one-shot click sample (short): left out. Macros that drive an effect's Wet are folded in
  (`fold_macro_wets`), 19 of 70 presets. `fit_level.py` (threaded) is unstable for some presets; `refit.py`-style sequential fit +
  `cap_limiter` is repeatable.
- Volume cap for the Deep House bank (2026-11-08): the converter's closed-form volume leaves some presets hotter than Serum's, and the engine's soft limiter
  (knee 0.7) then adds distortion Serum's render lacks (Keys - BUNT: EQ-only distortion floor about -47 dBc; volume 52 -> 40 takes its band loss 16.9 -> 7.8).
  `python calibration/cap_bank.py SRC DEST` runs `import_pack.cap_limiter` (the Factory importer's rule: at most 1.5% of samples reach the shoulder) over a
  converted bank. Measured on all 70: mean band loss 12.955 -> 12.636, presets under 14 dB 42 -> 44; 17 volumes change, Can't Find You 19.4 -> 6.4,
  Keys - BUNT 19.8 -> 7.8, Nu Aspect 2 11.6 -> 9.3; Lead - Self Control 14.3 -> 16.8 and Keys - Why U So 6.5 -> 7.9 get worse (the lower level drifts from Serum's).
  A knee of 0.98 in the engine was worse than the cap.
- Noise slot holding a sample (2026-11-08): Serum plays it at its own octave spectrum, 11.6 dB under the sample's rms at volume 0.75 (White at 0.75 is -22.4 dB),
  `calibration/probe_noise_sample.py`, 7 factory samples, bands within 1 dB. The converter used to read the sample name from the wrong node, so every such slot became
  white noise (Bass - Reiny: an envelope drove it to a +20 dB high-frequency floor). Now `sample_export.noise_standin` fits an engine noise (type, hp, lp, level) to the
  sample's octave levels. Measured on the 20 presets with such a slot: sum of band loss -7.1 (Reiny 18.7 -> 13.2, Hans Deeper 22.5 -> 17.3, 13 vs 12 under 14 dB),
  most others within +-1; 70-preset mean about -0.10. Narrow tonal samples (an organ, a balafon) are only roughly matched by a noise.
- Wavetable position is STEPPED, not blended, for tables whose WAV `clm ` chunk reads "<!>2048 0..." (frame = floor(pos*N); Default Shapes,
  Basic Shapes, DS Saw and Tri); "2..." tables interpolate. The engine reads the flag (`Wavetable::stepped`); tablor_cli.cpp loads tables
  exactly like the module (0.9-peak normalise + stepped) so level fits are valid. Modulation source ids: 1 = Mod Wheel (0 in every render).
- Serum's own window can be captured: `calibration/ui_shot.py "Preset" out.png --click "x,y;x,y,notches" --crop ... --scale ...` (DawDreamer
  open_editor; pops a window for ~10 s; tabs at window px osc 205,78 mix 275,78 fx 343,78 matrix 410,78). The title shows "Init" but
  the panels are the preset's. Crops at scale 0.6 are cheap.
- Sample oscillator (Sample/Multisample, 8 of 70 DH presets): `sample_export.py` turns the SFZ/audio into a .tbs set under
  out/move/data/UserData/UserLibrary/Samples/<bank>/ (Serum's own sounds: never commit or share; deploy with deploy_samples.sh);
  engine `sample.h` + params `smp_*`; the osc's Loop Start/End % override the SFZ loops; sample osc warps (HPF etc.), unison, own env
  release are not modelled. Level vs Serum varies by sample (-7..+3 dB), left at 1.0.
- FX audit: `calibration/fx_audit.py` (one effect at a time, Serum vs engine, on noise; run it after ANY effect-mapping change; covers Delay,
  Reverb, Chorus, Flanger, Phaser, EQ(+static Filter FX), HyperD; not yet Distortion, Comp, dynamic Filter FX). Its stereo check compares
  correlation change only (Serum's noise source has a random L/R level imbalance). Found: delay time is a STEPPED tempo-synced position
  (edges ~0.032/0.063/0.105/0.185 -> 1/16,1/8,1/4,1/2,1 bar at 120 BPM; OffsetL/R multiply; unwritten = 1/4); delay mix = stored Wet through
  the smoothstep (the fingerprint echo ratio failed whenever the assumed echo time was wrong); the delay's Freq/BW band-pass acts on the
  first echo (engine had it on repeats only; fitted DELAY_BW_SCALE 2.0, kDelayOut 1.17). 6 dB/oct Filter FX = fitted hishelf -24 dB.
  Remaining: reverb pre-delay/tail shapes, Filter FX drive and resonance, 2 delays (Deep In The Water, NOTSOBAD).
- Other fixes of 2026-11-03: multiband comp upward boost capped at 12 dB (it ran away in silent tails: first note after a pause came out
  loud); filter cutoff stepped in 4 pieces per block (a one-sample click at note start); measured BEND_POT and FILTER_DRIVE_POT tables
  (the straight lines were 3x too strong); output dither; compressor thresholds follow the fitted volume (calibration/comp_rescale.py);
  the deploy must also upload wavetables (deploy_wavetables.sh) or presets name files the Move lacks.
- Filter FX measured on Serum (calibration/probe_fxfilter_drive.py, probe_fxfilter_types.py [--hp]): an unwritten Reso plays as 0 (not 10);
  every LP/HP ladder type is that many identical one-pole filters at the STATED cutoff (MgL24/L24 4, MgL18/L18 3, MgL12/L12 2, MgL6/L6/default 1;
  H24/H18/H12/H6 likewise; DirtyMg/LadderAcid/ZDF_A taken as 3, unmeasured); two poles = a Q 0.5 biquad, an odd pole is fitted by a shelf. Default type
  with Reso lowers the passband (-2.8 dB at 10, -7.3 at 35, -14 at 100) and puts a peak at the cutoff (table in fxfilter_drive_probe2.json,
  `fxmap.fx_filter_reso`); Drive is a level-independent gain (+3.2 at 10, +7.7 at 25, +14 at 50, +23.5 at 100). The engine EQ keeps two LPs/HPs in a
  row (`reduce_bands`). EQ audit 50/61 -> 58/61 with these (the audit now ignores an all-over EQ gain: the loudness is fitted afterwards).
- Reverb: the audit's tail time is wet-dependent, so it compares the slope rt60 now (all of a preset's reverbs together). The old reverb tail
  colour read a fixed 0.3-1.3 s window: for short reverbs that is a numerical floor (white), so it read "bright", the bisect hit damp 20 and the engine
  tail came out ~30 dB too quiet (Rivo, Reiny, Atlantic...). `fxmetrics.tail_window` ends the window where the tail has fallen 35 dB; 23 short-tail
  fingerprints were re-measured. The chain report's >8k / 3-8k errors are mostly tiny absolute levels (-60..-130 dBFS): do not chase them as audible.
- Pads with a SECOND voice filter (MIND: filter2_enabled) are far too bright in the engine (1-3 kHz +18 dB): the second filter is not built.
- SECOND VOICE FILTER (built 2026-11-03, 15 of 70 presets use it): engine `flt2_*` pots + per-source balance `wt1_bal..smp_bal` (0 = Filter 1 only,
  the default; 127 = Filter 2 only), cutoff destination "Filter 2 Freq" (MD_FLT2_FREQ, ME_DST 23), Filter 2 follows the one filter envelope by its own
  `flt2_env`. Serum's FilterBalance (percent, 0 = middle, UNWRITTEN = -100 = Filter 1 only): Filter 1 gets (1-t)/2 and Filter 2 (1+t)/2 of the source
  (calibration/probe_filter2.py). A disabled filter passes its share unfiltered; a source in `kRoutingDestFilter` only. Unsupported F2 models (formant,
  comb, Diffuser) are left open. Modulation routes carry `dest_id` 0/1 for the two filters. An UNWRITTEN voice-filter Type is a 12 dB/oct low-pass
  (MgL12-like), not MgL24 as the converter had assumed (11 presets): `DEFAULT_VOICE_FILTER`.
- NOISE OSCILLATOR (2026-11-04, probe_noise_color.py, noise_survey.py; supersedes the older "unwritten = pink through a 950 Hz high-pass", which was Bass - Gravity's
  Pink / Color 0.75 left in the probe base's NoiseOsc3): the noise osc has Type (White / Pink / Brown / Geiger, unwritten = a noise flat to 1.6 kHz rising 3-5 dB/oct above)
  and Color (0.5 neutral). Color is two 12 dB/oct Butterworth filters: below 0.5 a LOW-pass with corner 460 Hz at 0.2 and x4.5 per 0.1; above 0.5 a HIGH-pass, corner
  460 Hz at 0.7 (0.6: 100 Hz, 0.8: 2 kHz); 0.9+ adds a rising tilt (not carried), 1.0 is silent. Engine: noise_type 0 White (gain x3.05 so it is 4.1 dB over Pink like
  Serum), 1 Pink, 4 Brown (leaky integrator, corner 90 Hz, 0.6 dB over Pink), 2/3 legacy Pink HP 950/1190; new pots noise_hp (0 off) / noise_lp (127 off), 20 Hz..20 kHz
  log. Converter: `noise_color_pots`, `NOISE_TYPE_ENGINE`; the unwritten type is carried as White minus 2.2 dB (shape rms 4-8 dB, LF too low). A noise SAMPLE
  (relativePathToNoiseSample) keeps `noise_type_for`. Self Control Noise 29 -> 19 loss, Can't Find You 11 -> 6, Keys Other Side 18 -> 12. One-shot noise (kParamOneShot) and
  Geiger are not carried.
- Voice-filter DRIVE in Serum is a plain gain (+3.3 dB at 10 %, +7.6 at 25, +12.5 at 50; a loud saw tops out near +13, a quiet noise reaches +24), every
  harmonic by the same dB; the engine's tanh Drive adds less gain and some harmonics. Kept: the engine pot up to its 25 % value (Bass - AVAION's 320 Hz band
  needs that bit of saturation) plus a gain stage after each filter (`flt_gain`, `flt2_gain`, 0..24 dB; `FILTER_DRIVE_MAKEUP_DB`). Checked end to end
  against Serum with calibration/check_filter2.py (8 synthetic cases, rms 0.6-3.5 dB on harmonics).
- The Distortion effect has its own 12 dB state-variable filter (Freq, BW = resonance, LPHP 0 = low-pass .. 100 = high-pass; PrePost 1 = AFTER the
  distortion, 2 = BEFORE, 0/unwritten off). Carried as an EQ band only when it is after (`fxmap.dist_filter_bands`: Origins, all things break, Movement);
  a filter before the distortion (AVAION: LP 144 Hz) cannot be an EQ band (the harmonics the overdrive makes sit above it), so it is not carried.
  Distortion modes/other params still unaudited; Distortion LevelOut (-6 dB unwritten, -15 at 0.3) is not carried (uniform: the loudness fit).
- The chain report's 3-8k and >8k band errors vary by 10 dB from run to run on the same state (random unison phases, aliasing at -75 dB): inaudible,
  never act on them; judge by audited effects and the 80 Hz-3 kHz bands.
- A route's SECOND source (ModSlot `source` = [main, aux]) scales its depth: a macro as aux multiplies it by the macro value (MIND's filter envelope is gated
  by macro 1 = 0, so Serum's filter stays at its base cutoff while the engine opened it; fixed in `physical`). Aux sources 16-21 (velocity-like) are not carried.
- Staged 2026-11-03 night (not installed): calibration/g15_final = g39 (all 70 refitted, mean chain loss 15.97, level |err| 0.89 dB; previous staged set in
  calibration/g15_before_f2), ARM dsp.so md5 24066037809d8edd40f914d37e4289fc (second filter, noise types, filter gain stage). A full refit takes ~25 min
  when nothing else runs (it takes 100 min with Serum/other jobs competing). Voice-section survey (calibration/voice_survey.py) is unreliable at high
  frequencies: the dither floor of a quiet engine render and Serum's digital silence read as 50-80 dB differences.
- g15 set (calibration/g15_final, staged in out/, not installed): mean loss 16.93 vs 17.69 for g14; cycle script calibration/run_g15b.sh
  (fit_level only refits names removed from level_fits.json; level fits are keyed by params_sig).

- DISTORTION (2026-11-04, `fx.h processDistCal`, params `dist_cal`/`dist_out`/`dist_level`, `fxmap.dist_params`, curves in calibration/dist_curves.json from
  probe_dist_transfer.py + calibrate_dist2.py, stage audit calibration/stage_audit.py). Serum's Wet is a SMOOTHSTEP s with the dry part (1 - s^2):
  out = LevelOut_gain * ((1 - s^2) x + s K f(G x)); LevelOut unwritten = 0.5 and its gain is (LevelOut / 0.5)^2 on the SUM (dry too). The curve is level-
  dependent (the old fit matched harmonics at one input level only): tanh / biased tanh (bias 0.003) / hard clip / sine (SineShaper, a folder) with G and K
  per mode and drive. Measured at the probe preset's default master, so the engine pots follow G x probe gain (10^(master_default_db/20)); the stage sits
  BEFORE the engine master gain, so `comp_rescale.rescale` moves dist_drive (+dB) and dist_out (-dB) with the final volume, like the comp thresholds.
  Modes mapped: default/SoftClip/TapeSat tanh, Asym/Diode2 biased, Overdrive/HardClip hard clip, SineShaper sine. Poor fits remain: Diode2 (5-8 dB), Asym at
  high drive (4-5), SineShaper (3-8; Rivo). Only the FIRST master Distortion is carried (SWEDISHMF and The Selected have a second).
- COMP thresholds were measured at the default master; Serum's master comes AFTER the FX, so thresholds in output dB shift by 20 log10(master / default
  master) (`master_vs_default_db`; Reiny master 1.0 = +11.8 dB). Before this the engine over-compressed every high-master preset (Reiny -11 dB vs Serum +3).
- `import_pack.finalize_level(m, it, serum, bank)` is the importer's level step. The comp thresholds and distortion gains work BEFORE the master gain, so
  their reference level is the volume at which the engine's VOICE (fx_on 0) matches Serum's voice-only render (`voice_level_error_db`, clamped +-12 dB), set
  once with `comp_rescale.rescale`; the loudness fit then moves the volume AFTER them (`fit_volume(follow=False)`). Following the fitted volume instead
  (what the scratch refit did before) was wrong whenever the full-chain fit deviated from the voice level for other reasons (Other Side: +12 dB because
  of effects the engine orders differently; its comp then over-compressed by 8.5 dB). Comp stage level error on the audit: 2.85 -> 1.83 dB mean.
  Chain loss is flat (15.8) because its per-preset noise is +-4; use the stage audits. a sequential refit script that calls finalize_level is the repeatable way to refit.
- Serum's route aux curve (`kParamAuxCurveData` on the slot) changes what an aux 16/17 does: Tonic Walter's aux-16 route at velocity 100 is the UNSCALED
  route (60 darker, 127 brighter); a static 100/127 scale made it 25 dB too dark. Aux 16/17 are left at full depth (tried and reverted).
- Serum's reverb Delay (pre-delay) param has NO effect on the wet timing at wet 100 (hall; 0 / 104 / 300 ms identical); do not add a pre-delay.
- Aux sources of a route (probe_aux_source.py, plain slot): 16 = velocity and 17 = note number scale the depth by x/127; 18, 19, 24 are closed at rest,
  20 and 23 constant, 21 and 22 random per note. None carried (see the aux curve note).
- Renders of the same engine patch vary by ~3 dB in level from run to run (random unison phases): average 3+ renders before drawing a level conclusion.
- The engine voice level vs Serum's at the closed-form master: median |diff| 3.6 dB, scatter +-8 dB (calibration/voice_level.py); the loudness fit moves
  the volume by up to +-20 dB from the closed form, mostly because the FX chain (comp, EQ) differs, so absolute stage levels depend on the rescale.
- Dynamic Filter FX audit (stage_audit.py --kinds Filter) is dominated by noise floors at HF (Serum voice has a flat -45 dB floor); not informative.
- Ping-pong delay (calibration/probe_pingpong.py): structure matches (L at T, R at 2T, L at 3T, -6 dB per echo at fb 50 %), but NOTSOBAD's narrow band-pass
  delay (BW 0.75) loses ~3.6 dB per pass in the engine's 1-pole HP+LP vs Serum's ~2.4 (decay rt30 0.37 vs 0.9 s); Sleepless's delay first echo is 5.7 dB
  too loud (the engine's 6 dB/oct skirt vs Serum's steeper one). Not fixed.

- ROUTE SOURCES (2026-11-04, `physical()`/`map_to_tablor` fold): velocity (16) and note number (17) are x/127 (probe_aux_source.py, probe_note_route.py);
  taken static at VELOCITY_NOMINAL 100 and middle C, and a note route to the filter cutoff is also carried exactly as key tracking (`keyfold`, slope
  a/127 x CUTOFF_NOTES per semitone). A BIPOLAR route is the source minus one half (a (x - 0.5)), NOT 2x - 1 (probe_macro_bipolar.py: macro 0/50/100 % gave
  -0.27/0/+0.22 of the knob at +50 %); the converter had 2x - 1 for macros (twice too deep). Macro-driven detune, filter reso and drive are now folded too.
  Lead - Ari went 27 -> 16 loss (its velocity route to the cutoff was ignored), Keys - Nyday 18.7 -> 13.5, I'll Be Yours 17 -> 7.
  Still not folded: Oscillator pan (note routes in Reiny), WTOsc warp (3 macro routes), the Env/Global-dest routes.
- Staged 2026-11-04 (not installed): calibration/g15_final = g54_final (dist cal, comp master correction + voice-referenced stage levels, velocity/note/bipolar folds,
  PWM/sync/EvenOdd warps, noise type+colour, random per-note noise pan, delay tempo sync dly_sync); previous staged sets in calibration/g15_before_dist (= g39) and g40..g46_final. ARM dsp.so md5 24066037809d8edd40f914d37e4289fc.

- Warps: kPWM and kSync mapped from probes (probe_pwm.py, probe_sync.py: PWM_BEND_POT, SYNC_POT); kEvenOdd at ~1.0 = the same wave an octave up (HotLap's lead played an
  octave low); kFM_OSC, kFM_NOISE, kFilterLPF/HPF, kDLM, kDistZeroSquare are not carried (about 8 presets). Sync Var (kParamWarpVar) and tables flagged spectral in the wav
  clm chunk (AT Spectrum: "11000000") are also not modelled. High-pass voice filters: the H24/H12 cutoff delta was +7 notes too high (probe_hp24_low.py, noise); now ~0.
- osc_survey.py (each oscillator alone, Serum vs engine) shows the wavetable/unison/tune engine matches within 0-3 dB for nearly every oscillator; remaining voice mismatches
  are the filter, modulation and the unsupported warps. voice_survey.py: trust 80 Hz-2.5 kHz only (HF is floor-limited).

- DELAY TEMPO SYNC (2026-11-04): Serum's delay times are note steps (62.5 ms .. 2 s at 120 BPM). Engine `dly_sync` On scales dly_time / dly_timer by 120 / host BPM
  (Schwung's get_bpm, clamped 40..300; refreshFx re-reads it); the converter writes dly_sync=1 whenever a delay is carried. The CLI renders at the default 120 BPM, so
  every audit and loss number is unchanged by it. Test: tests/test_engine.cpp testDelaySync.
- The noise oscillator is mono with a RANDOM per-note L/R level imbalance in Serum (corr 1.000, side/mid -13..-29 dB, mean |L-R| 2.3 dB); the engine now draws a pan
  of +-0.27 per note (`noisePan`). Without it noise-only presets had a side/mid error of 65 dB in the chain loss (Self Control Noise, Keys - Loop).
- The chain loss's side/mid term (0.25 x |dB error|, no floor) is dominated by cases where BOTH sides are nearly mono (-30 vs -80 dB: oskar med k, InDeep); not audible,
  do not chase it. The effect audit's delay "first echo" check only runs when an echo is found above -35 dB on the engine side, so some old OKs were unchecked.

- AUX GATES (2026-11-04): a route whose second source is the mod wheel (1) or 18/19/24 (closed at rest) is OFF in a static preset (amount 0); 21/22 (random per note) take
  half the depth. The converter used to carry the LFO route in full: Bass - The Selected (an LFO gated by the mod wheel into the filter) went 24 -> 11 loss, Keys - Billboard
  (a vibrato gated by a random aux) 17 -> 10.5. A MAIN-source random route (21/22 straight to table position etc.) folded at its mean did NOT help (Atlantic, Ari worse): left alone.

- Routes into an envelope's SUSTAIN (macro, velocity, note) are folded into the stored sustain (Bass - Let It Go: velocity +73 % into the filter envelope held its filter open;
  loss 40.7 -> 37.4). Attack / decay / release are log time knobs and are not folded. A route with an envelope or LFO as the aux gate takes half depth (two vibrato routes:
  Self Control 25.6 -> 21.5). Chain-report loss moves by up to +-5 on presets with HF energy near the floor (Keys - BUNT) for a 3-pot change: judge a change on several presets.

- VELOCITY -> AMPLITUDE (2026-11-04): Serum's loudness is flat in velocity unless a route (source 16 -> Global Voice Amp) puts it there; with the route it is linear
  (20 log10(vel/127)). The engine's original law (v 25^v 0.04, params vca_vel default 100) made every converted preset follow velocity steeply while Serum's did not
  (the loudness fits all hid a -6.4 dB at velocity 100). New `vca_vlaw` = Serum: gain 0.4856 x ((1 - s) + s vel/v100), anchored at velocity 100 so every fitted level holds;
  converter writes vca_vlaw 1 and vca_vel 0 (127 for the four presets with the route: Keys Nyday / Other Side, Lead Sleepless / Space & Time). Test: testVelocityLaw.
  Filter-envelope velocity (flt_vel) stays 0. Velocity routes to filter/osc are static at velocity 100 (VELOCITY_NOMINAL).

- DELAY FILTER (2026-11-04, probe_delay_filter2.py, third-octave response of 8 Freq/BW pairs): a 1-pole high-pass + 1-pole low-pass (6 dB/oct) with UNITY gain at the centre;
  corners at Freq x 2^(+-0.8 BW) (DELAY_BW_SCALE 1.6; 2.0 was the older coarse fit). The engine's pair lost R/(1+R) at the centre (2.6 dB per pass at BW 0.75, so narrow
  echoes died faster than Serum's); fx.h processDelay now applies the make-up (1 + R) / R. Delay audit 32/35 (31 before, 33 at the start of the day, one of whose OKs was an
  unchecked missing echo). NOTSOBAD (ping-pong, BW 0.75) first echo -16.5 Serum vs -22.7 engine (was -25.0 / none found); its second delay node is still not carried.
- Another DC fix: the calibrated distortion removes the DC of its whole output (8 Hz one-pole; Bass - Rivo / Gravity / NOTSOBAD had 0.02-0.08 DC at soft velocity).

- REVERB on played notes (calibration/reverb_stage_audit.py, reverb_tail_fit_probe.json; PARTLY FIXED 2026-11-04: wet gain kRevOut 1.50 -> 2.25, tail medians now -0.9 / -2.8 / -2.1 dB re Serum in the three windows instead of -1.9 / -4.5 / -3.7, sustain add +0.2 dB; line length scaling x1.6 / x2.4 did not help; 3.0 centres the tails but makes steady noise 5.5 dB hot; measured with calibration/reverb_engine_only.py; the demo-MIDI level of Other Side / Can't Find You / Let It Go / Self Control did not change, so their quietness is not the reverb): the engine's reverb tail is a median 5 dB under Serum's on real notes
  (up to -20 on bass / long pads), although the effect audit (noise burst, rt60 slope) says OK. On a wet-100 burst the two envelopes have the SAME late slope but Serum's
  holds its peak 50-100 ms longer and decays more gently right after it (MIND: Serum -2 -3 -4 -5 dB at 240-400 ms, engine -3 -5 -7 -9; Lynnic and HotLap 4-5 dB), and on a few
  (Scorpios) it is silent for the first 40 ms: a slower build-up that grows with Size. Matching the tail LEVEL with the decay param would need rt60 of 5-20 s, so the fix is
  structural (a build-up / pre-delay in processReverb, sized from Size; Serum's own Delay param has no effect on the wet timing). It also explains the audit's "echoes at
  0.17-0.25 s missing". Bass tails are the worst (-10 dB at 40 Hz even in mid bands OK). Bus-rack FX (13 presets: bus-1 reverbs in Keys - Loop, Gravity, Sleepless, Tonic Walter;
  EQ / HyperD / Distortion / Utils buses elsewhere) are not carried either: Keys - Loop's whole reverb tail is a bus send (macro -> RoutingSlot FXBus1Level, with a curve in
  the slot's flex data).

- ROUTING AND FX BUSES (2026-11-04, probe_bus.py, probe_bus_sources.py, scratch probes of the rack-0 Filter): Serum 2 RoutingSlot 0..4 = osc A, B, C, noise, sub; slot 5 = the OUTPUT of
  Voice Filter 1, slot 6 = of Voice Filter 2 (slot 5 = None silences a source routed through Filter 1). Dest: Filter (through the voice filter), Master (past the voice filter, INTO the main
  rack: what the converter always called Direct), Direct (past the filter AND the main rack: straight to the output), None (the buses only); on slots 5/6 Master / Direct / None, and
  "Filter" = Filter 1 into Filter 2 IN SERIES (169 of the 626 factory presets: it was invisible until now). Sends: level%^3 in amplitude, same unit as the main path, taken after the amp
  envelope and before the filter for a source (velocity-independent), after the filter for a filter output; every source, any dest. A bus rack's output joins the main path AHEAD of the
  main rack (a Filter FX in rack 0 filters what a bus returns). Unwritten send = 0 (a bus rack with no send is inactive: 8 of the deep house presets' bus racks). A macro route into a send level
  (RoutingSlot kParamFXBus1Level, dest_id = slot) is folded at the saved macro value. Engine: params `wtN/sub/noise/smp/flt1/flt2_bus1/2` (send = pot/127), routes `*_post` 0 Filter /
  1 Direct(=Master) / 2 None / 3 Out, `flt1_out` / `flt2_out` (Master/None/Out/Filter), the buses' racks are state keys `b1.<fxkey>` / `b2.<fxkey>` -> Engine::busPots / fxBus[2]; converter
  `CARRY_BUSES`, `map_bus_racks`, bus racks fingerprinted as '<preset>#bus1'. NOT carried: Utils width in a bus rack, Filter 2 -> Filter 1 serial, a send moved by an LFO / envelope, curves on the
  send route. Deep house (bus_audit.py): band error vs Serum 9.9 -> 8.3 dB over 22 affected presets (Rollin 13 -> 6, Nu Aspect 2 18 -> 6); factory sample is noisier (samples, distortion).
- Importer gate (2026-11-04): `import_pack.py` drops a preset whose finished level is > 3 dB or octave bands > 14 dB rms from Serum's (a gate on the result, not a fit); "Factory Picks" = 25 of 37
  tried (mean 9.2 dB bands). Gotchas: the converter's main flow reads presets back through JSON (dict int keys become strings: use `int(k)`), `fit_level.py` skips names already in level_fits.json
  (delete the entry to refit) and crashes on names with an apostrophe (file has a curly one). Deep house g16_final adopts the bus conversion only for presets whose chain loss improved. See reports/2026-11-04-buses-and-factory.md.
- `dist_params` now carries the master Distortion that does the MOST (wet x drive; unwritten drive 25), not the first in the rack; a first node that was off used to end the search.
  Bass - SWEDISHMF (19 % TapeSat ahead of a 100 % drive-72) and Bass - The Selected (a drive-0 filter ahead of a soft clip) changed. The engine still has one distortion stage.

- Oscillator level residuals (osc_survey.py, 148 oscillators, engine - Serum against the master law): single voices -2.7 dB, stacks +3 dB at 2 voices falling 1.6 dB per doubling to
  -2 at 16 (`unison_level_db`, switch UNISON_LEVEL_FIT, OFF): applying it to twelve mixed presets made the chain loss worse (14.8 -> 15.3), so it stays off. A plain saw alone is
  consistently -2.5 dB. Voice-only level (voice_level.py) scatter vs Serum is +-8 dB and is absorbed by the loudness fit.

Banks: a bank is a folder under `/data/UserData/schwung/presets/tinyhouse/`; loose files are the "User" bank; the Bank knob is the
8th knob of the Main page. Converter flags `--bank`, `--supported-only`; `import_pack.py` imports a folder of Serum presets
(factory: `...\Xfer\Serum 2 Presets\Presets\Factory`, 626 presets). Deploy with `BANK="Deep House" ./deploy_all.sh`.

## Added 2026-11-04 (evening): LFO bends and the Fine stop, the formant filters, real-playing checks

- **LFO curve bends** (`calibration/probe_lfo_bend.py`, a 4-point curve at 0.5 Hz driving Fine pitch, the value read off the pitch every 0.1 s). `curveVals[i]` bends the segment that STARTS at
  point i; the last entry has no effect. For a rising segment c > 0.5 eases in (stays low longer: at 0.7 the value at t = 0..0.875 of the segment is .01 .02 .05 .10 .17 .30 .52 .87, not a pure power),
  c < 0.5 eases out (0.3: .33 .61 .77 .87 .93 .96 .99 1.0); for a falling segment c > 0.5 stays high longer (0.9: ~1.0 until t = .6, then drops), c < 0.5 drops early. This is the convention
  `lfo_shape_pots` already used; the exponent table (`LFO_BEND_C/LOGP`, measured to c = 0.8) is only a power-law approximation of these shapes.
- **Serum's Fine knob stops at +-100 cents, and the stop applies to the knob value with the modulation added.** An LFO into Fine at -100 % moves the pitch -200 cents per unit of LFO but the pitch
  holds at -100 once the LFO passes half its height (Lead - Lynnic's LFO3: -100 cents from about 1.8 s, held until the curve falls back at 3.4 s). The engine's tune route has no stop (route amount 55
  gives -97.6 cents, 51 gives -204: checked with a constant LFO on one oscillator), so `map_lfos` builds the stop into the LFO's table and ends the route's depth at the clipped swing, for an LFO whose
  routes are all unipolar Fine routes of one depth (`clipped`). Before this Lynnic swung two semitones. Other destinations are not clipped here; Coarse and Octave have much wider ranges.
- **Formant voice filters** (`calibration/probe_formant.py`, saw on note 48 through Voice Filter 1; `probe_formant.json`): FormantTWO is three formants (F1, F2, F3) with peaks of about -5, -2.7 and
  -1.5 dB over a floor near -15 dB (the mean transfer is -15 dB). They move with both Var and Freq. At Freq 0.1277: Var 0 gives 392 / 2093 / 2747 Hz, Var 12.85 392 / 2224 / 3009, Var 50 523 / 2747 / 3532,
  Var 100 785 / 4317 / 5494. At Freq 0.35: Var 0 523 / 1831 / 2616, Var 100 1047 / 3924 / 5363. At Freq 0.55: Var 0 654 / 1701 / 2485, Var 100 1439 / 3532 / 4971 (the harmonic spacing of 131 Hz limits
  the resolution). Key tracking moves them with the note. NOT BUILT in the engine: it needs a filter type of three parallel resonators fitted on a noise-driven measurement; a static EQ cannot stand in for it
  (Lead - Kisaya sends half of every source through Filter 2 = FormantTWO with key tracking on). `voice_filter_pots` flags a formant model as critical for either filter, so `import_pack.py` drops such presets.
- **Bode** (the frequency shifter) is not the cause of Lynnic's or Kisaya's differences: in both its Wet is 0 unless a macro raises it. Serum itself, with Bode switched off, moves Lynnic by 1.2 against a noise floor of 2.8.
- **Noise samples**: Lynnic's noise oscillator plays `Organics/S-Sound.wav` (an "S": almost all its energy at 4 to 9 kHz, spectral flatness 0.07) where the engine plays white noise. Shaping the engine's noise
  to 4 to 10 kHz with `noise_hp` / `noise_lp` changed the match by nothing measurable (loss 6.5 against 6.7): not worth a rule on its own.
- **Real-playing check** (`calibration/midi_ab.py`, `reports/2026-11-04-demo-midi-ab.md`): the pack's own demo MIDI through Serum and the engine. A held test chord hid several things this shows: level errors of
  3 to 7 dB on some leads, a spectral centroid up to 2.5x off, and sub-bass the engine has and Serum does not. Use it after any change that could move a preset's sound. `tablor_cli` plays a
  performance when `TB_EVENTS` names a file of `on_s off_s note velocity` lines.

## Added 2026-11-04 (night): route curves, key tracking checked, four reports from the Move

- **A route has its own curve** (`kParamMainCurveData` on, the points in `flex[0]`; `route_curve` / `curve_value` in `deephouse2move.py`, `fxmap.macro_value`). The route's value is the macro's position
  put through the curve, and the curve gives `1 - y` like an LFO shape (measured on Keys - Loop: its macro OPEN at 35 % drives the bus-1 send through a curve from 0.726 to 1.0, so the send is 0.82, not
  0.35; the reverb tail in Serum rose -39 / -33 / -24 dB at macro 0 / 35 / 100, as 1 - y plus the opening filter predicts). Before this the send was `0.35^3` instead of `0.82^3`: a 23 dB shortfall of the
  whole bus, which in Loop IS the reverb and the stereo width. After it, on the pack's demo MIDI, Keys - Loop's side/mid is within about 4 dB of Serum's below 1 kHz (was 20 dB under), the L/R correlation
  0.957 against Serum's 0.924 (was 0.999) and the tail 5 dB under (was 25). Only Keys - Loop changes in the Deep House bank (the other presets with curved routes are on the pre-bus conversion).
- **Key tracking is right** (`calibration/probe_keytrack.py`, MgL24, cutoff 0.45, tracking 1.0): the engine's -12 dB corner equals Serum's from note 42 up and is within one harmonic step (523 against
  491 Hz at note 24) below. It is not the cause of the low-register differences reported on Lynnic and Gravity.
- **Tempo-synced LFOs** (found on Lead - Loop, NOT FIXED): an LFO with `kParamTriplets` and no `kParamBeatSync` stored has a Rate of 14.7 that is a beat position, not hertz; `map_lfos` read it as 14.7 Hz
  and the engine flutters at about 15 Hz where Serum runs a gate at a note value. Needs a probe of Serum's synced LFO (what an unwritten BeatSync means, the division at each Rate, triplets / dotted) and,
  to follow the Move's tempo, an LFO sync switch in the engine like the delay's `dly_sync`.
- **Keys - Other Side's second sound** (NOT FIXED): in Serum Filter 2's output goes to FX Bus 1, whose rack is EQ -> SplitMS (with EQs in its branches) -> EQ. The shipped preset has no sends and no bus rack
  (the earlier conversion was kept because the bus one measured worse), and on the demo MIDI it is about 10 dB under Serum at 4 and 8 kHz. The bus conversion (`flt2_bus1` 127) is worse: 4.6 dB too loud, 20 dB under
  at 4 to 8 kHz. SplitMS and EQs inside a Split branch are not carried; they are what is missing.
- **Bass - Gravity** (NOT FIXED): on the demo MIDI the engine is 5 to 7 dB under Serum at 500 to 1300 Hz and 4 to 11 dB over at 2.5 to 5 kHz, a missing low-pass or tilt; the cause is not yet found
  (its rack: HyperD, EQ x3, Distortion, Comp, Filter, EQ, Reverb; bus 1: Reverb, EQ, EQ).
- **Lead - Lynnic's bass against hiss** (NOT FIXED): before the EQ the engine's voice has about 7 dB less of 20 to 120 Hz and 3 to 4 dB more of 0.3 to 8 kHz than Serum's; the EQ stage itself agrees (cuts within
  3 dB). Sample oscillator (white noise through a HPF warp) and noise oscillator were each checked: both add the same amount in the engine as in Serum.

## Added 2026-11-04 (late): note starts and the "declick" report (engine change, NOT yet on the Move)

- **Engine attack floor is now 3 ms** (`potAttackTime` in `param_map.h`; was 0.5 ms). Serum's own note start is never instant: on the Loopsy pack played through Serum, presets with a stored attack of
  0 to 6 ms still take about 1.5 to 3 ms to reach full level. `tests/test_engine.cpp testNoteOnset` fails on the old floor (the first 8 samples reach 24 % of the peak) and passes on the new (5 %). Attacks
  longer than about 3 ms are unchanged. Built into the native tests only; the ARM `dsp.so` has not been rebuilt or installed for it.
- **A note taking over a voice does not cut it** (checked in a scratch test, not kept): with one voice, a note arriving over a releasing voice carries straight through (the sample before and after the join
  were -4660 and -4564 with a plain table, and the same with the sub oscillator or a low-pass), so voice stealing is not the source of a click. `Engine::noteOn` restarts the envelope and filters, but
  the audible join is smooth.
- **Not explained**: on the pack's demo MIDI the presets with quick notes step harder at a note start than Serum does (95th percentile of the largest sample-to-sample jump in the first 10 ms, against the
  signal's own level): Lead - I'll Be Yours 9.4 against Serum's 2.6, Lead - Nyday 5.9 against 1.7, Lead - Rollin 1.6 against 0.5, Bass - Gravity 5.8 against 3.1. Their attacks are far longer than the floor,
  so the floor does not explain it. Keys - Loop's demo MIDI is four slow chords and shows no click at all in either render. Needs the exact preset and moment where the click is heard.

- A route's `kParamOut` (saved on 40 of the pack's 951 slots) is its output scale in percent, default 100: depth = amount x Out / 100 on every destination (probe_route_out.py: coarse pitch swing 11 st at 10 % with no Out, 2.1 st at Out 20, 0.1 st at Out 1; filter cutoff centroid swing 63 / 30 / 6 Hz at none / 50 / 10). Not applied to velocity / note-number aux routes (Serum renders those the same either way).
- An LFO whose `kParamType` is `RandomSH` has no period, so it is built as a flat table at its mid level instead of a sine (`map_lfos`): a bipolar route then adds nothing and a unipolar one a steady half-depth offset. Lead - Other Side routes a 26 Hz Random S&H into oscillator level at 46 % bipolar; the sine put a steady 26 Hz tremolo on it (the user heard a warble where Serum is flute-like): envelope swing std 5.35 -> 4.17 dB, range 25 -> 17 dB, against Serum's 3.0 / 16. Lorenz and Rossler LFOs are left as before (Pad - Nu Aspect 2's Rossler route scored worse flat). Self Control and Self Control Noise carry the same pattern with no measurable change.
- The per-source Filter 1 / Filter 2 balance (`ph["balance"]`) was read with integer keys after the command-line converter had written and re-read each preset as JSON (keys are strings), so every source was sent to Filter 1 in a CLI run: Lead - Other Side's noise (Serum sends it to Filter 2) came out through the wrong filter, 20 dB too loud and full of low end, with Filter 2 left open. Fixed with an int() conversion; it changes 10 presets in a fresh conversion. The shipped set came from a path that kept ints, so it already had the balance.
- A Utils width inside an FX bus rack is carried as that rack's own width (`b1.mwidth` / `b2.mwidth`, the engine scales the side of what the bus returns, smoothed), not dropped: Lead - Rollin's bus 1 narrows to 22 %, and without it the engine's side/mid was +3.3 dB against Serum's -3.1 (now -1.4). Mono-below inside a bus rack is still not carried.
- A HyperD ahead of a hard-driven Distortion (enabled, real voices, detune and wet; distortion wet and drive >= 50) is carried as the engine's Hyper stage (`hyd_uni/det/rate/wet`, `fx.h` class Hyper, runs first in the chain, so the distortion clips the moving voices together instead of a steady saw). Serum's values map directly (percent x 1.27). The stage's depth/rate laws (`kDepthMs`, `kDepthExp`, `kRateHz`, `kRateExp`) are a ROUGH fit to an isolated Serum HyperD (FM at high detune is about 4x too strong); Lead - Other Side's breathing and band profile improve (band rms 8.8 -> 5.3 dB), Lead - Rollin gets darker and closer (centroid 1274 -> ~860 Hz, Serum 741) but its breathing is still weaker than Serum's: revisit.

## Added 2026-11-08 (factory import, branch `factory-import`)

- **A dry Distortion / Comp / Bode / Filter / Utils / Split does nothing** (`fxmap.map_fx`, `DRY_KINDS`): a node with a stored `kParamWet` under 1 and no envelope / LFO / velocity route to its Wet is bypassed, so it no longer blocks a preset ("not carried: Distortion" 38 -> 13). Checked on Serum for 122 factory presets (calibration scratch `serum_dry_ab`: the preset's own render against the same render with every such node disabled, 3 renders each): octave-band rms between the two median 0.85 dB, 90th percentile 2.9 dB (the render-to-render noise of the same preset is 1.5 / 8.4), only the level differs, by a median 0.3 dB and up to 8.5 dB for a few (the node's LevelOut / Makeup still scales the signal: a uniform gain that the loudness fit absorbs). Modulation effects are not in the rule (a flanger at Wet 0 still adds side).
- **A Distortion / Comp / Bode / Filter / Utils / Split that stores no Wet is fully wet** when a macro route to its Wet is folded (`fold_macro_wets`); the default base was 0, which turned a "-100 % x macro at 0" route (ARP - Aardvark's Bode, FX - Church's and PD - Butterbrot's Comps) into a dry node: Church's second Comp, with its +4 dB makeup, measurably plays (5 dB louder than with the node off).
- **Macros routed into an EQ band's Gain / Freq and into a Distortion's Drive are folded at the saved macro value** (`fxmap.fold_macro_knobs`; before this only a Wet was folded, and ~150 factory presets route a macro to an EQ gain or frequency). Measured on Serum with noise through the effect and a route from Macro 1 at 100 % (`calibration/probe_fx_macro.py`): the EQ gain knob spans -24..+24 dB (a route adds 0.48 dB per percent, clipped at the ends: 25 % from 0 dB gave +11.9 dB, 50 % from -12 dB +11.8, -50 % from +6 dB -18.3); the EQ frequency knob is logarithmic from 21.5332 Hz (the value Serum stores for the knob's left end) to 20 kHz and a route adds its amount to the knob position (+10 % from 200 / 800 / 3000 Hz = +1.02 / +0.87 / +1.01 octaves, +25 % from 3 kHz = 16.9 kHz); a Distortion's Drive is a percentage and the route adds percentage points (drive 10/25/50 with +20/+40/-10 % at macro 100 % matched the static drive of the sum on the 3rd harmonic of a sine to 0.1). A bipolar route is the macro minus one half. PL - Camp Fire 1's "HIGH CUT" macro opens a low-pass stored at 21.5 Hz; carried as stored it was a 28 dB loss (octave bands 28 dB from Serum), folded 4.3 dB. Not yet folded (measured routes exist on 30 to 40 presets each): Comp Makeup, Filter Drive / Reso, Distortion LevelOut / Freq, Reverb Size / Delay, Delay Feedback.
- **A voice filter with Wet under 1 % is off** (`physical()`, `vf_wet`): Serum's voice filters have a Wet (dry / wet mix of the filter, percent, unwritten = fully wet). 104 factory voice filters store Wet 0 (66 Filter 1, 38 Filter 2) and 120 macro routes (the "filter on / off" macros of the factory banks) move it, which the converter ignored: a filter that is dry at the saved macro position was carried as an active filter (BA - 303 Reality: a MgL12 at 424 Hz in series with a LadderEMS; its voice was 33 dB too dark at 320-640 Hz, octave bands 22.3 dB from Serum) or, with an unsupported model (CombP, Diffuser, BandReject, Wsp, formants), blocked the preset. The routes into the Wet are folded at the saved macro (bipolar = macro minus one half), the nominal velocity / note and the mod wheel at 0; an envelope / LFO route leaves the filter as it is. A Wet between 1 and 99 % (52 factory filters) mixes the unfiltered signal back in, which the engine's voice filter cannot do: noted as approximate. Checked on Serum for the 124 factory presets that have such a filter (calibration scratch `vf_wet_ab`: the preset render against the same render with the filter disabled, 3 renders each): octave-band rms median 0.83 dB, 105 of 124 within 3 dB, 120 within 6 dB (render-to-render noise of the same preset: median 1.1, p90 4.1); the worst are the three Kick presets with a BandReject filter and BA - Vintage Spread's Filter 2. The level does change (median 0.5 dB, p90 5.5, up to 10.6): a filter at Wet 0 still applies its Drive (a plain gain, +12.5 dB at 50 %) and LevelOut to the signal, a uniform gain when every source takes the same path (the loudness fit absorbs it) and an error in the balance when sources are split between the filter and the bypass: not carried.
- **A wavetable stored inside the preset is carried** (`write_embedded_table`): a table edited in Serum's wavetable editor is saved as `embeddedWTData` (a flat list of 2048-sample frames) with no table path, and the converter fell back to the engine's "Init" pulse table. 34 oscillators of the factory presets do this (BA - 303 Reality / Punchier / Dirty Macros, OTT Saw Wave, The Even Odds, KY - Delicacy...). The data is written as a float WAV with a Serum-style `clm ` chunk (frame size 2048, interpolation digit 0 = stepped, the digit of Default Shapes) under `Wavetables/<bank>/embedded/embedded_<hash>.wav`, which the engine's loader already reads. BA - 303 Reality, oscillator alone through Serum and the engine (filters open, octave bands re the loudest): Serum -75 -71 -64 0 -6 -8 -10 -12 -15 -18, engine with the Init table -74 -70 -63 0 -37 -10 -12 -15 -19 -27 (a pulse: no 2nd harmonic), with the embedded saw -76 -68 -62 0 -6 -8 -10 -13 -17 -24. Whether Serum steps or crossfades between the frames of an embedded multi-frame table is not measured (stepped assumed); the 2-frame tables are driven by a macro, folded at its saved position.
- **More voice-filter models are fitted against the engine's filter types** (`calibration/fit_filter_model.py`, `filter_models.json`, `filter_res.json`): H18 (HP12, 0.9 dB rms), HP12 (HP12, 1.0), BP12 / BN12 / BPN12 (BP12, 2.5), MgL18 (LP12, 1.1; the LP24 it was approximated by was 2.0), MgL6 (BP12, 2.3; LP12 3.9, the same pick as the existing L6), LadderMg (LP24, 1.8). The fit now searches the engine's eight types (notches included). Factory census (converter only): tier A 67 -> 87, tier C 417 -> 390, none lost; most of the tier A gain is a model that was an approximation (a note) becoming a measured fit, and only one tier A preset (LD - Sawblade Rush, Filter 2 H18) used a model that was unsupported before. Voice-only Serum render, level / octave-band rms before -> after (import_pack's own gate numbers): LD - Sawblade Rush (H18; was "left open") -0.01 / 9.5 -> -0.16 / 9.9; PL - Alive (MgL18) -0.04 / 7.2 -> -0.17 / 6.3; BA - Dual MG Bass (MgL18) +0.13 / 13.0 -> +0.41 / 12.1; BA - Quick Bark (MgL18) 35.4 -> 35.8 (fails both ways); SY - Magnetic (MgL6) +0.66 / 17.3 -> -1.66 / 9.0; ORCH - Cinematic Longing (MgL6) -0.18 / 8.9 -> +0.33 / 10.3 (the one worse, within the 1.1 dB render-to-render noise); BA - Ladders (LadderMg) +0.35 / 9.6 -> +0.17 / 9.5; PD - Butterbrot (LadderMg) -0.60 / 8.9 -> -1.14 / 5.8; SY - Plastic Player (LadderMg) +0.45 / 5.8 -> +0.85 / 5.8. **LadderEMS is not adopted**: its fit (LP12, 1.5 dB rms on the held saw, cutoff pots 27 lower) made SEQ - Funky Acid -1.2 / 13.7 -> -3.9 / 23.7 and 808 - Simple Electro 59.9 -> 64.3 worse (CH - Dubtron 21.2 -> 19.4 better), so the earlier LP24 approximation stays. HP12 and BP12 appear in no tier A factory preset, so they are fitted but not yet checked by a render.

## Added 2026-11-08: a route's aux curve, and the second filter's own envelope

- **A route's aux curve** (`kParamAuxCurveData`, points in `flex[1]`; `calibration/probe_aux_curve.py`, noise through a closed MgL24 with Env2 on the cutoff, the route's depth read by inverting a scan of the amount). The aux
  source's value x is put through the curve (value = 1 - y, like every Serum curve) and that REPLACES the straight x: a macro at 0 / .25 / .5 / .75 / 1 on a curve 0.8 -> 0.2 gave 0.20 / .35 / .50 / .66 / .79, on
  Lead - Billboard's own 4-point curve 0 / 0 / 0 / .10 / .48 / .61 at 0 / .2 / .4 / .7 / .85 / 1 (predicted 0 / 0 / 0 / .11 / .49 / .62), on Tonic Walter's and Can't Find You's likewise; velocity and note number (x = value / 127) on a
  (0.43 -> 0) curve gave 0.64 / .77 / .90 / 1.0 at 20 / 60 / 100 / 127; a plain slot is x itself. The random aux sources 21 and 22 are uniform 0..1 per note (24 notes: mean 0.57 / 0.48, range 0.04..0.98), so on a curve the depth is the
  curve's mean (Lead - Other Side's Env3 -> Filter 2 route, curve 0.274 -> 0.166: 0.73..0.81 per note, mean 0.78, where the converter carried 0.5). This is what Tonic Walter's "unscaled at velocity 100" route was (its curve
  is 0.87 there) and why a plain 100 / 127 made it too dark. `deephouse2move.aux_curve` / `aux_scale` fold every aux (macro, velocity, note, random, envelope / LFO gate) this way; before, 16 / 17 were left at full depth.
  Changes 7 of the 70 Deep House presets (Billboard's random-gated vibrato falls to about 0.05 of its stored depth: the curve is 0 below x = 0.62).
- **Voice filters on white noise** (`calibration/probe_voice_filter_noise.py`, engine through the real converter): the static laws are right. MgL24 / L24 / MgL12 / L12 at cutoff knob 0.2 .. 0.8 agree with Serum within 2-6 dB wherever the
  response is above -60 dB (below that the engine's 24-bit dither floor and Serum's deeper stop band differ); MgL18 is 10 dB dark in the stop band (an 18 dB slope on the 24 dB engine filter, known), MgL6 12 dB steeper than
  Serum's 6 dB/oct (the 12 dB engine filter, known). Filter 2 matches Filter 1's law (Serum's Filter 2 reads about 3 dB lower near the knee: 1.5 pot steps, not carried). So the "20-40 dB too little hiss above 1.4 kHz" of
  Lead - Other Side's Filter 2 noise was NOT a cutoff / slope law: Filter 2's cutoff is moved by Env3 through a route with a random aux on a curve (the depth was carried at 0.5 instead of 0.78), and Filter 2 followed Filter 1's
  slower envelope.
- **Filter 2 has its own envelope** when its cutoff route uses another envelope than Filter 1's: a spare modulation envelope (engine `me1`/`me2`) pointed at "Filter 2 Freq" (same units as `flt2_env`, its own ADSR); if both spare
  envelopes are taken Filter 2 follows the filter envelope as before. Lead - Other Side, noise only (FX stripped on both sides, 2-4 kHz band, 220 ms into the note at velocity 100): Serum -47 dB, engine -61 before, -45 after;
  4-8 kHz Serum -63, engine -79 before, -60 after. With the whole chain the engine is still 8 dB short at the peak (the EQs inside the main rack's SplitMS branches are not carried).

## Added 2026-11-08: mono notes over a releasing note

- **A mono note arriving while the previous note is still releasing took the voice with `Voice::start`**, which zeroes the amplitude envelope and the filters and draws new oscillator phases: a step from the release level down to zero, i.e. a click at
  every such join. Lead - Rollin (mono, 20 ms between short notes) had 20 dB more 31-125 Hz than Serum's, and a thump on every note (probably the "struck more times" the user hears). The takeover now goes through `retrigger` (envelope re-attacks from where it is,
  phases and filters carry on; a releasing voice is brought back whatever the Legato switch says). Native test `testMonoTakeover` (largest sample step across the join: 26 x the steady wave's before, 0.2 x after).
  Demo-MIDI octave bands, engine - Serum at 31 / 62 / 125 Hz: Rollin +15 / +27 / +21 -> -8 / +2 / -2 dB, octave-band rms 12.8 -> 4.1 dB; Bass - Self Control 17.3 -> 13.8; the other 20 presets with a demo MIDI unchanged (the mean over the 22: 9.5 -> 8.95).

## Added 2026-11-08: Serum's HyperD measured (the Hyper stage reworked)

- **Structure** (`calibration/hyperd_fit.py render|fit|check`; a pure sine through HyperD at several Detune / Rate / Unison / Wet, Lead - Rollin's oscillator through its own HyperD, white noise through it). With two voices a channel holds lines whose
  offset from the note stays constant for seconds and then flips sign (Detune 100 %, Rate 50 %, note 72: -4.1 Hz for 2 s, +5.3 Hz for 2 s, ...): every voice's delay is moved by a TRIANGLE wave (constant slope = constant pitch offset), not a smooth
  random LFO. The offsets scale with the harmonic number (h3 5.6 Hz where h1 has 1.8): a time-scaled copy, not a frequency shift. Peak-to-peak swing = 20 ms x Detune (linear: lines at +-1.0 / 2.7 / 5.2 Hz at 20 / 50 / 100 %), the triangle frequency grows
  as Rate squared (half period 5 s at Rate 30, 2 s at 50, 0.5 s at 100: 0.25 Hz at 50 %), Unison 1 is a bypass. Left and right have their own voices (phases and speeds differ). The voices are not normalised by their number: white noise through 7
  voices at Wet 100 gains +6.2 dB on the mono sum (each channel's cloud is incoherent: 7 x 1.0 / 2), a sine +8.4 dB per channel; 2 / 3 / 4 / 8 voices +0.8 / +3.5 / +4.9 / +8.4 dB (the engine uses (n / 7)^0.3 x 0.95 per voice). Wet is the usual
  smoothstep law with the dry part. The wet path has no bass: a 12 dB/oct high-pass near 150 Hz (third-octave gain of noise re the dry, Wet 50: dry only below 100 Hz, -2.3 dB; at Wet 100 -9 dB at 31 Hz against +6 in the mid); at Detune / Rate 100
  Serum keeps a little more bass than that (not modelled).
- **Engine Hyper stage** (`fx.h class Hyper`; was: two sines, 1/sqrt(n) normalisation, depth ~ Detune^0.8, rate ~ Rate^1.5, 1 ms, 8 Hz). Pure sine, engine against Serum (`hyperd_fit.py check`): histogram of the line spread rms 6.9 -> 4.5 dB, level 8 dB under
  Serum's -> within 0.8 dB at 2..8 voices, AM std 0.5-7.2 (Serum 2.8-5.7) -> 4.1-5.7. White noise, third-octave gain re dry, all bands above 200 Hz within +-1.5 dB. Lead - Rollin's isolated HyperD: profile rms 6.8 (no stage) / 4.5 (old stage) -> 3.8 dB, FM 4x -> 1.4x,
  AM 0.5x -> 1.6x of Serum's.
- **Lead - Other Side without its two interim hand values** (unison 7/90/64 on osc A and Filter 2 at 74; `Hyper` + the aux-curve / own-envelope Filter 2 replace them): demo MIDI, engine - Serum, octave-band rms 4.7 (converter before) / 4.9 (interim) -> 3.4 dB, level +0.2 dB,
  fast envelope flutter (500-5000 Hz, 4 ms) 0.77 (before) / 1.83 (interim) -> 1.65 against Serum's 1.73. Lead - Rollin (mono fix + Hyper): 11.4 -> 4.9 dB, flutter 0.82 -> 1.09 (Serum 0.92), loudness std of the 100-500 / 500-2k / 2-8 kHz bands 2.8 / 1.8 / 2.1 ->
  4.8 / 3.2 / 2.4 dB (Serum 4.0 / 3.5 / 2.9). Not solved: Rollin's 31 / 62 Hz bands +7 / +7 dB over Serum's (they are -8 / -2 with the stage off; hard clipping of the beating voices makes slow DC wobble) and 4-16 kHz +8 / +6.

## Added 2026-11-08: SplitMS branches, Bode defaults

- **SplitMS** (`calibration/probe_splitms.py`, a wide saw through [SplitMS, EQ, EQ, EQ] with +12 dB peaks at 500 / 1000 / 2000 Hz, mid and side measured separately): the modules after the node are ModuleCount1 for the FIRST branch = MID, then ModuleCount2 for the second = SIDE,
  then the master; **both counts are 0 when unwritten** (not 1: with only ModuleCount2 = 1 the first EQ acts on the side alone and the other two on the whole signal; counts 2 / 1: A and B on the mid, C on the side; counts 1 / 2: A mid, B and C side; counts
  unwritten: all three on the master). The converter had taken an unwritten count as 1, so Lead - Other Side's second EQ (+12 dB at 13.5 kHz, +4.3 dB low) was dropped as 'inside the branch'; it is a master EQ (disabling it in Serum: -4 dB at 125-500 Hz, -11 dB at 8-16 kHz on mid and side alike) and its first
  EQ is a SIDE-only EQ (disabling it: side +6 dB at 125 Hz, -5 / -6 dB at 4-16 kHz, mid unchanged). A split with transparent branches is transparent (level +0.1 dB, bands within 0.3).
- **Bode** (`calibration/probe_bode_default.py`, saw through the node against none): a Bode with no stored params is a no-op (band differences +-0.1 dB, the same as the render against itself), so is one with OutputMix 0.32 (Origins); NOTSOBAD's (OutputMix 26.7, Blur 42) takes
  up to 2.5 dB off 4-16 kHz and 7 dB more at 60-120 Hz, HotLap's (Blur 100, Feedback 14, no mix) -3.5 dB at 8-16 kHz and +11 dB at 60-120 Hz. Not yet used by the converter.
- **Mid / Side EQ stage** (engine `Fx` msEq, params `eqm1..4_*` / `eqs1..4_*`, `fxmap.ms_branches` / `eq_bands(which)`): the EQs inside a SplitMS node's branches are carried as up to four bands each, run in the mid / side domain (M = (L + R) / 2, S = (L - R) / 2; transparent when flat) at the EQ stage.
  A SplitMS whose branches hold nothing but EQs is carried whole (no "not carried: SplitMS"); one with a Comp / Distortion inside a branch (Bass - A Classic's Mid, Lead - Sleepless's Mid) still is not. Native test `testMidSideEq`. Demo MIDI, engine / Serum side-to-mid ratio at 1-8 kHz
  (engine without the branch EQs -> with): Lead - Let It Go -6.3 -> +1.3 dB (Serum +0.4), Bass - Black Out -7.3 -> -12.8 (Serum -14.3), Keys - Nyday -5.2 -> -3.0 (Serum -0.8), Lead - Sleepless -5.8 -> -3.1 (Serum -2.4), Keys - Other Side -10.0 -> -4.2 (Serum -8.4),
  Lead - Other Side +0.9 -> +5.5 (Serum +2.8: overshoots).

## 2026-11-08 Mod wheel (source 1)
- The mod wheel is at 0 in every Serum render and the Move has none, so a unipolar mod-wheel route adds nothing and a bipolar one sits half its depth below the base (folded like velocity). They no longer count as "not carried", and an oscillator whose level is 0 plus a mod-wheel route stays silent, as in Serum (before, it was held on at a fixed level).
- Deep House pack: tier A 30 -> 37 (tier B 26 -> 19, C 14 unchanged); 5 of 70 presets change (Hans Deeper sub, Why U So WT3, Nu Aspect WT2, CamelPhat filter drive/gain, Oskar Med volume); the rest are byte-identical in their state. Factory census: A 87 -> 112 of 626 (C 390 -> 388).
- Band-profile loss on the held chord (calibration/band_profile.py, lower is better), before -> after: Why U So 7.2 -> 6.1, Nu Aspect 9.5 -> 10.6, CamelPhat 8.9 -> 9.0, Hans Deeper 21.2 (unchanged; spectral error 17.8, a separate problem), Oskar Med 15.6 (unchanged). Net sound change is within noise; the change is kept because it is what Serum does at wheel 0.
- `calibration/dropped_routes.py DIR` tallies the audible mod routes still not carried, by source and destination. Left in the Deep House pack: the per-note random sources 21/22 and 39 into table position (folding them at their mean did not help), LFO routes to initial phase, pan, warp, drive.

## 2026-11-08 Factory wavetables must resolve (biggest single gain so far)
- `deephouse2move.py` run without `--serum-tables` wrote `Init` for every wavetable that lives in Serum's own Tables folder (a .SerumPack holds only its own): 66 of the 70 Deep House presets had at least one. It now defaults to `serum_paths.SERUM_TABLES`; the report line "Presets with an unresolved wavetable" must read 0.
- Full-pack `calibration/band_profile.py` loss (lower is better), before -> after: mean 15.3 -> 13.2, median 13.7 -> 11.6, presets under 14: 37 -> 42; 19 improved by more than 2 (Bass - AVAION 43.4 -> 9.4, Vintage Culture 24.2 -> 10.6, Bass - Self Control 23.6 -> 8.4, Scorpios 21.0 -> 8.6), 3 got worse by more than 2 (Aquatiq 11.9 -> 14.9, Pluck - Selected 8.3 -> 11.7, INNERVERSE Reese 17.8 -> 20.2). Worst now: Bass - Let It Go 37.6, Keys - Zerb 24.2, Lead - Other Side 22.9, Pluck - Deep In The Water 22.6, Keys - Hans Deeper 22.3.

## 2026-11-08: velocity and note routes into an effect's Wet or Distortion drive are folded at their nominal value

- An effect's Wet (and a Distortion's Drive) moved by velocity or the note was left dynamic, so a Distortion stored at Wet 0 with a velocity route to its Wet was dropped as "not carried: Distortion" (Bass - Let It Go: Wet 0 plus a velocity route at 100 %, which at velocity 100 is 52 %). `fxmap.foldable_source` now treats velocity (`VELOCITY_NOMINAL` = 100, the engine's own default) and the note (middle C) like a macro at its saved position: `fold_macro_wets` and `fold_macro_knobs` apply the route's amount through its own curve, and the dry-effect test no longer counts such a route as modulation. Filter FX and Utils routes by velocity stay dynamic (not carried), as before.
- Deep House pack, `band_profile.py` before -> after (current `out/` as the baseline): mean 13.17 -> 13.05, median 11.64 unchanged, under 14: 42 unchanged; only Bass - Let It Go moved by more than 1 dB: 37.6 -> 29.3 (spec 24 -> 15.4). Four presets (Keys/Lead - Other Side, Pluck - NOTSOBAD, Pluck - Scorpios) gained +2 on `volume` from the current code, no band change. Factory census unchanged (A 112, B 126, C 388).
- What is left on Bass - Let It Go (29.3 = spec 15.4 + 0.5 x env 17.0 + 0.25 x side/mid 21.9), both found, neither fixed:
  1. **Filter 2 is a Diffuser** (all-pass diffusion, unsupported, left open). Serum keeps sounding after the note ends (the amp release is 15 ms): -22 to -25 dB re peak for about a second; the engine is silent 100 ms after note off. This is the whole envelope term and needs a new filter shape.
  2. **`fx_wide=18` puts side energy on a mono bass that Serum leaves mono.** The width comes from the HyperD fingerprint (side added on white noise, `map_wide`); setting `fx_wide` 0 takes the side/mid error from 21.9 to 0 and the loss to 24.0, while Serum's own side/mid on this note is at the measurement floor (-40 dB). The width is broadband in the engine; a HyperD (and Utils) acts on the high part only, so a low-frequency-heavy preset gets side energy it should not. Check how many presets carry `fx_wide` with a bass-heavy spectrum before changing the width law.

## 2026-11-08: a bipolar envelope route to a voice filter's cutoff sits half its depth below the stored cutoff

- `map_to_tablor` dropped the `bipolar` flag of an envelope route: only the depth went to the engine's unipolar envelope, so the cutoff stayed at the stored value when the envelope had fallen, where Serum has it half the depth lower (a bipolar route is the source minus one half, the same rule measured for macros, velocity and the note). Lead - Let It Go (Env2 -> cutoff +17 % bipolar, sustain 0) had its filter about an octave too open: the engine was 12-21 dB too loud from 2 kHz up. The offset (-0.5 x depth) is now folded into the stored cutoff; the report says "N bipolar envelope route(s) to the cutoff".
- Verified against Serum renders (`band_profile.py`): Lead - Let It Go 20.6 -> 17.2 (spec 12.9 -> 9.4), Lead - Self Control 18.0 -> 14.0 (spec 8.9 -> 5.1; it now passes the gate). The other 68 Deep House presets are unchanged apart from a +-2 step in `volume`, which moves between runs on its own. Only the voice-filter cutoff is folded: the Deep House pack has no other bipolar envelope route, the Factory has about 16 on a cutoff and 10 more on pitch or distortion level (`calibration` tally in this session), pitch and level are not changed here and not verified.
- Run to run the converter's `volume` trim jitters by up to 2 pots on about a dozen presets (Reiny, Abelon, Lynnic, Keys - Other Side, Lead - Ari ...): a measured-level step, harmless but it makes a byte-for-byte diff of two conversions noisy. Not investigated.

## 2026-11-08: an envelope route to an oscillator level is added at the volume knob (gain = knob squared), not to the gain

- Serum adds a route to an oscillator's volume KNOB and the taper comes after. Measured (`calibration/probe_noise_route.py`, noise slot, and a WT oscillator A): with the volume at 0 an envelope route of depth a gives gain a^2 (12 dB per halving of the depth, not 6), a route on volume v gives (sqrt(v) + a)^2 (v 0.25, a 0.25 -> -5.0 dB re full scale, as measured -5.0), static volume is linear in amplitude (6 dB per doubling). The engine added the spare envelopes' level routes to the gain (a), so any route of depth under 1 was too loud by 1/a (9.6 dB for Bass - Reiny, depth 0.33); the LFO routes were already in the knob domain.
- `voice.h`: the spare envelopes' routes to WT1/2/3, Sub and Noise level now go into `lfoLvl` (the knob offset) like the LFOs. 18 Deep House presets carry such a route. Band loss (same capped presets, same converter): Bass - Reiny 13.2 -> 7.5, Keys - Abelon 9.4 -> 7.8, Pluck - all things break 13.5 -> 11.6; Keys - Hans Deeper 17.3 -> 18.5, Lead - Ari 11.8 -> 13.4, Keys - Lynnic 5.9 -> 7.3 got worse (their volume and amount were fitted against the old law; not investigated). Sum over the 18 -7.8, over the 70 about -0.11 on the mean. Needs the ARM `dsp.so` rebuilt to reach the Move.
- Negative route depths on a non-zero volume did not follow the same formula in one probe row (v 0.5, a -0.25: -33.5 dB, formula -26.7); not used by any preset here, not investigated.
- Converter: a route into the noise slot's volume is scaled by the slot's level against White (`noise_db`: the sample stand-in gain, or -2.2 dB for the unwritten type) at half the dB, because the route moves the knob and the gain is its square. Full 70-preset re-convert + cap + profile with the engine and converter changes: mean band loss 12.535 -> 12.396 (the 12.535 is the capped 70 with the stand-in, stitched from two runs), under 14 dB 45 -> 46. Bass - Reiny 13.2 -> 6.9, INNERVERSE Reese 20.4 -> 17.8; Pluck - Scorpios 8.6 -> 10.5, Lead - Ari 11.8 -> 13.1, Lead - Lynnic 6.1 -> 7.2 got worse (run jitter is about +-0.5 per preset).

## 2026-11-08: Color still acts on a noise-slot sample (S2 Noises/HP12 Pink / White)

- Reading the sample name correctly (d0799e6) sent the four presets whose slot holds Serum's own `S2 Noises/HP12 ...` samples down the sample path, which ignored the slot's Color: Bass - INNERVERSE Reese, SWEDISHMF, oskar med k, Pluck - Scorpios (and all things break with the White one). `calibration/probe_noise_hp12.py` (Serum, octave levels): Color acts on the sample as on generated noise (below 0.5 a low-pass, above it a high-pass), and on HP12 Pink it moves the sample's own 1.1 kHz high-pass corner to 1.9 kHz at 0.6, 5.3 kHz at 0.79, 12 kHz at 1.0 (one 12 dB/octave high-pass, fitted to the octave levels, err 2.5-3.3 dB).
- `deephouse2move.py`: a White / Pink / HP12 sample takes its Color pots as a generated noise does; HP12 Pink above Color 0.55 is Pink through a high-pass at those corners (pot 75 / 84 / 103 / 119 at Color 0.55 / 0.6 / 0.79 / 1.0), below it the HP12 Pink type with the Color low-pass.
- Band loss on the five presets, capped, after the previous commit (3cb4fa5): Reese 17.8 -> 9.7, all things break 13.7 -> 11.0, SWEDISHMF 13.3 -> 13.2, oskar med k 18.4 -> 18.9, Scorpios 10.5 -> 11.1 (Scorpios was 8.6 before the sample name was read, as White with the generator's Color high-pass; its top band is still 17 dB low, so the HP12 Pink at high Color has more top end in Serum than a pink through a 12 dB high-pass; level of the sample not carried either). Not fitted further.

## 2026-11-08: a preset with a polyphony of 1 plays as mono

- Lead - Other Side stores `Global0.kParamPolyCount` = 1 (one voice) with Mono off. The converter only read `kParamMonoToggle`, so the engine played the whole chord (C3 G3 C4 Eb4) where Serum, with one voice, lets only the last note sound: Serum's spectrum peaks at 310 Hz (Eb4) and its harmonics with the lower notes 40 dB down (60-200 Hz max -42 dB), the engine had all four notes at equal level (peaks at 129 / 194 / 258 Hz). That was the +16..+43 dB error in the 60-200 Hz bands.
- `deephouse2move.py`: `kParamPolyCount == 1` is carried as mono (voice_mode 1, voices 1; legato still from `kParamLegato`). Of the 70 Deep House presets, 63 leave PolyCount unwritten, 5 store 16, 1 stores 32 and this one stores 1, so it is the only preset changed. Lead - Other Side band loss 22.9 -> 17.7 (spec 18.9 -> 9.4, env 7.2 -> 15.2: Serum sustains past 1 s where the engine's mono voice drops out around 0.7-0.9 s; not investigated).

## 2026-11-08: the per-preset level fits were stale for most presets (25 of 70 more than 3 dB off Serum's level)

- `calibration/level_fits.json` holds each preset's volume for matching Serum's full-chain RMS, with a signature of the converter's other parameters; the converter ignores a fit whose signature no longer matches ("level fit not applied"). After the converter changes of the last weeks most signatures were stale, and `band_profile.py` normalises each spectrum so it never showed it. Measured with `rms(engine) - rms(Serum)` over the chord window, capped bank: 25 of 70 presets more than 3 dB off (Keys - Hans Deeper -13.2, Lead - Consoul Training -12.1, Keys - Zerb -9.9, Bass - A Classic -9.7, Lead - The Selected +10.4, Keys - Billboard +6.0).
- Re-run `calibration/fit_level.py --fresh` (70 presets, no shortfall over 1.5 dB) and re-convert: 6 of 70 remain over 3 dB, all limiter-capped on purpose (A Classic -8.8, oskar med k -5.6, Can't Find You -5.4, Consoul Training -5.2, Black Out -3.8, The Selected +3.6). The band loss does not move on account of level, but the mean over the 70 went 12.396 -> 12.165 with the HP12 Color and PolyCount fixes of the same session, 46 -> 47 under 14 dB.
- Re-run `fit_level.py --fresh` after any converter change that moves a preset's parameters, before `cap_bank.py`.
