#!/usr/bin/env python3
"""Tablor parameter generator — the single source of truth.

Emits, from one table:
  src/module.json        (< 8 KB — Schwung's loader cap; holds NO chain_params)
  src/dsp/params.h       (param table + chain_params JSON served via get_param)

Every continuous control is a 0..127 pot (the ER-99 doctrine): the DSP maps
each pot to its musical range internally; the screen never shows ms or Hz.
Musical integers (semitones, cents, voices) keep their real ranges.

Run: python3 tools/gen_params.py     (from the repo root)
"""
import json, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION = "0.9.9"

# This fork installs beside stock Tablor rather than over it: a Schwung store
# update looks modules up by id, so a different id is what keeps it from being
# replaced. It also gives the fork its own preset folder.
MODULE_ID = "tinyhouse"
MODULE_NAME = "TinyHouse"

# ---------------------------------------------------------------- enums
FILTER_TYPES = ["LP 12", "LP 24", "HP 12", "HP 24", "BP 12", "BP 24", "Notch 12", "Notch 24"]
# Same rule (this list drives the `waveform` graphic): "Pulse 25"/"Pulse 12"
# were unrecognised and drew sines. "Pulse" and "Pulse Tr" are both in the set.
SUB_WAVES    = ["Sine", "Triangle", "Saw", "Square", "Pulse", "Pulse Tr"]
# Serum's noise slot with no sample written plays pink noise through a 12 dB high-pass near 950 Hz (measured, calibration/probe_noise_default.py); its
# "HP12 Pink Noise" samples the same near 1.2 kHz. The two Pink HP entries are those.
NOISE_TYPES  = ["White", "Pink", "Pink HP", "Pink HP 12", "Brown"]
# Option NAMES are what the host draws a graphic from: Schwung matches them
# against a known set (viz_draw.mjs) and falls back to a SINE for
# anything it does not recognise. "Square+" -- our unipolar square -- was not
# in that set, so it drew a sine, which is a different waveform entirely.
# "Pulse" is recognised, and a 50% unipolar square has exactly the silhouette
# of the square glyph it selects. Renaming is safe for saved sounds: presets
# store the INDEX, and the order here is unchanged.
ONOFF        = ["Off", "On"]
RETRIG       = ["Free", "Retrig"]
VOICE_MODES  = ["Poly", "Mono"]
GLIDE_MODES  = ["Off", "Glissando", "Portamento"]
# Order is the engine's EqType; presets store the INDEX.
EQ_TYPES     = ["Off", "Bell", "Lo Shelf", "Hi Shelf", "Hi Pass", "Lo Pass", "V LoSh", "V Bell", "V HiSh", "V HP", "V LP", "V Notch"]
DELAY_MODES  = ["Stereo", "Ping-Pong"]

# Wavetable selection is a DYNAMIC ENUM: the options are whatever the scanner
# finds, served live from get_param("chain_params"). It has to be an enum —
# the stock UI cannot TURN a filepath/string/canvas param (param_meta.mjs:
# KIND_OPAQUE, isTurnable), it only opens them on click. An enum turns to the
# next wavetable, opens a full-screen list on click, and puts the full name in
# the header. Names come through without their extension.
WT_ROOT   = "/data/UserData/UserLibrary/Wavetables"
WT_FILTER = [".wav", ".wt2048", ".wt1024", ".wt512", ".wt256"]

def wtfile(key, short, full):
    """FILEPATH — the Schwung way: the cell shows the file in brackets, and
    touch-pot + jog-click opens the real browser (folders, live preview).
    A knob cannot TURN it (KIND_OPAQUE) and that is accepted: turning was
    tried as a dynamic enum and the option list had to be rebuilt on the SPI
    callback, which jammed the param bus."""
    return dict(key=key, short=short, full=full, type="file", default="")

# ---------------------------------------------------------------- helpers
def pot(key, short, full, default=64):
    """0..127 pot; DSP maps to the musical range."""
    return dict(key=key, short=short, full=full, type="int", min=0, max=127, default=default)

def num(key, short, full, lo, hi, default=0):
    """A musical integer shown as-is (semitones, cents, voices…)."""
    return dict(key=key, short=short, full=full, type="int", min=lo, max=hi, default=default)

def enum(key, short, full, options, default=0, **kw):
    d = dict(key=key, short=short, full=full, type="enum", options=options, default=default)
    d.update(kw)
    return d

# ---------------------------------------------------------------- the surface
# Trimmed 2026-08-16 per Gus: no fine, no retrig anywhere, no sub/noise pan,
# no filter routing switches. Behaviors hardcode to the old defaults in the DSP.
#
# Trimmed again 2026-09-02: the two LFOs and the four mod slots are gone. Four
# pages of clutter for modulation Schwung already provides -- a slot LFO can
# target any Tablor parameter from the slot settings, which is where a user
# reaches for it first. Velocity and key tracking stay: they are envelope
# controls, not a matrix.
def osc(n):
    return dict(
        table  = wtfile(f"wt{n}_table", f"WT{n}", f"WT{n}"),
        pos    = pot(f"wt{n}_pos",     f"POS{n}",  f"WT{n} Pos", 0),
        level  = pot(f"wt{n}_level",   f"LVL{n}",  f"WT{n} Level", 100 if n == 1 else 0),
        tune   = num(f"wt{n}_tune",    f"TUN{n}",  f"WT{n} Tune", -72, 72, 0),
        fixed  = enum(f"wt{n}_fixed",   f"FIX{n}",  f"WT{n} Pitch", ["Track", "Fixed"], 0),
        uni    = num(f"wt{n}_uni",     f"UNI{n}",  f"WT{n} Unison", 1, 16, 1),
        detune = pot(f"wt{n}_detune",  f"DET{n}",  f"WT{n} Detune", 0),
        spread = pot(f"wt{n}_spread",  f"SPR{n}",  f"WT{n} Spread", 0),
        pan    = pot(f"wt{n}_pan",     f"PAN{n}",  f"WT{n} Pan", 64),
        bend   = pot(f"wt{n}_bend",    f"BND{n}",  f"WT{n} Bend", 64),
        formant= pot(f"wt{n}_formant", f"FRM{n}",  f"WT{n} Formant", 64),
        sync   = pot(f"wt{n}_sync",    f"SYN{n}",  f"WT{n} Sync", 0),
        shape  = enum(f"wt{n}_wshape", f"SHP{n}",  f"WT{n} Warp Shaper", ["Off", "-", "Clip", "Softsign", "Cubic", "Curve"], 0),
        sgain  = pot(f"wt{n}_sgain",   f"SGN{n}",  f"WT{n} Shaper Gain", 64),
        sout   = pot(f"wt{n}_sout",    f"SOT{n}",  f"WT{n} Shaper Out", 64),
        xw     = enum(f"wt{n}_xw",     f"XW{n}",   f"WT{n} Warp", ["Off", "Filter LP", "Filter HP", "FM", "PD", "RM", "AM", "SelfPD"], 0),
        xa     = num(f"wt{n}_xa",      f"XA{n}",   f"WT{n} Warp Amount", 0, 1000, 0),
        xs     = enum(f"wt{n}_xs",     f"XS{n}",   f"WT{n} Warp From", ["Sub", "Osc A", "Osc B", "Osc C", "Noise"], 0),
        xw2    = enum(f"wt{n}_xw2",    f"XV{n}",   f"WT{n} Warp 2", ["Off", "Filter LP", "Filter HP", "FM", "PD", "RM", "AM", "SelfPD"], 0),
        xa2    = num(f"wt{n}_xa2",     f"XB{n}",   f"WT{n} Warp 2 Amount", 0, 1000, 0),
        xs2    = enum(f"wt{n}_xs2",    f"XU{n}",   f"WT{n} Warp 2 From", ["Sub", "Osc A", "Osc B", "Osc C", "Noise"], 0),
    )

# WT3 is deliberately the plain one: a layer, not a third lead. It has no bend
# or formant, because those two cells would have needed a page of their own
# and the third oscillator is there to fill a sound out, not to shape it.
O1, O2, O3 = osc(1), osc(2), osc(3)

# The voice filters' types: the eight biquads, then Serum's comb and flanger models (comb.h: a feedback comb tuned to the cutoff, Reso its feedback), then Serum's three Formant models (formant.h: parallel band-pass resonators from measured tables, Freq and the hidden Var move them).
FILTER_TYPES_V = FILTER_TYPES + ["Comb +", "Comb -", "Flange +", "Flange -", "Phase 12 +", "Phase 24 +", "Phase 36 +", "Phase 48 +", "Formant ONE", "Formant TWO", "Formant TWB"]

FILTER = dict(
    freq = pot("flt_freq", "FREQ", "Filter Freq", 127),
    res  = pot("flt_res",  "RES",  "Filter Res", 0),
    env  = pot("flt_env",  "FENV", "Filter EG Amt", 64),
    type = enum("flt_type", "TYPE", "Filter Type", FILTER_TYPES_V, 1),
    drive = pot("flt_drive", "DRIVE", "Filter Drive", 0),
    key  = pot("flt_key",  "KEYT", "Key Track", 0),
    vel  = pot("flt_vel",  "VELT", "Vel Track", 0),
)
# Serum's Filter FX when an envelope moves it: a filter in the effects block, right after the distortion, whose cutoff follows the
# filter envelope (the loudest voice's). No page on the device, the converter sets it.
FILTER2 = dict(
    on   = enum("fxf_on",   "F2ON", "FX Filter Enable", ONOFF, 0),
    type = enum("fxf_type", "F2TY", "FX Filter Type", FILTER_TYPES, 0),
    freq = pot("fxf_freq",  "F2FQ", "FX Filter Freq", 127),
    res  = pot("fxf_res",   "F2RS", "FX Filter Res", 0),
    env  = pot("fxf_env",   "F2EN", "FX Filter EG Amt", 64),
    wet  = pot("fxf_wet",   "F2WT", "FX Filter Wet", 127),     # 127 = fully wet (the stage was always fully wet before this knob)
)
# Serum's second voice filter: a second filter in every voice with its own type, cutoff, resonance, drive and key tracking; its cutoff follows
# the filter envelope by its own amount. Each source has a balance between the two filters (0 = filter 1 only, the default; 127 = filter 2 only;
# the middle is half and half, a straight crossfade as Serum's). Filter 2 runs only when a source reaches it. Set by the converter.
VFILTER2 = dict(
    type  = enum("flt2_type",  "V2TY", "Filter 2 Type", FILTER_TYPES_V, 1),
    freq  = pot("flt2_freq",   "V2FQ", "Filter 2 Freq", 127),
    res   = pot("flt2_res",    "V2RS", "Filter 2 Res", 0),
    env   = pot("flt2_env",    "V2EN", "Filter 2 EG Amt", 64),
    drive = pot("flt2_drive",  "V2DR", "Filter 2 Drive", 0),
    key   = pot("flt2_key",    "V2KY", "Filter 2 Key Track", 0),
)
# Serum's voice filter Drive is a plain gain, every harmonic by the same amount (+3.3 dB at 10 %, +7.6 at 25, +12.5 at 50; on a loud saw it stops at about
# +13 dB, on a quiet noise it goes on to +24: calibration/dev/probe_vdrive_gain.py and probe_vdrive_saw.py), with no distortion at ordinary levels. The converter
# writes it as this gain after each filter (0..24 dB) and leaves the engine's saturating Drive pot at 0.
VGAIN = dict(
    g1 = pot("flt_gain",  "FGN",  "Filter Makeup", 0),
    g2 = pot("flt2_gain", "V2GN", "Filter 2 Makeup", 0),
)
BALANCES = dict(
    wt1 = pot("wt1_bal",   "B1",   "WT1 Filter Balance", 0),
    wt2 = pot("wt2_bal",   "B2",   "WT2 Filter Balance", 0),
    wt3 = pot("wt3_bal",   "B3",   "WT3 Filter Balance", 0),
    sub = pot("sub_bal",   "BSUB", "Sub Filter Balance", 0),
    noise = pot("noise_bal", "BNZ", "Noise Filter Balance", 0),
    smp = pot("smp_bal",   "BSMP", "Sample Filter Balance", 0),
    smp2 = pot("smp2_bal", "BSM2", "Sample 2 Filter Balance", 0),
    smp3 = pot("smp3_bal", "BSM3", "Sample 3 Filter Balance", 0),
)
# Where each source goes, as Serum's routing buttons (calibration/dev/probe_bus.py and the rack-0 filter probe): through the voice filter (the default); "Direct" past the
# filter and into the effects (Serum's Master); "None", to the FX buses only; "Out", past the filter AND the effects, straight to the output (Serum's Direct).
ROUTE = ["Filter", "Direct", "None", "Out"]
ROUTES = dict(
    wt1 = enum("wt1_post",   "R1",  "WT1 Route", ROUTE, 0),
    wt2 = enum("wt2_post",   "R2",  "WT2 Route", ROUTE, 0),
    wt3 = enum("wt3_post",   "R3",  "WT3 Route", ROUTE, 0),
    sub = enum("sub_post",   "RSUB", "Sub Route", ROUTE, 0),
    noise = enum("noise_post", "RNZ", "Noise Route", ROUTE, 0),
)
# Serum's Chorus, Flanger and Phaser, each with its own switch. Rates are 0..20 Hz on a cubic knob, delays 0..30 ms, the filter and the
# phaser's base frequency 20 Hz..20 kHz; the converter sets them from the preset. A mix of 0 or a switch Off skips the stage.
MODFX = dict(
    con   = enum("chor_on",    "CHON",  "Chorus Enable", ONOFF, 0),
    cmix  = pot("chor_mix",    "CHMX",  "Chorus Mix", 0),
    crate = pot("chor_rate",   "CHRT",  "Chorus Rate", 37),
    cd1   = pot("chor_d1",     "CHD1",  "Chorus Delay 1", 21),
    cd2   = pot("chor_d2",     "CHD2",  "Chorus Delay 2", 22),
    cdep  = pot("chor_depth",  "CHDP",  "Chorus Depth", 64),
    cfb   = pot("chor_fb",     "CHFB",  "Chorus Feedback", 0),
    cfilt = pot("chor_filt",   "CHFL",  "Chorus Filter", 127),
    cfm   = enum("chor_fmode", "CHFM",  "Chorus Filter Mode", ["LPF", "HPF"], 0),
    fon   = enum("flan_on",    "FLON",  "Flanger Enable", ONOFF, 0),
    fmix  = pot("flan_mix",    "FLMX",  "Flanger Mix", 0),
    frate = pot("flan_rate",   "FLRT",  "Flanger Rate", 37),
    fdep  = pot("flan_depth",  "FLDP",  "Flanger Depth", 64),
    ffb   = pot("flan_fb",     "FLFB",  "Flanger Feedback", 0),
    fph   = pot("flan_phase",  "FLPH",  "Flanger Phase", 0),
    pon   = enum("phas_on",    "PHON",  "Phaser Enable", ONOFF, 0),
    pmix  = pot("phas_mix",    "PHMX",  "Phaser Mix", 0),
    prate = pot("phas_rate",   "PHRT",  "Phaser Rate", 37),
    pdep  = pot("phas_depth",  "PHDP",  "Phaser Depth", 64),
    pdep2 = pot("phas_depth2", "PHD2",  "Phaser Depth 2", 0),
    pfrq  = pot("phas_freq",   "PHFQ",  "Phaser Freq", 50),
    pfb   = pot("phas_fb",     "PHFB",  "Phaser Feedback", 0),
    pph   = pot("phas_phase",  "PHPH",  "Phaser Phase", 0),
    ppol  = num("phas_poles",  "PHPL",  "Phaser Poles", 2, 12, 4),
)
# The FX buses: each source's send to Bus 1 and Bus 2 (Serum's FX Bus 1 / 2 levels, written as 127 * (level/100)^3, the gain the send really has). The
# send is taken after the amp envelope and before the filter. "None" as a source's route leaves it on the buses alone. The buses' own effects are the
# "b1." and "b2." keys of the state (the same names as the main FX), not params.
# The two voice filters' outputs are sources too (Serum's routing slots 5 and 6): each goes to the main path (Master), to the buses only (None) or past the effects (Out), and sends
# to the buses after the filter. Filter 1's output can also go into Filter 2 in series ("Filter").
BUSES = {}
FLT_OUT = ["Master", "None", "Out", "Filter"]
FLTOUT = dict(f1 = enum("flt1_out", "F1O", "Filter 1 Output", FLT_OUT, 0), f2 = enum("flt2_out", "F2O", "Filter 2 Output", FLT_OUT, 0))
for _k, _n in (("wt1", "WT1"), ("wt2", "WT2"), ("wt3", "WT3"), ("sub", "Sub"), ("noise", "Noise"), ("smp", "Sample"), ("smp2", "Sample 2"), ("smp3", "Sample 3"), ("flt1", "Filter 1"), ("flt2", "Filter 2")):
    for _b in (1, 2):
        BUSES["%s_%d" % (_k, _b)] = pot("%s_bus%d" % (_k, _b), "%s%d" % (_k[:2].upper() + _k[-1:].upper(), _b), "%s Bus %d" % (_n, _b), 0)
