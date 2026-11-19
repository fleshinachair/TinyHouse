# Vital-engine controls that have no knob

Imported Vital presets use some engine code that is Vital's own (GPL-3.0-or-later, see `THIRD-PARTY.md`). Where a Vital effect has settings Serum's version does not, the engine has a
parameter for them, the importer sets it and the preset saves it, but there is **no knob** on the Move or the Remote UI. The aim is the same UI for a Serum preset and a Vital one: the
existing knobs (Mix, Decay, Damp, Lo cut ...) drive whichever algorithm the preset uses, and the Vital-only settings are carried silently.

The parameters are listed in `schwung-tablor/tools/gen_params.py` (the banks named "Vital ..."). They are in the host's parameter table and the preset state, so a different front end or a
later UI can surface them without a format change. Nothing below changes a Serum preset: each mode is off unless the preset sets it, and the one new control that works on both
(Reverb Pre-delay) is 0 by default, which leaves the Serum reverb exactly as it was.

## Reverb (Vital mode: `rev_vital` = 1, set by the importer; no knob for it)

Shared knobs, now driving Vital's reverb on a Vital preset: **Mix** (equal-power crossfade), **Decay** (T60), **Damp** (top of the input band, Vital's Pre High), **Lo cut** (bottom of it,
Pre Low), **Pre-delay** (also works on Serum presets). **Width** does nothing on a Vital preset (Vital's reverb has no use for it).

Hidden (saved in the preset, no knob): `rev_vsize` Size, `rev_vlocut` / `rev_vlogain` low shelf frequency and gain, `rev_vhicut` / `rev_vhigain` high shelf frequency and gain,
`rev_vchorus` / `rev_vchorusf` the reverb's own chorus amount and rate, `rev_vital` the mode itself. On a Serum preset these do nothing.

## Chorus, flanger, phaser (modes `chor_vital`, `flan_vital`, `phas_vital`)

The existing Mix / Rate / Depth / Feedback knobs drive them. Hidden: `chor_voices` (1 to 4 voice pairs), `chor_vcut` and `chor_vspread` (the band-pass in the feedback loop),
`flan_vcenter` (flanger centre note), `phas_vcenter` (phaser centre note), `phas_vblend` (4 / 8 / 12 stages), and the three mode switches.

## Other Vital-only parameters

- Filter comb law: `vcomb`, `cblend`, `ctrans`, `vcomb2`, `cblend2`, `ctrans2`.
- Compressor: `vc_bands`, `vc_att`, `vc_rel`.
- Distortion audio-rate LFO: `vdl_on`, `vdl_trans`, `vdl_drive`, `vdl_cut`, `vdl_res`, `vdl_blend`.
- Amp decay curve: `vca_dpow` (0 keeps the analog decay; 1..127 is a curve power of (pot - 64) * 20 / 63, so +-20 at the ends).
- Portamento: `glide_vlaw` (Serum / Vital), `glide_vscale` (Fixed / Per octave), `glide_vforce` (Held / Always). The existing Glide Mode and Glide knob drive it. With Law Vital the Glide knob is 0.001 to 16 s (log), for the whole glide or per octave, as a linear ramp in pitch, and a glide starts from the previous note only while its key is still held (Force Held) or after any note (Force Always); measured in `calibration/vital_porta_probe.py`. Vital's slope control is not carried (a straight glide). On a Serum preset the law is Serum and nothing changes.
- The delay's band-pass is carried on the existing Delay Lo / Hi cut knobs; Vital's delay styles map onto the existing Mode.

## Unison and voice filters (Vital laws)

- Unison: `uni_vital` (Serum / Vital) switches every oscillator to Vital's unison. Per oscillator: `wtN_ustack` Stack Style 0..10, `wtN_upow` Detune Power, `wtN_ublend` Unison Blend (0..100 %). The existing Unison, Detune and Spread knobs drive it: Detune is the stack's half-width, 48 * (pot / 127)^2 semitones; the voices sit at (e^(p|x|) - 1) / (e^p - 1) of it, p the Detune Power; Stack Style adds a semitone offset per voice by its distance from the middle of the stack; Blend sets the level of the middle voice(s) against the others (a measured table); Spread pans the voices as Vital does, an equal-power sqrt(2) cos / sin(pi/4 (1 +- spread)), and a lone voice or the middle voice of an odd stack is scaled by cos(pi spread / 4). Measured in `calibration/vital_stack_probe.py` (`--engine` compares the engine with the plugin). With the law on Serum, nothing changes: `uni_vital` is 0 on a Serum preset.
- Voice filter Mix: `flt1_mix`, `flt2_mix` (pot 127 = fully wet, the default). A linear crossfade, (1 - mix) of the filter's input plus mix of its output (`calibration/vital_fmix_probe.py`).

## LFOs, envelopes and the Filter FX (Vital laws)

All of these default to 0 (or the pot's middle), which leaves a Serum preset exactly as it was; the importer sets them from the preset.

- LFO: `lfo_vlaw` (Serum / Vital), `lfoN_delay` (0 to 4 s, linear over the pot), `lfoN_fade` (0 to 8 s, linear), `lfoN_phase` (0 to 1 of a cycle). The Delay holds the LFO at its start value until the time is up, Fade In blends from that start value to the
  running LFO (Vital skips it while its Smooth is on), Phase is added to the LFO's position. Law Vital also makes the existing `lfoN_smooth` start from 0 and climb to the LFO's value (Serum's starts at the value), and hold at 0 through a Delay.
  The importer maps Vital's Smooth as a time constant of 0.2885 * 2^smooth_time seconds (checked in `calibration/vital_lfo_delay_probe.py`). Vital's own LFO line "Smooth" switch is baked into the 16-point shape.
- Envelopes: `vca_delay` / `vca_hold` (amp), `flt_delay` / `flt_hold` (filter envelope), `me1_delay` / `me1_hold`, `me2_delay` / `me2_hold` (the two spare envelopes). 0 to 4 s over the pot as 4 * (pot / 127)^4: Vital's knobs are a fourth power of 0..sqrt 2.
  A delay holds the output at 0 before the attack; a hold keeps the peak before the decay.
- Filter FX: `fxf_key` is Vital's Key Track (pot 64 = 0, 127 = +1, 1 = -1): the cutoff moves by the amount times (the last played note - 60) semitones (`calibration/vital_fxkey_probe.py`).

Not hidden controls but worth knowing: the importer bakes Vital's four wavetable modifiers (Wave Folder, Frequency Filter, Wave Warp, Phase Shift) into the table frames (`converter/vital_wtmod.py`, checked against the plugin in `calibration/vital_modifier_check.py`),
and a random modulator (sample-and-hold, Perlin) is carried as its average, the middle of its range, folded in like a macro. A random route into an LFO's own frequency, smooth time, delay, fade or phase is folded the same way.

## If these are surfaced later

Surfacing a hidden control for a Serum preset means building the feature into Serum's own algorithm first (a shelf in its loop, chorus on its line reads, variable line lengths for
Size), with a neutral default so an imported Serum preset sounds the same until the knob moves. Otherwise the knob would be dead on half the presets.