# Serum's Sample and Multisample oscillators: one sample set (a .tbs file made by sample_export.py), played from the keyboard at its
# regions' pitch, with its own level, tuning and routing. Set by the converter; no page on the device.
# Serum's HyperD (Hyper/Dimension) AHEAD of the distortion: Voices delayed copies of the signal, each on a delay line that a triangle wave moves (a pitch-shifted copy at a fixed offset for seconds at a time), mixed with the dry
# signal by Wet. Detune scales how far the delay swings (20 ms peak to peak at 100 %), Rate how fast the triangle runs (0.25 Hz at 50 %, as Rate squared).
# The effect block's own Width stage is the HyperD that follows the
# distortion; this one is for a HyperD in front of it, where the distortion clips the moving cloud together. Wet 0 is off. No page on the device: set by the converter,
# in the main rack and, as "b1." / "b2." keys, in a bus rack.
HYD = dict(
    uni  = num("hyd_uni",  "HYDU", "Hyper Voices", 1, 8, 1),
    det  = pot("hyd_det",  "HYDD", "Hyper Detune", 0),
    rate = pot("hyd_rate", "HYDR", "Hyper Rate",   40),
    wet  = pot("hyd_wet",  "HYDW", "Hyper Wet",    0),
)
# A second delay after the first: Serum racks with two Delay nodes (10 of the 70 Deep House presets). Same laws as the first, the same tempo scale. No page on the device:
# set by the converter, in the main rack and as "b1." / "b2." keys in a bus rack.
DLY2 = dict(
    mix   = pot("dly2_mix",   "D2MX", "Delay 2 Mix",   0),
    time  = pot("dly2_time",  "D2TM", "Delay 2 Time",  94),
    timer = pot("dly2_timer", "D2TR", "Delay 2 Time R", 94),
    fb    = pot("dly2_fb",    "D2FB", "Delay 2 Feedback", 50),
    lo    = pot("dly2_lo",    "D2LO", "Delay 2 Low Cut", 0),
    hi    = pot("dly2_hi",    "D2HI", "Delay 2 High Cut", 127),
    mode  = enum("dly2_mode", "D2MD", "Delay 2 Mode", DELAY_MODES, 0),
)
SAMPLE = dict(
    set   = wtfile("smp_set", "SMPS", "Sample Set"),
    level = pot("smp_level", "SMPL", "Sample Level", 0),
    tune  = num("smp_tune",  "SMPT", "Sample Tune", -48, 48, 0),
    fine  = num("smp_fine",  "SMPF", "Sample Fine", -100, 100, 0),
    post  = enum("smp_post", "SMPR", "Sample Route", ROUTE, 0),
    fixed = enum("smp_fixed", "SMPX", "Sample Pitch", ["Track", "Fixed"], 0),
    uni   = num("smp_uni",    "SMPU", "Sample Unison", 1, 8, 1),
    det   = pot("smp_det",    "SMPD", "Sample Detune", 0),
    spread = pot("smp_spread", "SMPW", "Sample Spread", 0),
)
# A second sample oscillator for presets that stack two (Serum's oscillators A and B both on Sample / Multisample): the same controls and laws as the first.
SAMPLE2 = dict(
    set   = wtfile("smp2_set", "SMQS", "Sample 2 Set"),
    level = pot("smp2_level", "SMQL", "Sample 2 Level", 0),
    tune  = num("smp2_tune",  "SMQT", "Sample 2 Tune", -48, 48, 0),
    fine  = num("smp2_fine",  "SMQF", "Sample 2 Fine", -100, 100, 0),
    post  = enum("smp2_post", "SMQR", "Sample 2 Route", ROUTE, 0),
    fixed = enum("smp2_fixed", "SMQX", "Sample 2 Pitch", ["Track", "Fixed"], 0),
    uni   = num("smp2_uni",    "SMQU", "Sample 2 Unison", 1, 8, 1),
    det   = pot("smp2_det",    "SMQD", "Sample 2 Detune", 0),
    spread = pot("smp2_spread", "SMQW", "Sample 2 Spread", 0),
)
# Serum's Conv reverb as a convolver (conv.h): the impulse response is a sample-set file (TBS, the Size stretch, the Decay envelope and the level baked in by the converter), the mix is Serum's Wet.
CONV = dict(
    set = wtfile("conv_set", "CNVS", "Conv IR Set"),
    mix = pot("conv_mix", "CNVM", "Conv Mix", 0),
)
# Serum's Split / SplitMS: the stages named in Stages (HyperD 1, Distortion 2, Chorus / Flanger / Phaser 4, Width 32) work on one band only. Band 1 is the low band below Freq, 2 the high band
# above it (a 4th-order Linkwitz-Riley crossover, measured: calibration/dev/probe_split_band.py), 3 the Mid and 4 the Side of a SplitMS. Freq is 20 Hz .. 20 kHz (log). Off by default.
CTUNE = dict(
    f1 = num("flt_cfine",  "CTU1", "Filter Comb Tune", 0, 127, 64),
    f2 = num("flt2_cfine", "CTU2", "Filter 2 Comb Tune", 0, 127, 64),
)
SPLITFX = dict(
    band  = enum("split_band",  "SPLB", "Split Band", ["Off", "Low", "High", "Mid", "Side"], 0),
    freq  = pot("split_freq",   "SPLF", "Split Freq", 64),
    stages = num("split_stages", "SPLS", "Split Stages", 0, 255, 0),
)
SAMPLE3 = dict(
    set   = wtfile("smp3_set", "SMRS", "Sample 3 Set"),
    level = pot("smp3_level", "SMRL", "Sample 3 Level", 0),
    tune  = num("smp3_tune",  "SMRT", "Sample 3 Tune", -48, 48, 0),
    fine  = num("smp3_fine",  "SMRF", "Sample 3 Fine", -100, 100, 0),
    post  = enum("smp3_post", "SMRR", "Sample 3 Route", ROUTE, 0),
    fixed = enum("smp3_fixed", "SMRX", "Sample 3 Pitch", ["Track", "Fixed"], 0),
    uni   = num("smp3_uni",    "SMRU", "Sample 3 Unison", 1, 8, 1),
    det   = pot("smp3_det",    "SMRD", "Sample 3 Detune", 0),
    spread = pot("smp3_spread", "SMRW", "Sample 3 Spread", 0),
)

# Serum's Granular oscillator as a mode of a sample slot (granular.h; measured laws in docs/GRANULAR_PROBE_NOTES.md). Density and Grain Length are Serum KNOB positions (pot = 127 x knob),
# because a modulation route adds to the knob; Scan Rate is -200..200 %. Set by the converter; no page on the device.
GRAN_WINDOWS = ["Tukey", "ExpDec", "Triangle", "BlackmanHarris", "Gaussian"]
def _gran(slot, tag, name):
    return dict(
        kind  = enum(f"{slot}_kind",  f"GR{tag}K", f"{name} Kind", ["Sample", "Granular"], 0),
        dens  = pot(f"{slot}_gdens",  f"GR{tag}D", f"{name} Grain Density", 32),
        len   = pot(f"{slot}_glen",   f"GR{tag}L", f"{name} Grain Length", 64),
        scan  = num(f"{slot}_gscan",  f"GR{tag}S", f"{name} Grain Scan", -200, 200, 100),
        start = num(f"{slot}_gstart", f"GR{tag}T", f"{name} Grain Start", 0, 100, 0),
        win   = enum(f"{slot}_gwin",  f"GR{tag}W", f"{name} Grain Window", GRAN_WINDOWS, 0),
        winp  = num(f"{slot}_gwinp",  f"GR{tag}P", f"{name} Grain Window Param", 0, 100, 100),
        skew  = num(f"{slot}_gskew",  f"GR{tag}X", f"{name} Grain Window Skew", -100, 100, 0),
    )
GRAN1, GRAN2, GRAN3 = _gran("smp", "A", "Sample"), _gran("smp2", "B", "Sample 2"), _gran("smp3", "C", "Sample 3")
# The scan range, loop and Random settings of the granular mode (granular.h; docs/GRANULAR_PROBE_NOTES.md P4 / P5). Appended after the first granular bank. The Random values are Serum's own
# (percent; pitch in semitones, capped at 12 by Serum). Order matters: voice.h reads the 12 of a slot as TB_P_<slot>_GLOOP + 0..11.
def _gran2(slot, tag, name):
    return dict(
        loop  = enum(f"{slot}_gloop",   f"GQ{tag}O", f"{name} Grain Loop", ["Off", "Forward", "PingPong"], 0),
        loops = num(f"{slot}_gloops",   f"GQ{tag}S", f"{name} Grain Loop Start", 0, 100, 0),
        loope = num(f"{slot}_gloope",   f"GQ{tag}E", f"{name} Grain Loop End", 0, 100, 100),
        end   = num(f"{slot}_gend",     f"GQ{tag}N", f"{name} Grain End", 0, 100, 100),
        man   = enum(f"{slot}_gman",    f"GQ{tag}M", f"{name} Grain Manual Position", ["Off", "On"], 0),
        pos   = num(f"{slot}_gpos",     f"GQ{tag}P", f"{name} Grain Position", 0, 100, 0),
        rdir  = num(f"{slot}_grdir",    f"GQ{tag}D", f"{name} Random Direction", 0, 100, 0),
        rgain = num(f"{slot}_grgain",   f"GQ{tag}G", f"{name} Random Gain", 0, 100, 0),
        rpit  = num(f"{slot}_grpitch",  f"GQ{tag}H", f"{name} Random Pitch", 0, 12, 0),
        roff  = num(f"{slot}_groff",    f"GQ{tag}F", f"{name} Random Offset", 0, 100, 0),
        rlen  = num(f"{slot}_grlen",    f"GQ{tag}L", f"{name} Random Length", 0, 100, 0),
        rpan  = num(f"{slot}_grpan",    f"GQ{tag}W", f"{name} Random Pan", 0, 100, 0),
    )
GRANB1, GRANB2, GRANB3 = _gran2("smp", "A", "Sample"), _gran2("smp2", "B", "Sample 2"), _gran2("smp3", "C", "Sample 3")
def _gran3(slot, tag, name):
    return dict(
        scx   = enum(f"{slot}_gscx",   f"GR{tag}X", f"{name} Grain Scan Range", ["x1", "x2"], 0),
        scm   = enum(f"{slot}_gscm",   f"GR{tag}M", f"{name} Grain Scan Mode", ["Free", "Tempo lock", "Tempo sync"], 0),
        base  = num(f"{slot}_gbase",   f"GR{tag}B", f"{name} Grain Base Tempo", 20, 400, 120),
        sbr   = num(f"{slot}_gsbr",    f"GR{tag}R", f"{name} Grain Scan Rate Step", 0, 8, 3),
        lenm  = enum(f"{slot}_glenm",  f"GR{tag}L", f"{name} Grain Length Mode", ["Free", "BPM"], 0),
        denm  = enum(f"{slot}_gdenm",  f"GR{tag}D", f"{name} Grain Density Mode", ["Free", "BPM"], 0),
    )
GRANC1, GRANC2, GRANC3 = _gran3("smp", "A", "Sample"), _gran3("smp2", "B", "Sample 2"), _gran3("smp3", "C", "Sample 3")
# The oscillator Pan of a sample slot (either kind), in Serum's percent: hard left / right at -50 / +50 (measured: t = pan / 50, gains L = (1 - t)(1 + 0.172 t), R = (1 + t)(1 - 0.172 t)).
GRAND1, GRAND2, GRAND3 = [dict(pan=num(f"{s}_pan", f"GR{t}Y", f"{n} Pan", -50, 50, 0)) for s, t, n in (("smp", "A", "Sample"), ("smp2", "B", "Sample 2"), ("smp3", "C", "Sample 3"))]
SUB = dict(
    level = pot("sub_level", "SUB",  "Sub Level", 0),
    wave  = enum("sub_wave", "SUBW", "Sub Wave", SUB_WAVES, 0),
    tune  = num("sub_tune",  "SUBT", "Sub Tune", -48, 48, -12),
)
NOISE = dict(
    level = pot("noise_level", "NOIS", "Noise Level", 0),
    type  = enum("noise_type", "NTYP", "Noise Type", NOISE_TYPES, 0),
    # Serum's noise Color is a pair of 12 dB/octave filters: a low-pass below the middle (corner 460 Hz at 0.2, x4.5 per 0.1) and a high-pass above it (corner 460 Hz at 0.7).
    # Corners are 20 Hz .. 20 kHz, logarithmic; High Pass 0 and Low Pass 127 are off. (Pink HP and Pink HP 12 are Pink with the high-pass at 950 and 1190 Hz.)
    hp    = pot("noise_hp", "NHP", "Noise High Pass", 0),
    lp    = pot("noise_lp", "NLP", "Noise Low Pass", 127),
)
VCA = dict(
    # Defaults are the original's: attack 0.1 s, decay 0.1 s, sustain 80%,
    # release 0.1 s (PluginProcessor.cpp, both the amp and filter envelopes).
    # These had been ported as 0 / 100% / 5.7 ms, so a fresh patch had an
    # instant attack and an abrupt release -- the clicky first impression.
    a = pot("vca_a", "ATK", "VCA Attack", 51),
    d = pot("vca_d", "DEC", "VCA Decay", 64),
    s = pot("vca_s", "SUS", "VCA Sustain", 102),
    r = pot("vca_r", "REL", "VCA Release", 64),
    vel = pot("vca_vel", "VVEL", "VCA Velocity", 100),
)
FEG = dict(
    a = pot("flt_a", "FATK", "Flt Attack", 51),
    d = pot("flt_d", "FDEC", "Flt Decay", 64),
    s = pot("flt_s", "FSUS", "Flt Sustain", 102),
    r = pot("flt_r", "FREL", "Flt Release", 64),
)
# Two spare envelopes, each routed at one destination.
#
# Asked for after the mod matrix went: an envelope sweeping WT Position is how
# you get a struck timbre -- full at the attack, gentle as it decays, which is
# what makes a wavetable read as an electric piano. Schwung's slot LFOs cover
# modulation in general, but an LFO cannot do that: it repeats, and the point
# of this shape is that it does not.
#
# Deliberately not a matrix again: two sources, one destination each, one
# amount each. That is four cells on a page rather than sixteen, and it is the
# case people actually reached for.
MOD_DST = ["None",
           "WT1 Pos", "WT2 Pos", "WT1 Level", "WT2 Level", "WT1 Tune", "WT2 Tune",
           "WT1 Bend", "WT2 Bend", "WT1 Formant", "WT2 Formant", "WT1 Pan", "WT2 Pan",
           "Filter Freq", "Filter Res", "Sub Level", "Noise Level", "Amp",
           # Appended, never inserted: presets store the INDEX.
           "WT3 Pos", "WT3 Level", "WT1 Sync", "WT2 Sync", "WT3 Sync", "Filter 2 Freq", "FX Filter Freq", "Dist Wet", "Comp Wet",
           # Granular sample slots (granular.h): the knob of Density, Grain Length and Scan Rate, moved by an LFO / envelope route.
           "Smp1 Density", "Smp1 Length", "Smp1 Scan", "Smp2 Density", "Smp2 Length", "Smp2 Scan", "Smp3 Density", "Smp3 Length", "Smp3 Scan",
           # The Level Out knob of the Utils that ends the main rack / bus 1 / bus 2 (a sidechain-style pump on the hardstyle kicks).
           "Util Out", "Bus1 Out", "Bus2 Out",
           # A sample slot's Level knob (a Sample / Granular oscillator whose volume comes from an LFO or envelope).
           "Smp1 Level", "Smp2 Level", "Smp3 Level",
           # The Filter FX's Wet knob (a fraction of 0..100 %), moved by an LFO / envelope route.
           "FX Filter Wet", "WT1 Pitch", "WT2 Pitch", "WT3 Pitch",
           # An oscillator's FM / PD / RM / AM / filter warp amount (Serum's Warp knob, 0..1), moved by an LFO / envelope route.
           "WT1 Warp", "WT2 Warp", "WT3 Warp",
           # The master EQ bands' Freq and Gain knobs, moved by an LFO / envelope route (fx.h setEqMod).
           "EQ1 Freq", "EQ1 Gain", "EQ2 Freq", "EQ2 Gain", "EQ3 Freq", "EQ3 Gain", "EQ4 Freq", "EQ4 Gain",
           # The Vital distortion's Drive (a fraction of its 60 dB range), and its filter's Cutoff (a fraction of 128 notes), Res and Blend (a fraction of 0..2), moved by an LFO / envelope route (fx.h setVitalDistMod).
           "Dist Drive", "Dist Flt Cut", "Dist Flt Res", "Dist Flt Blend",
           # An oscillator's pitch in semitones at 2 per unit of the route (a vibrato of a few cents: the 48-per-unit WT Pitch destination steps by 0.76 st over a 127-step amount).
           "WT1 Fine", "WT2 Fine", "WT3 Fine",
           # The voice filters' Blend knob (a fraction of its 0..2 range) of the Blend, Phaser and Comb filters, moved by an LFO / envelope route.
           "Filter Blend", "Filter 2 Blend",
           # The whole voice's pitch (Vital voice_transpose, 48 semitones per unit of the route) and fine tune (voice_tune, 2 per unit).
           "Voice Pitch", "Voice Fine", "Filter Comb Trans", "Filter 2 Comb Trans"]

# Four LFOs and eight routes, set by the converter from a Serum preset's LFOs and no page on the device. An LFO is a
# rate (0.05 to 120 Hz, log), a mode (Trig restarts it on every note, Free runs on, Env plays the shape once and holds its end), and a 16-point shape (one cycle,
# 0..127 for 0..1, read with linear interpolation; the default is a sine). A route says which LFO drives which of the
# same destinations the spare envelopes use (MOD_DST), by a bipolar amount (64 = none). `lfo_bip` picks whether an
# LFO swings 0..1 (Uni: it only pushes the destination one way) or -1..+1 around it.
import math as _m
N_LFO, N_ROUTE, N_SHAPE = 4, 8, 16
LFO_SHAPE_DEFAULT = [round(127 * (0.5 + 0.5 * _m.sin(2 * _m.pi * k / N_SHAPE))) for k in range(N_SHAPE)]
LFOS = [dict(
    rate = pot(f"lfo{n}_rate", f"L{n}RT", f"LFO{n} Rate", 60),
    mode = enum(f"lfo{n}_mode", f"L{n}MD", f"LFO{n} Mode", ["Trig", "Free", "Env", "Key", "Key Oct"], 0),
    shape = [pot(f"lfo{n}_s{k}", f"L{n}S{k:X}", f"LFO{n} Shape {k}", LFO_SHAPE_DEFAULT[k]) for k in range(N_SHAPE)],
    # Serum's LFO Smooth: a one-pole low-pass on the LFO's output, 2 ms to 0.7 s (log), 0 = off (calibration/dev/probe_lfo_smooth.py)
    smooth = pot(f"lfo{n}_smooth", f"L{n}SM", f"LFO{n} Smooth", 0),
) for n in range(1, N_LFO + 1)]
# The route sources, by number: 1..4 LFOs, 5..8 random, 9 velocity, 10 aftertouch, 11..14 LFO 5..8 (appended after the rest: a saved preset stores the number).
LR_SRC = ["Off", "LFO1", "LFO2", "LFO3", "LFO4", "Rnd1", "Rnd2", "Rnd3", "Rnd4", "Velocity", "Aftertouch", "LFO5", "LFO6", "LFO7", "LFO8"]
LROUTES = [dict(
    src = enum(f"lr{r}_src", f"R{r}SR", f"LFO Route {r} Source", LR_SRC, 0),
    dst = enum(f"lr{r}_dst", f"R{r}DS", f"LFO Route {r} Dest", MOD_DST, 0),
    amt = pot(f"lr{r}_amt", f"R{r}AM", f"LFO Route {r} Amount", 64),
    # Serum stores the polarity per route (kParamBipolar): Uni adds lfo * amount (the LFO swings 0..1), Bi adds (2 lfo - 1) * amount
    bip = enum(f"lr{r}_bip", f"R{r}BP", f"LFO Route {r} Polarity", ["Uni", "Bi"], 0),
) for r in range(1, N_ROUTE + 1)]

def modenv(n):
    return dict(
        # The device draws the FULL name, abbreviated to fit -- so the full
        # name is what has to carry the envelope number. "Env1 Dest" and
        # "Env2 Dest" both abbreviated to "ENDEST": two cells, one label, and
        # no way to tell which envelope you were pointing. Short, already-
        # abbreviated names survive the squeeze intact.
        a   = pot(f"me{n}_a", f"E{n}ATK", f"EG{n} A", 0),
        d   = pot(f"me{n}_d", f"E{n}DEC", f"EG{n} D", 64),
        s   = pot(f"me{n}_s", f"E{n}SUS", f"EG{n} S", 0),
        r   = pot(f"me{n}_r", f"E{n}REL", f"EG{n} R", 64),
        # Amount is BIPOLAR (64 = off): the useful direction is often downward
        # -- position falling back as the note decays -- so it has to invert.
        dst = enum(f"me{n}_dst", f"E{n}DST", f"EG{n} Dst", MOD_DST, 0),
        amt = pot(f"me{n}_amt", f"E{n}AMT", f"EG{n} Amt", 64),
    )
ME1, ME2 = modenv(1), modenv(2)
ME3, ME4 = modenv(3), modenv(4)       # two more routable envelopes for Vital presets (hidden: set by the Vital converter, no page shows them)

# Presets are Schwung's, not ours. Its browser (shadow_ui_presets.mjs) is
# reached from the slot, works for every module, auditions as you scroll, and
# stores under /data/UserData/schwung/presets/<module-id>/ from the module's
# own `state` blob -- which is to say it needed nothing from Tablor but the
# blob Tablor already answers. Carrying a second preset system beside it was
# a page, four cells and a file format to keep honest, for less.
#
# The DSP still seeds the factory sounds into that store and copies anything
# a user had saved as .tblr (export_presets_to_schwung in tablor_plugin.cpp).

# Space: the effects a preset cannot reach any other way. A Schwung preset sets this module's
# parameters only, so a sound that depends on its reverb and EQ has to carry them. Deep house
# is the sounds and the space between them, so there are few: width, one delay, one reverb and
# a four-band tone EQ, all off at default. The SPACE macro on the main page scales both mixes.
FX = dict(
    space  = pot("fx_space",  "SPACE", "Space",       100),
    wide   = pot("fx_wide",   "WIDE",  "Width",       0),
    wrate  = pot("fx_wrate",  "WRAT",  "Width Rate",  40),
    wdepth = pot("fx_wdepth", "WDEP",  "Width Depth", 64),
    dmix   = pot("dly_mix",   "DMIX",  "Delay Mix",   0),
    dtime  = pot("dly_time",  "DTIM",  "Delay Time",  94),
    dtimer = pot("dly_timer", "DTMR",  "Delay Time R", 94),
    dfb    = pot("dly_fb",    "DFBK",  "Delay Feedback", 50),
    dlo    = pot("dly_lo",    "DLO",   "Delay Low Cut", 0),
    dhi    = pot("dly_hi",    "DHI",   "Delay High Cut", 127),
    dmode  = enum("dly_mode", "DMOD",  "Delay Mode", DELAY_MODES, 0),
    # The delay times are those of a 120 BPM beat (the converter writes Serum's tempo-synced steps that way); with Sync On they follow the host tempo (x 120 / BPM).
    dsync  = enum("dly_sync", "DSYN",  "Delay Sync", ["Off", "On"], 0),
    # Velocity law (Voice > VCA): Original is the original's curve; Serum is flat at VCA Velocity 0 and linear in the velocity at 127, both anchored at velocity 100.
    vlaw   = enum("vca_vlaw", "VLAW",  "VCA Velocity Law", ["Original", "Serum"], 0),
    rmix   = pot("rev_mix",   "RMIX",  "Reverb Mix",  0),
    rdec   = pot("rev_decay", "RDEC",  "Reverb Decay", 70),
    rdamp  = pot("rev_damp",  "RDMP",  "Reverb Damp", 110),
    rlo    = pot("rev_lo",    "RLO",   "Reverb Low Cut", 0),
    rwid   = pot("rev_width", "RWID",  "Reverb Width", 127),
    # Per-stage switches. All On by default, so a preset that does not mention them is unchanged. They
    # sit in their own bank straight after Space so they stay contiguous with the FX pots in the table
    # (Engine::refreshFx watches one run, TB_P_DLY_MIX..TB_P_REV_ON): keep REV_ON last.
    fxon   = enum("fx_on",    "FXON",  "FX Enable",   ONOFF, 1),
    eqon   = enum("eq_on",    "EQON",  "EQ Enable",   ONOFF, 1),
    wideon = enum("wide_on",  "WDON",  "Width Enable", ONOFF, 1),
    dlyon  = enum("dly_on",   "DLON",  "Delay Enable", ONOFF, 1),
    revon  = enum("rev_on",   "RVON",  "Reverb Enable", ONOFF, 1),
    compon = enum("comp_on",  "CPON",  "Comp Enable", ONOFF, 1),
)

def eqband(n):
    return dict(
        type = enum(f"eq{n}_type", f"E{n}TY", f"EQ{n} Type", EQ_TYPES, 0),
        freq = pot(f"eq{n}_freq",  f"E{n}FR", f"EQ{n} Freq", 64),
        gain = pot(f"eq{n}_gain",  f"E{n}GN", f"EQ{n} Gain", 64),
        q    = pot(f"eq{n}_q",     f"E{n}Q",  f"EQ{n} Q",    64),
    )
EQ1, EQ2, EQ3, EQ4 = eqband(1), eqband(2), eqband(3), eqband(4)
# The EQs inside a SplitMS node's two branches (calibration/dev/probe_splitms.py: the first branch is the Mid, the second the Side): four bands each, applied in the
# mid/side domain at the EQ stage. No page on the device: set by the converter, in the main rack and as "b1." / "b2." keys in a bus rack.
def msband(side, n):
    return dict(
        type = enum(f"eq{side}{n}_type", f"Q{side.upper()}{n}T", f"EQ{side.upper()}{n} Type", EQ_TYPES, 0),
        freq = pot(f"eq{side}{n}_freq",  f"Q{side.upper()}{n}F", f"EQ{side.upper()}{n} Freq", 64),
        gain = pot(f"eq{side}{n}_gain",  f"Q{side.upper()}{n}G", f"EQ{side.upper()}{n} Gain", 64),
        q    = pot(f"eq{side}{n}_q",     f"Q{side.upper()}{n}Q", f"EQ{side.upper()}{n} Q",    64),
    )
EQM = [msband("m", n) for n in range(1, 5)]
EQS = [msband("s", n) for n in range(1, 5)]

GLOBAL = dict(
    mode   = enum("voice_mode", "MODE", "Voice Mode", VOICE_MODES, 0),
    voices = num("voices", "VOIC", "Voices", 1, 8, 8),
    glide  = pot("glide", "GLID", "Glide", 0),
    gmode  = enum("glide_mode", "GMOD", "Glide Mode", GLIDE_MODES, 0),
    legato = enum("legato", "LEGA", "Legato", ONOFF, 0),
    pb     = num("pb_range", "BEND", "PB Range", 0, 24, 2),
    ucap   = num("uni_cap", "UCAP", "Unison Cap", 1, 16, 16),   # live performance limit on every oscillator's unison stack (16 = off); not stored in presets
    vol    = pot("volume", "VOL", "Master Gain", 36),         # +18 dB range (Engine kMasterMax): 36 is the old default, 100
)

# Performance controls that sit on top of every preset. All neutral at their default, and the converter
# writes the neutral values into each preset so loading one resets them.
#   decay    moves the filter and amp decay together (64 = as the preset has it; each step is a pot step on both)
#   drive    soft-clip saturation after the master gain (0 = off)
#   lo/hi    a low shelf (150 Hz) and a high shelf (5 kHz), +/-12 dB, 64 = flat
TONE = dict(
    decay = pot("decay",    "DECY", "Decay",      64),
    drive = pot("drive",    "DRIV", "Drive",      0),
    lo    = pot("lo_shelf", "LSHF", "Low Shelf",  64),
    hi    = pot("hi_shelf", "HSHF", "High Shelf", 64),
    # Stereo, after the shelves. Width scales the side channel (64 = as is, 0 = mono, 127 = about 2x); Mono Below
    # keeps everything under that frequency in the middle by high-passing the side channel (0 = off, 1..127 =
    # 40 Hz to 500 Hz). The converter sets both from a Serum Utils effect; neither has a page on the device.
    mwid  = pot("mwidth",   "MWID", "Master Width", 64),
    mono  = pot("monohz",   "MONO", "Mono Below",   0),
    # The base of the gain an LFO / envelope route puts on the Level Out of the Utils that ends a rack (0 = no such route; else the stored knob, pot / 127). Set by the converter; per rack.
    utout = pot("util_out", "UTOU", "Utils Level Base", 0),
    # Distortion, the first stage of the FX block (before width, delay and reverb, so the tail stays clean the way a
    # distortion ahead of a reverb leaves it). Drive 0 is off; Mix is the wet part; Mode picks the curve: Soft is a
    # tanh, Asym adds a bias (even harmonics, the diode/tube/overdrive family), Hard clips. No page on the device:
    # the converter sets it from a Serum Distortion effect and fits it.
    ddrive = pot("dist_drive", "DDRV", "Distortion Drive", 0),
    dmode  = enum("dist_mode", "DMDE", "Distortion Mode", ["Soft", "Asym", "Hard", "Sine", "Hold", "Fold", "Crush"], 0),
    dmix   = pot("dist_mix",   "DMIX", "Distortion Mix",   127),
    # Calibrated distortion = Vital (2): Vital's measured law (calibration/vital_dist_probe.py): Drive is the gain in dB (-30..+30), Mode the curve (Soft tanh, Hard clip, Fold = a triangle fold, Sine = sin(pi/2 x), Hold),
    # Mix a LINEAR crossfade of the dry and the curve's output, and the curve works on the signal against Vital's own full scale (the engine's pre-master level is 6.7 dB under it). The filter (below the FX Order bank) is Vital's.
    # Calibrated distortion (Serum's measured transfer curves; the converter's Serum Distortion translation). On: Drive is the pre-gain G
    # (-16..+54 dB), Mode adds Sine (3), Wet Gain is the curve's output gain K (-36..+12 dB, 64 = -12), Mix is the wet part s with the dry part
    # (1 - s^2), and Level is a gain on the sum (-24..+24 dB, 64 = 0). Off is the earlier law (drive 0 off, 1/sqrt(g) scaling, linear mix).
    dcal   = enum("dist_cal",  "DCAL", "Distortion Calibrated", ["Off", "On", "Vital"], 0),
    dout   = pot("dist_out",   "DOUT", "Distortion Wet Gain", 64),
    dlvl   = pot("dist_level", "DLVL", "Distortion Level",    64),
    # The Distortion effect's own filter when it sits BEFORE the distorting (Serum's PrePost 2): a 12 dB/octave low-pass or high-pass ahead of the
    # curve, on the distorted part only. Freq is 20 Hz..20 kHz (log), Q 0.3..3 (log, 40 = 1). Calibrated distortion only. Off by default.
    dpre   = enum("dist_pre",      "DPRE", "Distortion Pre-Filter", ["Off", "LP", "HP"], 0),
    dprf   = pot("dist_pre_freq",  "DPRF", "Distortion Pre-Filter Freq", 64),
    dprq   = pot("dist_pre_q",     "DPRQ", "Distortion Pre-Filter Q", 40),
    # Compressor, after the distortion (before width, delay and reverb): a stereo-linked peak compressor with a soft knee. Threshold
    # 0..127 is -90..+30 dB at the stage's input (before the master gain), Ratio 1:1 to 100:1 (exponential), Attack 0.1..100 ms and Release 10..3000 ms (exponential), Makeup 0..24 dB,
    # Mix the parallel part. Mix 0 is off. No page on the device but the Comp Enable switch: the converter sets it from a Serum Comp.
    cthr   = pot("comp_thresh", "CTHR", "Comp Threshold", 127),
    crat   = pot("comp_ratio",  "CRAT", "Comp Ratio",     0),
    catt   = pot("comp_attack", "CATT", "Comp Attack",    40),
    crel   = pot("comp_release","CREL", "Comp Release",   60),
    cmk    = pot("comp_makeup", "CMKP", "Comp Makeup",    0),
    cmix   = pot("comp_mix",    "CMIX", "Comp Mix",       0),
    ckn    = pot("comp_knee",   "CKNE", "Comp Knee",      32),      # soft-knee width, 0..48 dB
    # Multiband mode: three bands (crossovers 120 Hz and 2.4 kHz, Linkwitz-Riley), each with its own threshold (band 1 uses Comp Threshold,
    # bands 2 and 3 these), a gain of -24..+24 dB (64 = 0), and upward compression below a threshold 11 dB under the band's own, with
    # slope Below/127 (0 = none, 127 = the signal is pulled up to the floor). Ratio, Attack, Release, Makeup and Mix are shared.
    cmulti = enum("comp_multi", "CMUL", "Comp Multiband", ["Off", "On", "Vital"], 0),
    ct2    = pot("comp_thresh2", "CTH2", "Comp Threshold Mid",  127),
    ct3    = pot("comp_thresh3", "CTH3", "Comp Threshold High", 127),
    cg1    = pot("comp_gain1",   "CGN1", "Comp Gain Low",  64),
    cg2    = pot("comp_gain2",   "CGN2", "Comp Gain Mid",  64),
    cg3    = pot("comp_gain3",   "CGN3", "Comp Gain High", 64),
    cbl    = pot("comp_below",   "CBLW", "Comp Below",      0),
)

# The eight macros of an imported Serum preset. A macro pot moves the parameters its preset routes to it (the state's "mac" key lists the routes: macro,
# parameter, the parameter's travel over the macro's whole range); the pots hold the macro's position, and a preset is saved with them where they sit.
MACROS = [pot(f"mac{i}", f"MC{i}", f"Macro {i}", 0) for i in range(1, 9)]

# ---------------------------------------------------------------- pages
def row(*slots):
    out = list(slots) + [None] * (8 - len(slots))
    assert len(out) == 8, f"row has {len(slots)} slots"
    return out

BANKS = [
    ("Osc", False, [
        row(O1["table"], O2["table"], O1["pos"], O2["pos"],
            O1["level"], O2["level"], O1["tune"], O2["tune"]),
        row(O1["uni"], O1["detune"], O1["spread"], O1["pan"],
            O2["uni"], O2["detune"], O2["spread"], O2["pan"]),
        row(O1["bend"], O1["formant"], O2["bend"], O2["formant"],
            SUB["tune"], O1["sync"], O2["sync"], O3["sync"]),
        row(O3["table"], O3["pos"], O3["level"], O3["tune"],
            O3["uni"], O3["detune"], O3["spread"], O3["pan"]),
    ]),
    # Stock pages render 2 rows of 4; a viz group must sit contiguously in
    # one row. freq/res/TYPE first so the filter-curve roles touch.
    ("Filter", False, [
        row(FILTER["freq"], FILTER["res"], FILTER["type"],
            SUB["level"], SUB["wave"], NOISE["level"], NOISE["type"], FILTER["drive"]),
    ]),
    # Both envelopes on ONE page: an envelope graphic spans one 4-cell line
    # and a page has two lines, so VCA (line 1) + Filter (line 2) draw as two
    # stacked ADSR graphics. Each foursome must own its own line -- split
    # across lines it degrades to lone ramps.
    ("Env", False, [
        row(VCA["a"], VCA["d"], VCA["s"], VCA["r"],
            FEG["a"], FEG["d"], FEG["s"], FEG["r"]),
        # the two spare envelopes: each foursome owns its own 4-cell line so
        # both draw as a curve, the way the amp and filter pair do above
        row(ME1["a"], ME1["d"], ME1["s"], ME1["r"],
            ME2["a"], ME2["d"], ME2["s"], ME2["r"]),
        # Tracking on the top row; the routing on the bottom one, all four
        # cells together, so each destination sits next to its own amount
        # instead of the pair being split across the fold.
        # Top row is the four envelope-DEPTH controls, filter EG amount
        # included -- it is how far the filter envelope goes, which is the
        # same question the amounts below answer for EG1 and EG2, and it was
        # sitting on the filter page next to cutoff where it is not.
        # Bottom row is the routing, all four cells together, so each
        # destination sits beside its own amount instead of the pair being
        # split across the fold.
        row(VCA["vel"], FILTER["key"], FILTER["vel"], FILTER["env"],
            ME1["dst"], ME1["amt"], ME2["dst"], ME2["amt"]),
    ]),
    # Space is what the sound sits in. Delay on the top line, reverb under it, so the
    # four cells of each read as one group; everything else about them is one jog away.
    ("Space", False, [
        row(FX["dmix"], FX["dtime"], FX["dfb"], FX["dhi"],
            FX["rmix"], FX["rdec"], FX["rdamp"], FX["rwid"]),
        row(FX["space"], FX["wide"], FX["wrate"], FX["wdepth"],
            FX["dtimer"], FX["dmode"], FX["dlo"], FX["rlo"]),
        row(EQ1["type"], EQ1["freq"], EQ1["gain"], EQ1["q"],
            EQ2["type"], EQ2["freq"], EQ2["gain"], EQ2["q"]),
        row(EQ3["type"], EQ3["freq"], EQ3["gain"], EQ3["q"],
            EQ4["type"], EQ4["freq"], EQ4["gain"], EQ4["q"]),
    ]),
    # On/off for each stage of the block, and for the whole block. Off fades the wet signal out
    # over about 10 ms; the stage keeps running underneath, so switching it back on does not click.
    ("Switch", False, [
        row(FX["fxon"], FX["eqon"], FX["wideon"], FX["dlyon"], FX["revon"]),
    ]),
    # The three modulated effects: a switch and the mix for each, then the rates and depths. No page of their own on the
    # device beyond this first row (the Mod FX page); the rest are set by the converter and reachable from the web UI.
    ("Mod FX", False, [
        row(MODFX["con"], MODFX["cmix"], MODFX["fon"], MODFX["fmix"], MODFX["pon"], MODFX["pmix"], MODFX["crate"], MODFX["cdep"]),
        row(MODFX["cd1"], MODFX["cd2"], MODFX["cfb"], MODFX["cfilt"], MODFX["cfm"]),
        row(MODFX["frate"], MODFX["fdep"], MODFX["ffb"], MODFX["fph"]),
        row(MODFX["prate"], MODFX["pdep"], MODFX["pdep2"], MODFX["pfrq"], MODFX["pfb"], MODFX["pph"], MODFX["ppol"]),
    ]),
    ("Global", True, [
        row(GLOBAL["mode"], GLOBAL["voices"], GLOBAL["ucap"], GLOBAL["glide"], GLOBAL["gmode"],
            GLOBAL["legato"], GLOBAL["pb"], GLOBAL["vol"]),
    ]),
    # The three pages the device shows (TinyHouse). The banks above stay in the table, so every parameter
    # still exists, is saved in a preset's state and is on the web UI; they just have no page on the Move.
    ("Main", False, [
        row(FILTER["freq"], FILTER["res"], FILTER["env"], TONE["decay"], SUB["level"], FX["wide"], FX["space"]),   # Bank is the 8th knob of this page (added below); Unison Cap lives on the Voice page
    ]),
    ("Tone", False, [
        # Row 0 has no page: Low Shelf lost its knob to the third position (it stays on the web UI), and it is kept here, in its old place in the
        # table, so no parameter's number moves. The page is row 1 (PAGE_MAP).
        row(TONE["drive"], TONE["lo"]),
        row(O1["pos"], O2["pos"], O3["pos"], VCA["a"], VCA["r"], TONE["drive"], TONE["hi"], GLOBAL["vol"]),
    ]),
    ("Effects", False, [
        row(FX["eqon"], FX["wideon"], FX["dlyon"], FX["revon"], FX["dtime"], FX["compon"], FX["rdec"], FX["fxon"]),
    ]),
    # No page on the device: the LFOs and their routes, set by the converter.
    ("LFO", False,
        # Each LFO's 18 parameters (rate, mode, 16 shape points) and each route's 3 are CONSECUTIVE in the table:
        # voice.h strides through them. Rows stay in this order for that reason (see the static_asserts there).
        [r_ for L in LFOS for r_ in (row(L["rate"], L["mode"], *L["shape"][:6]), row(*L["shape"][6:14]), row(*L["shape"][14:], L["smooth"]))] +
        [row(R["src"], R["dst"], R["amt"], R["bip"]) for R in LROUTES]),
    # No page on the device: set by the converter, reachable from the web UI.
    ("Stereo", False, [
        row(TONE["mwid"], TONE["mono"], TONE["ddrive"], TONE["dmode"], TONE["dmix"], TONE["dcal"], TONE["dout"], TONE["dlvl"]),
        row(TONE["cthr"], TONE["crat"], TONE["catt"], TONE["crel"], TONE["cmk"], TONE["cmix"], TONE["ckn"]),
        row(TONE["cmulti"], TONE["ct2"], TONE["ct3"], TONE["cg1"], TONE["cg2"], TONE["cg3"], TONE["cbl"]),
        row(FILTER2["on"], FILTER2["type"], FILTER2["freq"], FILTER2["res"], FILTER2["env"], FILTER2["wet"]),
        row(ROUTES["wt1"], ROUTES["wt2"], ROUTES["wt3"], ROUTES["sub"], ROUTES["noise"]),
        row(SAMPLE["set"], SAMPLE["level"], SAMPLE["tune"], SAMPLE["fine"], SAMPLE["post"], SAMPLE["fixed"], SAMPLE["uni"], SAMPLE["det"]),
        row(VFILTER2["type"], VFILTER2["freq"], VFILTER2["res"], VFILTER2["env"], VFILTER2["drive"], VFILTER2["key"], VGAIN["g1"], VGAIN["g2"]),
        row(BALANCES["wt1"], BALANCES["wt2"], BALANCES["wt3"], BALANCES["sub"], BALANCES["noise"], BALANCES["smp"]),
        row(NOISE["hp"], NOISE["lp"], FX["dsync"], FX["vlaw"], SAMPLE["spread"], SAMPLE2["spread"], SAMPLE3["spread"]),
        row(BUSES["wt1_1"], BUSES["wt1_2"], BUSES["wt2_1"], BUSES["wt2_2"], BUSES["wt3_1"], BUSES["wt3_2"]),
        row(BUSES["sub_1"], BUSES["sub_2"], BUSES["noise_1"], BUSES["noise_2"], BUSES["smp_1"], BUSES["smp_2"]),
        row(BUSES["flt1_1"], BUSES["flt1_2"], BUSES["flt2_1"], BUSES["flt2_2"], FLTOUT["f1"], FLTOUT["f2"]),
        row(HYD["uni"], HYD["det"], HYD["rate"], HYD["wet"]),
        row(DLY2["mix"], DLY2["time"], DLY2["timer"], DLY2["fb"], DLY2["lo"], DLY2["hi"]),
        row(DLY2["mode"]),
        row(TONE["dpre"], TONE["dprf"], TONE["dprq"], TONE["utout"]),
        row(*[v for k in ("type", "freq", "gain", "q") for v in (EQM[0][k], EQM[1][k])]),
        row(*[v for k in ("type", "freq", "gain", "q") for v in (EQM[2][k], EQM[3][k])]),
        row(*[v for k in ("type", "freq", "gain", "q") for v in (EQS[0][k], EQS[1][k])]),
        row(*[v for k in ("type", "freq", "gain", "q") for v in (EQS[2][k], EQS[3][k])]),
        row(O1["shape"], O1["sgain"], O1["sout"], O2["shape"], O2["sgain"], O2["sout"]),
        row(O3["shape"], O3["sgain"], O3["sout"]),
        row(SAMPLE2["set"], SAMPLE2["level"], SAMPLE2["tune"], SAMPLE2["fine"], SAMPLE2["post"], SAMPLE2["fixed"], SAMPLE2["uni"], SAMPLE2["det"]),
        row(BALANCES["smp2"], BUSES["smp2_1"], BUSES["smp2_2"]),
        row(SAMPLE3["set"], SAMPLE3["level"], SAMPLE3["tune"], SAMPLE3["fine"], SAMPLE3["post"], SAMPLE3["fixed"], SAMPLE3["uni"], SAMPLE3["det"]),
        row(BALANCES["smp3"], BUSES["smp3_1"], BUSES["smp3_2"]),
    ]),
    # Last, so no earlier parameter's number moves. The page is on the device (PAGE_MAP).
    ("Macros", False, [row(*MACROS)]),
    # After the Macros, so no earlier parameter's number moves. Serum's filter / FM / PD / RM warps (the amount is Serum's, in thousandths); no page on the device.
    ("Warps", False, [row(O1["xw"], O1["xa"], O1["xs"], O1["xw2"], O1["xa2"], O1["xs2"], O1["fixed"]),
                      row(O2["xw"], O2["xa"], O2["xs"], O2["xw2"], O2["xa2"], O2["xs2"], O2["fixed"]),
                      row(O3["xw"], O3["xa"], O3["xs"], O3["xw2"], O3["xa2"], O3["xs2"], O3["fixed"])]),
    # After the Warps, for the same reason. Set by the converter; no page on the device.
    ("Conv", False, [row(CONV["set"], CONV["mix"])]),
    # After the Conv, for the same reason. Set by the converter; no page on the device.
    ("Split", False, [row(SPLITFX["band"], SPLITFX["freq"], SPLITFX["stages"])]),
    # After the Split, for the same reason. The comb filters' tuning, in cents (64 = 0): a pot step of Freq is a semitone, far too coarse to tune a comb. Set by the converter.
    ("CombTune", False, [row(CTUNE["f1"], CTUNE["f2"])]),
    # After the CombTune, for the same reason. The granular mode of the three sample slots; set by the converter, no page on the device.
    ("Granular", False, [row(*G.values()) for G in (GRAN1, GRAN2, GRAN3)]),
    # After the Granular bank, for the same reason: the scan range, loop and Random settings of the granular mode. Set by the converter; no page on the device.
    ("Granular 2", False, [row(*list(G.values())[i:i + 6]) for G in (GRANB1, GRANB2, GRANB3) for i in (0, 6)]),
    # After Granular 2, for the same reason: the scan range option, the tempo scan, and the BPM modes of grain length and density. Set by the converter; no page on the device.
    ("Granular 3", False, [row(*G.values()) for G in (GRANC1, GRANC2, GRANC3)]),
    # After Granular 3, for the same reason: a sample slot's Pan. Set by the converter; no page on the device.
    ("Granular 4", False, [row(GRAND1["pan"], GRAND2["pan"], GRAND3["pan"])]),
    # The Grain pages of the device (PAGE_MAP): the four main grain knobs of a slot on the first line (under the sample's picture), then its Kind and the loop (mode, start, end).
    # The slot's Level is on its Sample page; the window and its shape are remote-UI only. Every key is in the table already (an earlier bank), so no number moves.
    ("Grain", False, [row(G["dens"], G["len"], G["scan"], G["start"], G["kind"], GB["loop"], GB["loops"], GB["loope"])
                      for G, GB in ((GRAN1, GRANB1), (GRAN2, GRANB2), (GRAN3, GRANB3))]),
    # The Effects and Mod FX pages as the device shows them now: the switches together on the first line (they draw as a strip of boxes) and the knobs for them under.
    # The older Effects and Mod FX rows above stay as they were (they fix the parameters' numbers); these only reorder keys that are in the table already.
    ("Effects Page", False, [row(FX["eqon"], FX["wideon"], FX["dlyon"], FX["revon"], FX["compon"], FX["fxon"], FX["dtime"], FX["rdec"])]),
    ("Mod FX Page", False, [row(MODFX["con"], MODFX["fon"], MODFX["pon"], MODFX["crate"], MODFX["cmix"], MODFX["fmix"], MODFX["pmix"], MODFX["cdep"])]),
    # After the Mod FX Page, for the same reason. The order of the main effects chain (distortion, Filter FX, comp, chorus / flanger / phaser, delay, conv and reverb, EQ) as the rank of
    # a permutation of that list: 0 is the order above, which every Serum preset uses. Set by the Vital converter (Vital lets a preset reorder its effects); no page on the device.
    # Comp Boost Cap: the most dB the multiband compressor's upward part may add to a quiet band (12 = the engine's own; Vital's goes to about 43, so its converter sets more).
    ("FX Order", False, [row(num("fx_order", "FXOD", "FX Order", 0, 5039, 0), num("comp_boost", "CBST", "Comp Boost Cap", 0, 48, 12),
                       # The Vital distortion's own filter (calibration/vital_distfilt_probe.py): Off / Pre / Post the curve, on the wet part only; a two-pole state-variable filter whose Cutoff is a MIDI note
                       # (pot 0..127 = note 8..136), Res maps to a Q of 0.5..14 and Blend morphs low-pass (0) to band-pass (64) to high-pass (127). Used with Distortion Calibrated = Vital.
                       enum("dist_flt", "DFLT", "Distortion Filter", ["Off", "Pre", "Post"], 0), pot("dist_flt_cut", "DFCT", "Distortion Filter Cutoff", 64),
                       pot("dist_flt_res", "DFRS", "Distortion Filter Res", 0), pot("dist_flt_blend", "DFBL", "Distortion Filter Blend", 0))]),
    # After FX Order, for the same reason. Vital's oscillator-to-oscillator ring modulation uses the other oscillator's output with that oscillator's own modulation applied (osc 2 ring-modulated by
    # osc 3, then osc 1 by osc 2). Off (every Serum preset) renders a warp source bare, as Serum does; On lets an oscillator that is a warp source keep its own warps. Set by the Vital converter.
    ("Warp Chain", False, [row(enum("xchain", "XCH", "Warp Source Keeps Warps", ["Off", "On"], 0))]),
    # After Warp Chain, for the same reason. The Vital comb (Comb +, Flange + and Flange - read Vital's comb_filter law instead of Serum's): the model (Serum / Vital Blend / Vital Band),
    # its Blend knob (0..2 over the pot) and its Blend Transpose (-48..+48 semitones over the pot, 64 = 0) for each of the two voice filters. Set by the Vital converter; Serum presets leave them at 0.
    ("Vital Comb", False, [row(enum("vcomb", "VC1", "Filter Comb Law", ["Serum", "Vital Blend", "Vital Band"], 0), num("cblend", "CB1", "Filter Comb Blend", 0, 127, 0), num("ctrans", "CT1", "Filter Comb Transpose", 0, 127, 64),
                                       enum("vcomb2", "VC2", "Filter 2 Comb Law", ["Serum", "Vital Blend", "Vital Band"], 0), num("cblend2", "CB2", "Filter 2 Comb Blend", 0, 127, 0), num("ctrans2", "CT2", "Filter 2 Comb Transpose", 0, 127, 64))]),
    # After Vital Comb, for the same reason. The Vital compressor (Comp Multiband = Vital): per band (low, mid, high) an upper threshold and ratio (downward), a lower threshold and ratio (upward when
    # above 64, expanding below it) and a gain, the shared attack and release (0..1 over the pot) and which bands are split off. Thresholds -100..+12 dB over the pot, gains -30..+30 dB. Set by the Vital converter.
    ("Vital Comp", False, [row(*[num(f"vc_ut{b}", f"VU{b}", f"Vital Comp Upper Threshold {n}", 0, 127, 127) for b, n in ((1, "Low"), (2, "Mid"), (3, "High"))],
                              *[num(f"vc_ur{b}", f"VR{b}", f"Vital Comp Upper Ratio {n}", 0, 127, 0) for b, n in ((1, "Low"), (2, "Mid"), (3, "High"))],
                              enum("vc_bands", "VBD", "Vital Comp Bands", ["Three", "Low + Rest", "Rest + High", "One"], 0), num("vc_att", "VAT", "Vital Comp Attack", 0, 127, 64)),
                         row(*[num(f"vc_lt{b}", f"VL{b}", f"Vital Comp Lower Threshold {n}", 0, 127, 0) for b, n in ((1, "Low"), (2, "Mid"), (3, "High"))],
                              *[num(f"vc_lr{b}", f"VW{b}", f"Vital Comp Lower Ratio {n}", 0, 127, 64) for b, n in ((1, "Low"), (2, "Mid"), (3, "High"))],
                              num("vc_rel", "VRL", "Vital Comp Release", 0, 127, 64)),
                         row(*[num(f"vc_g{b}", f"VG{b}", f"Vital Comp Gain {n}", 0, 127, 64) for b, n in ((1, "Low"), (2, "Mid"), (3, "High"))])]),
    # After Vital Comp, for the same reason. An audio-rate LFO on the Vital distortion: Vital's keyboard-following LFO runs at the note's own pitch, which the block-rate LFOs cannot do, so this one
    # is stepped every few samples inside the distortion stage. Its frequency is the newest note's plus Transpose (-48..+48 semitones, 64 = 0); Drive / Cutoff / Res / Blend are its route amounts
    # (a fraction of each control's range, 64 = 0); the 16 shape points are one cycle, 0..1. Set by the Vital converter.
    ("Vital Dist LFO", False, [row(enum("vdl_on", "VDL", "Dist Audio LFO", ["Off", "On"], 0), num("vdl_trans", "VDT", "Dist Audio LFO Transpose", 0, 127, 64),
                                   num("vdl_drive", "VDD", "Dist Audio LFO Drive", 0, 127, 64), num("vdl_cut", "VDC", "Dist Audio LFO Cutoff", 0, 127, 64), num("vdl_res", "VDR", "Dist Audio LFO Res", 0, 127, 64), num("vdl_blend", "VDN", "Dist Audio LFO Blend", 0, 127, 64)),
                              row(*[num(f"vdl_s{i}", f"VS{i}", f"Dist Audio LFO Shape {i + 1}", 0, 127, 0) for i in range(8)]),
                              row(*[num(f"vdl_s{i}", f"VS{i}", f"Dist Audio LFO Shape {i + 1}", 0, 127, 0) for i in range(8, 16)])]),
    # After Vital Dist LFO, for the same reason. Vital's amplitude-envelope decay curve: 0 keeps the analog decay, 1..127 is a curve power of (pot - 64) * 10 / 63 (a positive power holds the level and
    # then plunges, as Vital's Kick Drum 1 does; the analog decay only falls fast first). Set by the Vital converter.
    # Vital's envelopes 3 and 4 as routable envelopes too (the converter ranks a preset's envelope routes onto four instead of two): the same knobs as the first two, plus the Vital extras.
    ("Vital Mod Env", False, [row(ME3["a"], ME3["d"], ME3["s"], ME3["r"], ME3["dst"], ME3["amt"]),
                              row(ME4["a"], ME4["d"], ME4["s"], ME4["r"], ME4["dst"], ME4["amt"]),
                              row(num("me3_apow", "M3P", "Vital Mod Env 3 Attack Curve", 0, 127, 0), num("me4_apow", "M4P", "Vital Mod Env 4 Attack Curve", 0, 127, 0),
                                  num("me3_delay", "VM5", "Env 3 Delay", 0, 127, 0), num("me3_hold", "VM6", "Env 3 Hold", 0, 127, 0),
                                  num("me4_delay", "VM7", "Env 4 Delay", 0, 127, 0), num("me4_hold", "VM8", "Env 4 Hold", 0, 127, 0))]),
    ("Vital Env", False, [row(num("vca_dpow", "VDP", "Vital Amp Decay Curve", 0, 127, 0)),
                         row(num("vca_apow", "VAP", "Vital Amp Attack Curve", 0, 127, 0), num("flt_apow", "FAP", "Vital Filter Attack Curve", 0, 127, 0),
                             num("me1_apow", "M1P", "Vital Mod Env 1 Attack Curve", 0, 127, 0), num("me2_apow", "M2P", "Vital Mod Env 2 Attack Curve", 0, 127, 0))]),
    # Vital's four random modulators (random_lfo.cpp), routed through the same eight LFO routes as sources Rnd1..Rnd4: a style (Perlin, Sample and Hold, Sine, Lorenz), a rate (the LFO rate scale,
    # 0.05 to 120 Hz) and a Tempo switch (the rate is the one at 120 BPM and the host tempo scales it). A new random value is drawn for every voice at note on. Set by the Vital converter.
    ("Vital Random", False, [row(enum(f"rn{n}_style", f"RN{n}S", f"Random {n} Style", ["Perlin", "Sample&Hold", "Sine", "Lorenz"], 0),
                                 pot(f"rn{n}_rate", f"RN{n}R", f"Random {n} Rate", 60),
                                 enum(f"rn{n}_tempo", f"RN{n}T", f"Random {n} Tempo", ["Off", "On"], 0)) for n in range(1, 5)]),
    # Attack curves (0 keeps the analog attack; 1..127 draws Vital's attack, power (pot - 64) * 20 / 63, 64 a straight line; the amplitude envelope is squared into the gain as Vital does).
    # After Vital Env, for the same reason. Vital's chorus (calibration/vital_chorus_probe.py): Off keeps Serum's. On runs Vital's module: 1-4 voice pairs of two delay lines each (Delay 1 and Delay 2 are log2 seconds over the
    # pot's 0..127 = -10..-5.64), Depth adds up to 30 ms on top of the sweep, Feedback is bipolar (pot 0..127 = -0.95..+0.95) and a band-pass (Cutoff = a MIDI note, pot 0..127 = 8..136, Spread 0..1) sits in the loop. Mix is an equal-power crossfade.
    ("Vital Chorus", False, [row(enum("chor_vital", "VCH", "Vital Chorus", ["Off", "On"], 0), num("chor_voices", "VCV", "Vital Chorus Voices", 1, 4, 1),
                                 num("chor_vcut", "VCC", "Vital Chorus Cutoff", 0, 127, 0), num("chor_vspread", "VCS", "Vital Chorus Spread", 0, 127, 127))]),
    # After Vital Chorus, for the same reason. Vital's flanger and phaser (Off keeps Serum's). Flanger On: a stereo delay of 1 / the Center note's frequency (about 0.5 to 10.5 ms, Depth scaling it) swept by a triangle, bipolar Feedback
    # (hard-limited), an equal-power Mix, the Phase knob the right side's offset. Phaser On: 4 / 8 / 12 first-order all-pass stages (Blend 0 / 1 / 2) around a cutoff of Center +- Depth (0..48 semitones), Feedback as resonance, a linear Mix.
    ("Vital Mod FX", False, [row(enum("flan_vital", "VFL", "Vital Flanger", ["Off", "On"], 0), num("flan_vcenter", "VFC", "Vital Flanger Center", 0, 127, 64),
                                 enum("phas_vital", "VPH", "Vital Phaser", ["Off", "On"], 0), num("phas_vcenter", "VPC", "Vital Phaser Center", 0, 127, 64), num("phas_vblend", "VPB", "Vital Phaser Blend", 0, 127, 64))]),
    # After Vital Mod FX, for the same reason. Reverb Type Vital runs Vital's reverb (a 16-line feedback network) behind the same Mix, Decay (T60), Damp (the top of its input band) and Lo cut as Serum's; Mix is then an
    # equal-power crossfade. Size, the two shelves (MIDI-note frequency, pot 0..127 = 0..130, and gain, pot 0..127 = 0..-24 dB) and the Chorus act only with Type Vital; Pre-delay (0..0.3 s) works with both.
    ("Vital Reverb", False, [row(enum("rev_vital", "VRV", "Reverb Type", ["Serum", "Vital"], 0), num("rev_vsize", "VRS", "Reverb Size", 0, 127, 64), num("rev_predelay", "VRY", "Reverb Pre-delay", 0, 127, 0),
                                 num("rev_vlocut", "VRC", "Reverb Low Shelf Freq", 0, 127, 0), num("rev_vlogain", "VRG", "Reverb Low Shelf Gain", 0, 127, 0),
                                 num("rev_vhicut", "VRK", "Reverb High Shelf Freq", 0, 127, 88), num("rev_vhigain", "VRT", "Reverb High Shelf Gain", 0, 127, 5),
                                 num("rev_vchorus", "VRO", "Reverb Chorus", 0, 127, 25)),
                             row(num("rev_vchorusf", "VRF", "Reverb Chorus Rate", 0, 127, 58))]),
    # After Vital Reverb, for the same reason. Vital's portamento (calibration/vital_porta_probe.py): Law Vital makes the Glide knob (with Glide Mode Portamento) a time of 0.001 to 16 s (log over the pot) for the whole glide
    # (Scale Fixed) or for each octave (Scale Per octave), a linear ramp in pitch, and a glide starts from the previous note only while its key is still held (Force Held) or after any note (Force Always).
    # Law Serum keeps Serum's glide as it was.
    ("Vital Glide", False, [row(enum("glide_vlaw", "VGL", "Glide Law", ["Serum", "Vital"], 0), enum("glide_vscale", "VGS", "Glide Scale", ["Fixed", "Per octave"], 0),
                                enum("glide_vforce", "VGF", "Glide Force", ["Held", "Always"], 0))]),
    # After Vital Glide, for the same reason. Vital's unison (calibration/vital_stack_probe.py): Law Vital reads each oscillator's Detune knob as the half-width of the stack in semitones (48 * (pot / 127)^2, the importer writes it
    # with decimals) and spreads the voices at half-width * (e^(p|x|) - 1) / (e^p - 1) for x evenly over -1..1, p the oscillator's Detune Power (the inner voices sit closer in than Serum's even spacing); Stack Style adds a semitone
    # offset to each voice by its distance group from the middle (0 Normal, 1 / 2 Center Drop 12 / 24, 3 Octave Up, 4 Octaves, 5 Power Chord, 6 Power Chord Up, 7 Major, 8 Minor, 9 Harmonic Series, 10 Odd Harmonics). Law Serum keeps
    # the unison as it was. Unison Blend (0..100 %) sets the level of the middle voice(s) against the others (a measured table, vitalBlendGains).
    ("Vital Unison", False, [row(enum("uni_vital", "VUV", "Unison Law", ["Serum", "Vital"], 0), num("wt1_ustack", "VU1", "Osc A Stack Style", 0, 10, 0), num("wt2_ustack", "VU2", "Osc B Stack Style", 0, 10, 0),
                                 num("wt3_ustack", "VU3", "Osc C Stack Style", 0, 10, 0)),
                                row(num("wt1_upow", "VP1", "Osc A Detune Power", -8, 8, 2), num("wt2_upow", "VP2", "Osc B Detune Power", -8, 8, 2), num("wt3_upow", "VP3", "Osc C Detune Power", -8, 8, 2)),
                                row(num("wt1_ublend", "VB1", "Osc A Unison Blend", 0, 100, 80), num("wt2_ublend", "VB2", "Osc B Unison Blend", 0, 100, 80), num("wt3_ublend", "VB3", "Osc C Unison Blend", 0, 100, 80))]),
    # After Vital Unison, for the same reason. Vital's voice filter Mix (calibration/vital_fmix_probe.py): a linear crossfade, (1 - mix) of the filter's input plus mix of its output (pot 127 = fully wet, the engine's own behaviour).
    ("Vital Filter", False, [row(num("flt1_mix", "VF1", "Filter 1 Mix", 0, 127, 127), num("flt2_mix", "VF2", "Filter 2 Mix", 0, 127, 127))]),
    # After Vital Filter, for the same reason. Vital's LFO Delay, Fade In and Phase (modulators/synth_lfo.cpp, checked with calibration/vital_lfo_delay_probe.py): the LFO holds its start value for Delay (0 to 4 s, linear over
    # the pot), then runs; Fade In (0 to 8 s, linear) blends from that start value to the running LFO over that time; Phase (0..1 of a cycle) is added to the LFO's position. All default to 0, which leaves every LFO as it was. LFO Law Vital also makes the LFO's Smooth start from 0 and climb to the LFO's value (Serum's starts at the value), and hold at 0 through a Delay.
    ("Vital LFO", False, [row(enum("lfo_vlaw", "VLL", "LFO Law", ["Serum", "Vital"], 0)),
                          row(*[num(f"lfo{n}_delay", f"VD{n}", f"LFO{n} Delay", 0, 127, 0) for n in (1, 2, 3, 4)]),
                          row(*[num(f"lfo{n}_fade", f"VE{n}", f"LFO{n} Fade In", 0, 127, 0) for n in (1, 2, 3, 4)]),
                          row(*[num(f"lfo{n}_phase", f"VH{n}", f"LFO{n} Phase", 0, 127, 0) for n in (1, 2, 3, 4)])]),
    # After Vital LFO, for the same reason. Vital's envelope Delay (silent for that long before the attack) and Hold (the peak held before the decay): 0 to 4 s over the pot as 4 * (pot / 127)^4, the same law as Vital's own
    # knobs (a quartic of 0..sqrt(2)). 0 leaves the envelope as it was. Amp envelope, filter envelope and the two spare envelopes.
    ("Vital Envelope", False, [row(num("vca_delay", "VAD", "Amp Delay", 0, 127, 0), num("vca_hold", "VAH", "Amp Hold", 0, 127, 0), num("flt_delay", "VFD", "Filter Env Delay", 0, 127, 0),
                                   num("flt_hold", "VFH", "Filter Env Hold", 0, 127, 0)),
                               row(num("me1_delay", "VM1", "Env 1 Delay", 0, 127, 0), num("me1_hold", "VM2", "Env 1 Hold", 0, 127, 0), num("me2_delay", "VM3", "Env 2 Delay", 0, 127, 0),
                                   num("me2_hold", "VM4", "Env 2 Hold", 0, 127, 0))]),
    # After Vital Envelope, for the same reason. Vital's Filter FX Key Track (calibration/vital_fxkey_probe.py): the cutoff moves by Key Track x (the last played note - 60) semitones; pot 64 is 0, 127 is +1, 1 is -1.
    ("Vital Filter FX", False, [row(num("fxf_key", "VFK", "FX Filter Key Track", 0, 127, 64))]),
    # After Vital Filter FX, for the same reason. Vital's filter Blend knob on its first five models (Analog, Dirty, Ladder, Digital, Diode): a linear crossfade of the low-pass (0), band-pass (1) and high-pass (2)
    # outputs of the same two- or four-pole filter (calibration/vital_vfilter_probe.py --blend). Type Blend plays the Type's slope (12 / 24 dB) with the blend, Off plays the Type as it is. Blend is 0..2 over the pot. Passband Drop is how many dB the low-pass / high-pass end loses at full resonance (0..20 over the pot, a curve of res^0.9): Vital's Analog filter drops 10, its Digital none.
    ("Vital Filter Blend", False, [row(enum("flt_vb", "VB1", "Filter Blend Mode", ["Off", "On", "Phaser", "Formant"], 0), num("flt_blend", "VBL", "Filter Blend", 0, 127, 0),
                                       enum("flt2_vb", "VB2", "Filter 2 Blend Mode", ["Off", "On", "Phaser", "Formant"], 0), num("flt2_blend", "VBM", "Filter 2 Blend", 0, 127, 0),
                                       num("flt_vbg", "VD1", "Filter Passband Drop", 0, 127, 0), num("flt2_vbg", "VD2", "Filter 2 Passband Drop", 0, 127, 0))]),
    # After Vital Filter Blend, for the same reason. LFO Tempo: a synced LFO of the source preset (Serum's BeatSync, Vital's Tempo / Dotted / Triplets) plays its Rate pot as the rate at 120 BPM and the host
    # tempo scales it (rate x BPM / 120); Free leaves the rate a fixed Hz, as every LFO was before. No host tempo (0) counts as 120.
    ("LFO Tempo", False, [row(*[enum(f"lfo{n}_tempo", f"LT{n}", f"LFO{n} Tempo", ["Free", "Host"], 0) for n in (1, 2, 3, 4)])]),
    # After LFO Tempo, for the same reason. Filter Drive Law: Serum saturates before and after the filter (tanh in, tanh out); Vital saturates the filter's input with a gain of drive / (1 + 2 resonance) and takes the output down by
    # 1 / sqrt(drive) (filters/sallen_key_filter.cpp setupFilter, calibration/vital_vfilter_probe.py --drive). Serum (0) leaves every preset as it was.
    ("Filter Drive Law", False, [row(enum("flt_vdl", "VDL", "Filter Drive Law", ["Serum", "Vital"], 0), num("fxf_drive", "VXD", "FX Filter Drive", 0, 127, 0))]),
    # After Filter Drive Law, for the same reason. LFO Hi-Res: a second shape of 64 points per LFO (0..127 over one cycle, linear between points), read instead of the 16 points when LFO Hi-Res is on. Vital's lines have steps and
    # narrow pulses (a gate is a few percent of a cycle) that 16 points smear. Off (0) leaves every preset as it was. Each LFO's 65 parameters are consecutive: the switch, then the 64 points.
    ("LFO Hi-Res", False, [row(*c) for n in (1, 2, 3, 4) for c in (lambda L: [L[i:i + 8] for i in range(0, len(L), 8)])(
        [enum(f"lfo{n}_hr", f"H{n}R", f"LFO{n} Hi-Res", ["Off", "On"], 0)] + [num(f"lfo{n}_h{k}", f"H{n}{k:02X}", f"LFO{n} Hi-Res {k}", 0, 127, 64) for k in range(64)])]),
    # After LFO Hi-Res, for the same reason. Osc Phase (modulators / synth_oscillator.cpp: a voice starts at random x Random Phase of a cycle, and the Phase knob is added as a constant offset): Random Phase 127 (the default) is the engine's
    # free-running start, 0 starts every voice at the Phase offset (0..127 over one cycle). Two oscillators at the same pitch then keep a fixed phase to each other, so their harmonics add or cancel the same way on every note.
    ("Osc Phase", False, [row(num("wt1_rph", "RP1", "Osc A Random Phase", 0, 127, 127), num("wt2_rph", "RP2", "Osc B Random Phase", 0, 127, 127), num("wt3_rph", "RP3", "Osc C Random Phase", 0, 127, 127)),
                          row(num("wt1_phs", "PS1", "Osc A Phase", 0, 127, 0), num("wt2_phs", "PS2", "Osc B Phase", 0, 127, 0), num("wt3_phs", "PS3", "Osc C Phase", 0, 127, 0))]),
    # After Osc Phase, for the same reason. Vital's Formant voice filter (Filter Blend Mode = Formant, derived from filters/formant_filter.cpp, formant.h): four band-pass resonators placed between the vowels of the style by X / Y
    # (0..127 over 0..1); Spread (64 = 0, -1..+1) pulls them together or apart around note 80, Transpose (64 = 0, +-12 semitones) moves them, Resonance is the knob (0..1 over the pot). Style: a-i-u-o or a-o-i-e corners. Set by the Vital converter.
    ("Vital Formant", False, [row(num("flt_fx", "FMX1", "Formant X", 0, 127, 64), num("flt_fy", "FMY1", "Formant Y", 0, 127, 64), num("flt_fsp", "FMS1", "Formant Spread", 0, 127, 64),
                                  num("flt_ftr", "FMT1", "Formant Transpose", 0, 127, 64), num("flt_fres", "FMR1", "Formant Resonance", 0, 127, 108), enum("flt_fst", "FMK1", "Formant Style", ["A I U O", "A O I E"], 0)),
                             row(num("flt2_fx", "FMX2", "Formant 2 X", 0, 127, 64), num("flt2_fy", "FMY2", "Formant 2 Y", 0, 127, 64), num("flt2_fsp", "FMS2", "Formant 2 Spread", 0, 127, 64),
                                  num("flt2_ftr", "FMT2", "Formant 2 Transpose", 0, 127, 64), num("flt2_fres", "FMR2", "Formant 2 Resonance", 0, 127, 108), enum("flt2_fst", "FMK2", "Formant 2 Style", ["A I U O", "A O I E"], 0))]),
    # After Vital Formant, for the same reason. Serum's Formant filter models (Filter Type = Formant ONE / TWO / TWB) take the Freq knob and this Var knob (0..127 over Serum's 0..100 %): the resonators sit where the measured
    # tables put them for that Freq and Var (a Freq / Key Track / envelope move slides them along the same axis). Set by the Serum converter.
    ("Serum Formant", False, [row(num("flt_var", "FV1", "Filter Var", 0, 127, 0), num("flt2_var", "FV2", "Filter 2 Var", 0, 127, 0))]),
    # After Serum Formant, for the same reason. Vital has eight LFOs (Serum four): LFO 5 to 8 carry all the parameters of LFO 1 to 4 (rate, mode, 16 shape points, smooth, Hi-Res switch and 64 points, Delay, Fade In, Phase, Tempo),
    # CONSECUTIVE per LFO in this order (voice.h reads them through a table, with static_asserts), and are route sources 11 to 14. Routes 9 to 16 are routes 1 to 8 again (source, destination, amount, polarity). Set by the Vital converter.
    ("Vital LFO 5-8", False, [row(*c) for n in (5, 6, 7, 8) for c in (lambda L: [L[i:i + 8] for i in range(0, len(L), 8)])(
        [pot(f"lfo{n}_rate", f"L{n}RT", f"LFO{n} Rate", 60), enum(f"lfo{n}_mode", f"L{n}MD", f"LFO{n} Mode", ["Trig", "Free", "Env", "Key", "Key Oct"], 0)] +
        [pot(f"lfo{n}_s{k}", f"L{n}S{k:X}", f"LFO{n} Shape {k}", LFO_SHAPE_DEFAULT[k]) for k in range(N_SHAPE)] + [pot(f"lfo{n}_smooth", f"L{n}SM", f"LFO{n} Smooth", 0)] +
        [enum(f"lfo{n}_hr", f"H{n}R", f"LFO{n} Hi-Res", ["Off", "On"], 0)] + [num(f"lfo{n}_h{k}", f"H{n}{k:02X}", f"LFO{n} Hi-Res {k}", 0, 127, 64) for k in range(64)] +
        [num(f"lfo{n}_delay", f"VD{n}", f"LFO{n} Delay", 0, 127, 0), num(f"lfo{n}_fade", f"VE{n}", f"LFO{n} Fade In", 0, 127, 0), num(f"lfo{n}_phase", f"VH{n}", f"LFO{n} Phase", 0, 127, 0),
         enum(f"lfo{n}_tempo", f"LT{n}", f"LFO{n} Tempo", ["Free", "Host"], 0)])]),
    # After Vital LFO Routes, for the same reason. Vital's own decay curve on the filter envelope and the four spare envelopes (the amplitude envelope has Vital Amp Decay Curve): 0 keeps the analog fall, 1..127 draws the curve of power (pot - 64) * 20 / 63 over the stage's time, not squared. Set by the Vital converter.
    ("Vital Env Curves", False, [row(num("flt_dpow", "FDP", "Vital Filter Decay Curve", 0, 127, 0), *[num(f"me{n}_dpow", f"M{n}D", f"Vital Mod Env {n} Decay Curve", 0, 127, 0) for n in (1, 2, 3, 4)])]),
    ("Vital LFO Routes 9-16", False, [row(enum(f"lr{r}_src", f"R{r}SR", f"LFO Route {r} Source", LR_SRC, 0), enum(f"lr{r}_dst", f"R{r}DS", f"LFO Route {r} Dest", MOD_DST, 0),
                                          pot(f"lr{r}_amt", f"R{r}AM", f"LFO Route {r} Amount", 64), enum(f"lr{r}_bip", f"R{r}BP", f"LFO Route {r} Polarity", ["Uni", "Bi"], 0)) for r in range(9, 17)]),
    # Last, so the earlier indices stay. The EQ's frequency knob steps by about a semitone; a Vital EQ band is tuned finer than that (a notch is only deep on its exact frequency): this is the remainder, +-0.5 semitone, 64 centred.
    ("Vital EQ Fine", False, [row(*[num(f"eq{n}_ffine", f"E{n}FF", f"EQ{n} Fine Freq", 0, 127, 64) for n in (1, 2, 3, 4)])]),
    # Vital's own filter models (Analog: Sallen-Key; Ladder), 12 or 24 dB, Blend between low / band / high, in place of the biquad. Set by the Vital converter for a filter of that model in a plain style.
    ("Vital Filter SK", False, [row(enum("flt_vsk", "FSK", "Vital Model Filter 1", ["Off", "Analog", "Ladder", "Dirty", "Digital", "Diode"], 0), enum("flt2_vsk", "F2SK", "Vital Model Filter 2", ["Off", "Analog", "Ladder", "Dirty", "Digital", "Diode"], 0))]),
    # The Filter FX in one of Vital's own models (the same list as the voice filters' Vital Model) and its Blend knob (0..2 over the pot).
    ("Vital Filter FX Model", False, [row(enum("fxf_vsk", "XSK", "Vital Model Filter FX", ["Off", "Analog", "Ladder", "Dirty", "Digital", "Diode", "Comb"], 0), pot("fxf_blend", "XBL", "Vital Filter FX Blend", 0),
                                              num("fxf_cstyle", "XCS", "Vital Filter FX Comb Style", 0, 5, 0), num("fxf_ctrans", "XCT", "Vital Filter FX Blend Transpose", 0, 127, 0))]),
    # Vital's filter style for the Analog model beyond the 12 / 24 dB pair (2 notch / pass swap, 3 dual notch / band, 4 band / peak / notch); 0 reads the filter type's slope.
    ("Vital Filter Style", False, [row(num("flt_vsty", "FST", "Vital Filter 1 Style", 0, 4, 0), num("flt2_vsty", "F2ST", "Vital Filter 2 Style", 0, 4, 0), num("fxf_vsty", "XST", "Vital Filter FX Style", 0, 4, 0))]),
    # Vital's own release curve on the amplitude envelope (0 keeps the analog fall, 1..127 draws the curve of power (pot - 64) * 20 / 63 over the release time).
    ("Vital Amp Release Curve", False, [row(num("vca_rpow", "VRP", "Vital Amp Release Curve", 0, 127, 0))]),
]

# Main: the page you land on, and the only one you should need while playing.
# Deep house is a few sounds and the space between them, so this is the short
# list of what you ride in a mix: how open the sound is and how much it moves
# (cutoff, resonance, filter envelope amount and decay), its weight (sub), and
# how long it hangs in the space (amp decay and release). The last cell is SPACE:
# how much of the preset's own delay and reverb you hear. WT1 Pos, which held it
# before the effects were built in, is still the first cell of the OSC page.
#
# Row one is the filter and row two is the body, so the knobs read the same way
# on every sound. These keys keep their home on their own pages as well.
PERFORM = [FILTER["freq"], FILTER["res"], FILTER["env"], TONE["decay"], SUB["level"], FX["wide"], FX["space"]]

# ---------------------------------------------------------------- collect
def all_params():
    seen, out = set(), []
    for _, _, rows in BANKS:
        for r in rows:
            for s in r:
                if s and s["key"] not in seen:
                    seen.add(s["key"]); out.append(s)
    return out

# The 8 assignable macros were cut in 1.1.1, with the LFOs and the mod slots
# before them: two more pages, and a 46-entry target list to scroll, for
# indirection over knobs that are already one jog away. Schwung's own knob
# mapping reaches any Tablor parameter from the slot settings and is where a
# user looks for exactly this.
PARAMS = all_params()

PARAMS = all_params()

PARAMS = all_params()

# ---------------------------------------------------------------- emit: chain_params
# Schwung 0.12+ parameter visualisations: DECLARED, not detected (the docs'
# own advice). Groups must sit contiguously within one 4-cell row — the page
# layouts above are ordered for exactly that.
VIZ = {
    "vca_a": {"group": "amp",  "role": "attack"},
    "vca_d": {"group": "amp",  "role": "decay"},
    "vca_s": {"group": "amp",  "role": "sustain"},
    "vca_r": {"group": "amp",  "role": "release"},
    "flt_a": {"group": "fenv", "role": "attack"},
    "flt_d": {"group": "fenv", "role": "decay"},
    "flt_s": {"group": "fenv", "role": "sustain"},
    "flt_r": {"group": "fenv", "role": "release"},
    "me1_a": {"group": "menv1", "role": "attack"},
    "me1_d": {"group": "menv1", "role": "decay"},
    "me1_s": {"group": "menv1", "role": "sustain"},
    "me1_r": {"group": "menv1", "role": "release"},
    "me2_a": {"group": "menv2", "role": "attack"},
    "me2_d": {"group": "menv2", "role": "decay"},
    "me2_s": {"group": "menv2", "role": "sustain"},
    "me2_r": {"group": "menv2", "role": "release"},
    "flt_freq": {"group": "flt", "role": "cutoff"},
    "flt_res":  {"group": "flt", "role": "resonance"},
    "flt_type": {"group": "flt", "role": "mode"},
    "sub_wave":  {"kind": "waveform"},
    "wt1_level": {"kind": "fader"},
    "wt2_level": {"kind": "fader"},
    "wt3_level": {"kind": "fader"},
    "sub_level": {"kind": "fader"},
    "noise_level": {"kind": "fader"},
    "volume": {"kind": "fader"},
    "legato": {"kind": "switch"},
}
# Macros page: one widget per knob (src/ui/canvas.js). It draws the value, or a dimmed "N/A" when the preset does not use that macro; it
# learns that from macro_names (a declared read-only key, "Cutoff||Wet|||||"), which the host reads as an extra key.
# The custom dials (canvas.js). Set False to fall back to the host's stock dials. (A glitch when turning a macro was NOT caused by this widget: it
# glitched with the stock dials too.)
MACRO_WIDGETS = True
if MACRO_WIDGETS:
    for _i in range(1, 9):
        VIZ[f"mac{_i}"] = {"kind": f"custom:tinyhouse_mac{_i}", "group": f"mac{_i}", "role": "a", "extra_keys": ["macro_names"]}
    # The Tone page's three wavetable positions are ONE picture across their three cells: each oscillator's wave at its position (the loaded table's shape
    # digests, and wt_on for the oscillators that are sounding). The positions stay three knobs under it.
    for _i in range(1, 4):
        VIZ[f"wt{_i}_pos"] = {"kind": "custom:tinyhouse_waves", "group": "waves", "role": f"p{_i}",
                              "extra_keys": ["wt1_shape", "wt2_shape", "wt3_shape", "wt_on"]}
    # The Effects page's first line: the four stages of the chain as one strip of boxes, filled when the stage is on.
    for _k in ("eq_on", "wide_on", "dly_on", "rev_on"):
        VIZ[_k] = {"kind": "custom:tinyhouse_fxchain", "group": "fxchain", "role": _k}
    # The same strip for the compressor and the master switch (second line of the Effects page, no line between them) and for the three modulation effects (the Mod FX
    # page's first line, their mixes under them).
    for _k in ("comp_on", "fx_on"):
        VIZ[_k] = {"kind": "custom:tinyhouse_fxchain", "group": "fxmaster", "role": _k}
    for _k in ("chor_on", "flan_on", "phas_on"):
        VIZ[_k] = {"kind": "custom:tinyhouse_fxchain", "group": "modfx", "role": _k}
    # The Grain pages (one per sample slot): the sample's shape across the first line with the start point and the scan direction marked, the four main grain
    # knobs under it. Kind and level are read as extra keys (they are on the second line, outside the picture), and so is `_gv`: End, Loop, Loop start/end, Manual pos and Position as one
# short string (the module composes it), for the moving head: those six keys sit past the host's 256-key cache, and a read of one of them re-parses the whole contract.
    for _p in ("smp", "smp2", "smp3"):
        for _k in ("gdens", "glen", "gscan", "gstart"):
            VIZ[f"{_p}_{_k}"] = {"kind": "custom:tinyhouse_grain", "group": f"grain_{_p}", "role": _k,
                                 "extra_keys": [f"{_p}_shape", f"{_p}_kind", f"{_p}_level", f"{_p}_gv"]}
# The wavetable card (src/ui/cards.js): while a Tone-page position knob is held or turned the host raises a bordered picture of the wave over the page
# (Schwung's `card_script`; the host needs 1.9 or later, an older one just shows no card). 124 x 58 is the card, 118 x 52 inside its border.
CARDS = {}
for _i in range(1, 4):
    CARDS[f"wt{_i}_pos"] = {"card_script": f"cards.js#tinyhouse_wave_card_{_i}", "card_w": 124, "card_h": 58}
def chain_param(p):
    if p["type"] == "file":
        d = {"key": p["key"], "name": p["full"], "type": "filepath",
             "root": WT_ROOT, "start_path": WT_ROOT,
             "filter": WT_FILTER, "live_preview": True, "default": ""}
    elif p["type"] == "enum":
        d = {"key": p["key"], "name": p["full"], "type": "enum",
             "options": p["options"], "default": p["options"][p["default"]]}
    else:
        d = {"key": p["key"], "name": p["full"], "type": p["type"],
             "min": p["min"], "max": p["max"], "default": p["default"]}
    if p["key"] in VIZ:
        d["viz"] = VIZ[p["key"]]
    if p["key"] in CARDS:
        d.update(CARDS[p["key"]])
    # access:"write" is what stops the host scrubbing a trigger with a knob.
    # It was being emitted into the wrong builder, so the host treated SAVE
    # as an ordinary enum and every detent of a turn fired it -- reported as
    # one turn of the pot creating 29 preset files.
    if p.get("behavior") == "trigger":
        d["access"] = "write"
    return d

# `preset` is dynamic: its options ARE the preset list, which changes on
# every save -- so the module publishes it into the contract tail at runtime,
# exactly like the wavetable enums. A static entry would duplicate the key.
# The LFO shapes, rates, modes and routes are hidden and only ever set by the converter or a preset load, so the host has
# no use for them in chain_params (they would double its size); they stay in the param table, the state blob and set_param.
# The sample slots' Kind and grain keys are NOT hidden: the remote UI draws its Sample tab from chain_params, and a key missing here is a knob that never appears.
# The 0.9.8 additions that only the Vital converter sets (random modulators, attack curves, the formant filters' own knobs) stay out of it too: the contract was 800 bytes under the host's 64 KB
# limit (it refuses to load the synth past it) and these put it over (loadtest, 2026-11-20).
HIDDEN_FROM_CHAIN = re.compile(r"^(lfo\d_|lr\d_|smp_set$|smp2_set$|smp3_set$|conv_set$|rn\d_|vca_apow$|flt_apow$|me\d_apow$|flt_dpow$|me\d_dpow$|eq\d_ffine$|flt2?_vsk$|fxf_vsk$|fxf_blend$|fxf_cstyle$|fxf_ctrans$|flt2?_vsty$|fxf_vsty$|vca_rpow$|flt2?_(fx|fy|fsp|ftr|fres|fst|var)$)")
# The Vital banks (comp, distortion LFO, unison, envelope, ...) have no control on any page or in the remote UI: only the Vital converter and a preset load set them, as for the LFOs above. The
# one exception is Vital Reverb, which the remote UI names. Together with the keys above this takes the contract from 64.9 KB to about 55 KB, well inside the host's 64 KB.
HIDDEN_BANK_KEYS = {s["key"] for name, _, rws in BANKS if name.startswith("Vital ") and name != "Vital Reverb" for r in rws for s in r if s}
chain = [chain_param(p) for p in PARAMS if p["key"] != "preset" and not HIDDEN_FROM_CHAIN.match(p["key"]) and p["key"] not in HIDDEN_BANK_KEYS]
chain_json = json.dumps(chain, separators=(",", ":"))
assert "%" not in chain_json, "chain_params must be printf-safe (served verbatim)"

# ---------------------------------------------------------------- emit: ui_pages.json
# Data for Tablor's OWN Shadow UI (ui_chain.js) — the 9W9 treatment: jog flips
# pages, Shift+jog jumps sections, 8 encoders edit the visible page.
# NOTE: the module must NOT serve ui_hierarchy — the Shadow UI's hierarchy
# editor takes precedence over ui_chain.js whenever a module offers one.
PAGE_MAP = [  # (bank name, row index, page title). Section = bank name. The root is the preset browser (TinyK's pattern).
    ("Main",    0, "Main"),
    ("Tone",    1, "Tone"),
    ("Macros",  0, "Macros"),
    ("Effects Page", 0, "Effects"),
    ("Mod FX Page",  0, "Mod FX"),
    ("Global",  0, "Voice"),
    ("Grain",   0, "Grain 1"),
    ("Grain",   1, "Grain 2"),
    ("Grain",   2, "Grain 3"),
]

# THE HOST CACHES ONLY THE FIRST 256 DECLARED KEYS (MAX_CHAIN_PARAMS in Schwung's chain host). A set of a key past that is a cache miss, and a miss
# makes the host fetch the whole chain_params again and re-parse it on the SPI callback (~23 ms here, at most once per 250 ms), which is a dropout of every
# slot each time. Schwung's automation lanes look the key up on every set, so any key a hand can turn must be inside the first 256. The pages on the Move
# come first, then the rest in the old order; the module adds the dynamic keys the Move turns (the wavetable selects, bank, the demo keys, preset) right
# after them, at `tb_chain_params_prio_len`. What is left past 256 are the remote UI's own controls and the data channels.
DEVICE_KEYS = []
_rows = {(name, i): r for name, _, rws in BANKS for i, r in enumerate(rws)}
for _bank, _ri, _title in PAGE_MAP:
    for _s in _rows[(_bank, _ri)]:
        if _s and _s["type"] != "file" and _s["key"] not in DEVICE_KEYS:
            DEVICE_KEYS.append(_s["key"])
DYNAMIC_DEVICE_KEYS = ["wt1_select", "wt2_select", "wt3_select", "bank", "demo_on", "demo_pack", "demo_part", "preset"]
_head = [d for d in chain if d["key"] in DEVICE_KEYS]
_rest = [d for d in chain if d["key"] not in DEVICE_KEYS]
assert len(_head) == len(DEVICE_KEYS), "a page key is missing from chain_params"
chain = _head + _rest
chain_json = json.dumps(chain, separators=(",", ":"))
CHAIN_PRIO_LEN = len(json.dumps(_head, separators=(",", ":"))) - 1      # offset of the comma that starts the first non-page entry
assert chain_json[CHAIN_PRIO_LEN] == ","
assert len(_head) + len(DYNAMIC_DEVICE_KEYS) <= 200, "the keys the Move turns must stay well inside the host's 256-key cache"

def page_slot(p):
    if p is None:
        return None
    d = {"k": p["key"], "n": p["short"], "full": p["full"], "t": p["type"]}
    if p["type"] == "enum":
        d["options"] = p["options"]
    elif p["type"] == "int":
        d["min"], d["max"] = p["min"], p["max"]
    if p.get("behavior"):
        d["b"] = p["behavior"]
    return d

def build_pages():
    rows = {(name, i): r for name, _, rws in BANKS for i, r in enumerate(rws)}
    pages = []
    for bank, ri, title in PAGE_MAP:
        pages.append({
            "name": title,
            "sec": bank,
            "slots": [page_slot(s) for s in rows[(bank, ri)]],
        })
    return {"pages": pages}

ui_pages = build_pages()

# ---------------------------------------------------------------- ui_hierarchy
# Schwung 0.12+: the STOCK hierarchy editor draws knob pages with viz graphics,
# has a native preset-browser level (list_param/count_param/name_param), a
# file browser for filepath params, and an on-screen keyboard for `string`
# params. Tablor no longer ships its own editor — this hierarchy IS the UI.
def hier_param(p):
    d = {"key": p["key"], "name": p["full"]}
    if p["type"] == "file":
        d.update(type="filepath", root=WT_ROOT, start_path=WT_ROOT,
                 filter=WT_FILTER, live_preview=True)
    elif p["type"] == "enum":
        d.update(type="enum", options=p["options"])
    else:
        d.update(type="int", min=p["min"], max=p["max"])
    return d

def build_hierarchy():
    rows = {(name, i): r for name, _, rws in BANKS for i, r in enumerate(rws)}
    levels = {}

    # The wavetable pickers are the knobs users reach for first on an OSC page,
    # and they were the ones that could not move. The note at the top of this
    # file already says why -- "it HAS to be an enum, the stock UI cannot TURN a
    # filepath" -- but hier_param() emits filepath, so the intent never reached
    # the contract. Reported by a tester as "I want to change wavetables with
    # the pots... right now it's not possible".
    #
    # So the KNOB drives an enum (wtN_select), whose options the DSP publishes
    # at runtime from the scanner.
    #
    # NEITHER the pack chooser NOR the filepath browsers appear on the device.
    #
    # Both were opaque to a knob, so each claimed a whole cell and the pair of
    # them pushed extra MAIN pages: one showing two brackets under the stock
    # "sample" graphic -- a fixed shape that never reads the file
    # (viz_draw.mjs drawSample), so it said nothing about the loaded table --
    # and one that was just a three-row list of pack names. Reported as "what
    # is this shit page, we don't need this on the module".
    #
    # Nothing is lost. wtN_select is on a knob, and an enum is divable, so
    # hold+click opens a scrolling picker over every table: with no pack chosen
    # the list is "All", and the option cap (128) is comfortably above the
    # libraries this ships against. wt_pack and the wtN_table keys still exist
    # and still stream -- the web UI drives both -- they simply have no cell on
    # the Move.
    level_ids = []
    for bank, ri, title in PAGE_MAP:
        if bank == "Preset":
            continue                      # the browser lives on root (below)
        lid = title.lower().replace(" ", "").replace(".", "").replace("-", "")
        params, knobs = [], []
        for s in rows[(bank, ri)]:
            if not s:
                continue
            if s["type"] == "file":
                sel = s["key"].split("_")[0] + "_select"
                # Turnable first; options come from get_param("chain_params").
                params.append({"key": sel, "name": s["full"] + " Table", "type": "enum"})
                knobs.append(sel)
            else:
                params.append(hier_param(s))
                knobs.append(s["key"])
        if lid == "main":
            # The bank (a folder of presets) is the 8th knob of the first page; like the wavetable selects, its options come from
            # get_param("chain_params"), where the DSP lists the folders it found.
            params.append({"key": "bank", "name": "Bank", "type": "enum"})
            knobs.append("bank")
        levels[lid] = {
            "name": title.title() if title.isupper() else title,
            "params": params,
            "knobs": knobs,
        }
        level_ids.append((lid, levels[lid]["name"]))

    # The demo mode: play a MIDI part of an uploaded song on this slot (the remote UI's MIDI import). The switch is a plain enum; the pack and the part
    # are enums whose options the DSP publishes from what it scanned (like Bank), so a knob steps through them.
    levels["demo"] = {
        "name": "Demo",
        "params": [{"key": "demo_on", "name": "Demo", "type": "enum", "options": ["Off", "On"]},
                   {"key": "demo_pack", "name": "Demo Pack", "type": "enum"},
                   {"key": "demo_part", "name": "Demo Part", "type": "enum"}],
        "knobs": ["demo_on", "demo_pack", "demo_part"],
    }
    level_ids.append(("demo", "Demo"))

    # ROOT declares the preset browser AND the PERFORM knobs -- the obxd pattern
    # the planner is built for ("a level is routinely both the Main knob page
    # and the preset browser"). That yields, in jog order:
    #
    #     [Presets] > Main > Osc > Unison > Shape > Osc 3 > Filter > ...
    #
    # so you land on the browser, pick a sound, and every jog from Main goes
    # forward through the SECTIONS. A separate presets level put the browser
    # *between* Main and the sections instead, which meant one jog off Main
    # dropped you straight back into the preset list.
    #
    # Rename is a params-only `string` entry (opens the stock keyboard; the
    # DSP renames the preset's file). Save Preset is a knob on Global.
    # The root is the preset browser, the way TinyK's is: list_param / count_param / name_param make the host
    # draw a scrolling list of the module's presets (served by the DSP from Schwung's store), and the
    # levels below it are the pages: Main, Tone, Effects. Same jog order as TinyK: browser, then pages.
    root_params = [{"level": lid, "label": label} for lid, label in level_ids]
    levels["root"] = {
        "name": MODULE_NAME,
        "label": MODULE_NAME,
        "list_param": "preset",
        "count_param": "preset_count",
        "name_param": "preset_name",
        "params": root_params,
        "knobs": [],
    }

    # (The WT Pack items level lived here. It existed so the host would
    # re-read the contract after a pack commit -- armContractSettle -- which
    # only matters if the pack can be changed from the device, and it no
    # longer can. The web UI sets wt_pack directly.)

    # No list_param: a level that declares one renders as a scrolling browser
    # INSTEAD of its knobs (page_plan.mjs: "the browser takes priority"), and
    # this page needs its four cells. PRESET is an enum whose options are the
    # live preset list, so a turn steps presets and a dive opens the picker.
    return {"levels": levels}

hierarchy_json = json.dumps(build_hierarchy(), separators=(",", ":"))

# ---------------------------------------------------------------- emit: module.json
module_json = {
    "id": MODULE_ID,
    "name": MODULE_NAME,
    "abbrev": "TINYH",
    "version": VERSION,
    "description": "3-oscillator wavetable synth - 8-voice poly, unison to 16, hard sync, sub, noise, "
                   "multimode filter, four envelopes (two routable). Serum/Vital/Ableton "
                   "wavetables via Move Manager.",
    "author": "athousanddetails; after Wavetable (Roland Rabien / FigBug, BSD-3)",
    "dsp": "dsp.so",
    "api_version": 2,
    "capabilities": {
        "audio_out": True,
        "audio_in": False,
        "midi_in": True,
        "midi_out": False,
        "chainable": True,
        "component_type": "sound_generator",
    },
}

# ---------------------------------------------------------------- emit: params.h
def c_escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')

lines = [
    "/* Generated by tools/gen_params.py — DO NOT EDIT.",
    " * The parameter surface: IDs, ranges, defaults, and the chain_params",
    " * JSON template (wavetable option lists are spliced in at runtime). */",
    "#ifndef TABLOR_PARAMS_H",
    "#define TABLOR_PARAMS_H",
    "",
    f'#define TB_VERSION "{VERSION}"',
    f'#define TB_MODULE_ID "{MODULE_ID}"',
    f"#define TB_PARAM_COUNT {len(PARAMS)}",
    "",
    "typedef enum { TB_INT = 0, TB_ENUM = 1, TB_PATH = 2 } tb_param_type_t;",
    "",
    "typedef struct {",
    "    const char *key;",
    "    tb_param_type_t type;",
    "    float min, max, def;              /* enums: def is the option index */",
    "    int n_options;                    /* 0 for non-enums                */",
    "    const char *const *options;       /* NULL for non-enums             */",
    "} tb_param_t;",
    "",
    "/* TB_PATH params (the wavetable files) store a string, not a float. */",
    "#define TB_WT_SLOTS 3",
    "",
]
enum_names = []
for i, p in enumerate(PARAMS):
    enum_names.append(f"    TB_P_{p['key'].upper()} = {i},")
lines += ["enum {"] + enum_names + ["};", ""]

# Option string tables (shared where identical)
opt_tables, opt_index = [], {}
for p in PARAMS:
    if p["type"] == "enum" and p["options"] is not None:
        key = tuple(p["options"])
        if key not in opt_index:
            name = f"tb_opts_{len(opt_tables)}"
            opt_index[key] = name
            opts = ", ".join(f'"{c_escape(o)}"' for o in p["options"])
            opt_tables.append(f"static const char *const {name}[] = {{ {opts} }};")
lines += opt_tables + [""]

rows = []
for p in PARAMS:
    if p["type"] == "file":
        rows.append(f'    {{ "{p["key"]}", TB_PATH, 0, 0, 0, 0, 0 }},')
    elif p["type"] == "enum":
        tbl = opt_index[tuple(p["options"])]
        rows.append(f'    {{ "{p["key"]}", TB_ENUM, 0, {len(p["options"]) - 1}, '
                    f'{p["default"]}, {len(p["options"])}, {tbl} }},')
    else:
        rows.append(f'    {{ "{p["key"]}", TB_INT, {p["min"]}, {p["max"]}, {p["default"]}, 0, 0 }},')
lines += ["static const tb_param_t tb_params[TB_PARAM_COUNT] = {"] + rows + ["};", ""]

lines += [
    "/* chain_params: fully static, served verbatim. It MUST stay cheap —",
    " * get_param runs on the SPI callback. */",
    f'static const char *tb_chain_params_json =\n    "{c_escape(chain_json)}";',
    "/* Where the module inserts the dynamic keys the Move turns (see gen_params.py: the host caches only the first 256 declared keys). */",
    f"static const unsigned tb_chain_params_prio_len = {CHAIN_PRIO_LEN};",
    "/* Every key a hand can turn on the Move: all of them must sit inside the first 256 declared (tools/loadtest.cpp checks it). */",
    "static const char *const tb_device_keys[] = { " + ", ".join(f'"{k}"' for k in DEVICE_KEYS + DYNAMIC_DEVICE_KEYS) + " };",
    "",
    "/* ui_hierarchy for the STOCK Shadow UI (Schwung 0.12+): knob pages",
    " * with declared viz graphics, native preset browser, file browser,",
    " * on-screen keyboard for renames. Tablor ships no custom editor. */",
    f'static const char *tb_ui_hierarchy_json =\n    "{c_escape(hierarchy_json)}";',
    "",
]

# User-macro wiring: pot index, selector index, and option->param-index map.
lines += [
    "#endif /* TABLOR_PARAMS_H */",
]

# ---------------------------------------------------------------- write
out_module = ROOT / "src" / "module.json"
out_header = ROOT / "src" / "dsp" / "params.h"

out_pages = ROOT / "src" / "ui_pages.json"

out_module.write_text(json.dumps(module_json, indent=2) + "\n")
out_header.write_text("\n".join(lines) + "\n")
out_pages.write_text(json.dumps(ui_pages, separators=(",", ":")) + "\n")

msize = out_module.stat().st_size
print(f"params: {len(PARAMS)}")
print(f"module.json: {msize} bytes  ({'OK' if msize < 8192 else 'OVER 8KB CAP!'})")
print(f"params.h: {out_header.stat().st_size} bytes  (chain fmt {len(chain_json)} bytes)")
assert msize < 8192, "module.json exceeds Schwung's 8 KB loader cap"
