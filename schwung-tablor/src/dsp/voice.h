/* Tablor voice — the per-voice signal path, structured after
 * WavetableVoice.cpp in the original (FigBug/Wavetable, BSD-3):
 *
 *   osc1/osc2/osc3 (unison WT) ─┬─ preFilter ── Filter ──┐
 *   sub, noise ─────────────────┴─ postFilter ───────────┴─ VCA ADSR ─ out
 *   (each source can also go to Filter 2, or to both by a balance: see addSource)
 *
 * Per-source pre/post-filter routing, per-voice filter ADSR, 3 per-voice
 * glide via noteSmoother. Parameters are read from the engine's pot
 * array each block through param_map.h; the mod matrix contributes offsets.
 */
#pragma once

#include "../ported/wt_oscillator.h"
#include "../ported/analog_tables.h"
#include "../ported/filter.h"
#include "comb.h"
#include "formant.h"
#include "sample.h"
#include "granular.h"
#include "../ported/adsr.h"
#include "param_map.h"
#include "params.h"
#include "saturate.h"
#include "vital_sk.h"

#include <cstdlib>
#include <cstring>

namespace tb {

/* The glide smoother's time (seconds to cross the whole note range, 127 semitones) for a glide of `semis` semitones. Law Serum: the Glide knob as it always was. Law Vital (glide_vlaw,
 * measured on the plugin, calibration/vital_porta_probe.py): the knob is 0.001..16 s (log) for the whole glide, or for each octave when Scale is Per octave. */
inline float glideSmootherTime(const float *P, float semis)
{
    if (P[TB_P_GLIDE_VLAW] < 0.5f) return potGlideTime(P[TB_P_GLIDE]);
    const float t = 0.001f * std::pow(16000.0f, pot01(P[TB_P_GLIDE]));
    return P[TB_P_GLIDE_VSCALE] >= 0.5f ? t * (127.0f / 12.0f) : t * (127.0f / std::max(semis, 0.01f));
}

inline float velocityToGain(float velocity, float sensitivity)
{
    float v = velocity * sensitivity + 1.0f - sensitivity;
    float g = v * std::pow(25.0f, v) * 0.04f;
    return std::clamp(g, 0.0f, 1.0f);
}

/* The velocity law the converter writes (VCA Velocity Law = Serum): Serum's loudness does not depend on the velocity unless a route puts it there, and then it is linear
 * in the velocity (calibration/vel_amp probe: a plain saw is flat from 20 to 127, a velocity->Voice Amp route at 100 % follows 20 log10(vel / 127)). The curve is anchored
 * at velocity 100, the one the calibrations play, where it gives the original law's gain for the default tracking (0.4856), so every level fitted at velocity 100 holds. */
inline float velocityToGainSerum(float velocity, float sensitivity)
{
    constexpr float kAt100 = 0.4856f, kV100 = 100.0f / 127.0f;
    return std::clamp(kAt100 * ((1.0f - sensitivity) + sensitivity * velocity / kV100), 0.0f, 1.0f);
}

inline constexpr int kBlock = 128;
inline constexpr int kOscs = TB_WT_SLOTS;

/* The most wavetable streams the engine will run at once, across every voice.
 *
 * 64 is upstream's own worst case (8 voices x 2 oscs x unison 4), the load its
 * benchmark was written around. Raising the limits without a budget would have
 * made the new worst case 384 streams, which no patch needs and this box will
 * not survive. The share is decided by the Voices control and not by how many
 * notes happen to be down, so a chord cannot change a held note's unison and
 * make it step: fewer voices buys wider stacks, and 4 voices of 7+7 fit whole. */
inline constexpr int kStreamBudget = 64;

/* Everything a voice needs from the engine each block. */
/* Where a spare envelope can be pointed. The ORDER is the option order of
 * meN_dst in gen_params.py -- the DSP receives the selection as an index, so
 * the two lists are one contract and must move together. */
enum ModDst {
    MD_NONE = 0,
    MD_WT1_POS, MD_WT2_POS, MD_WT1_LEVEL, MD_WT2_LEVEL, MD_WT1_TUNE, MD_WT2_TUNE,
    MD_WT1_BEND, MD_WT2_BEND, MD_WT1_FORMANT, MD_WT2_FORMANT, MD_WT1_PAN, MD_WT2_PAN,
    MD_FLT_FREQ, MD_FLT_RES, MD_SUB_LEVEL, MD_NOISE_LEVEL, MD_AMP,
    /* Appended, never inserted: saved presets store the index. */
    MD_WT3_POS, MD_WT3_LEVEL, MD_WT1_SYNC, MD_WT2_SYNC, MD_WT3_SYNC, MD_FLT2_FREQ,
    MD_FXF_FREQ,                 /* the effects block's Filter FX cutoff: read by the engine from the newest voice (fxfCutoffMod) */
    MD_DIST_WET,                 /* the effects block's Distortion Wet, as a fraction of its 0..100 % range: read by the engine from the newest voice (distWetMod) */
    MD_COMP_WET,                 /* the effects block's Comp Wet (its parallel mix), as a fraction of 0..100 %: read from the newest voice (compWetMod) */
    /* A granular sample slot's Density, Grain Length and Scan Rate KNOBS (granular.h): the offset is added to the knob (0..1; Scan: 400 % of speed per unit), as Serum's routes do. */
    MD_SMP1_GDENS, MD_SMP1_GLEN, MD_SMP1_GSCAN, MD_SMP2_GDENS, MD_SMP2_GLEN, MD_SMP2_GSCAN, MD_SMP3_GDENS, MD_SMP3_GLEN, MD_SMP3_GSCAN,
    /* The Level Out KNOB of the Utils that ends a rack (its gain is (knob / 0.5)^2, the knob 0.5 = unity), moved by an LFO / envelope route: the main rack's, then bus 1's and bus 2's. Read by the engine from the newest voice. */
    MD_UTIL_OUT, MD_BUS1_OUT, MD_BUS2_OUT,
    /* A sample slot's Level KNOB (squared after, as for the wavetables'), moved by an LFO / envelope route: a Sample / Granular oscillator whose volume comes from a modulator. */
    MD_SMP1_LEVEL, MD_SMP2_LEVEL, MD_SMP3_LEVEL,
    MD_FXF_WET,                  /* the effects block's Filter FX Wet, as a fraction of its 0..100 % range: read by the engine from the newest voice (fxfWetMod) */
    MD_WT1_PITCH, MD_WT2_PITCH, MD_WT3_PITCH,     /* an oscillator's pitch in semitones, 48 per unit of the route (a pitch envelope: a kick's drop is 24 st, past the +-12 st of the Tune destinations) */
    MD_WT1_WARP, MD_WT2_WARP, MD_WT3_WARP,     /* an oscillator's FM / PD / RM / AM / filter warp amount (Serum's Warp knob 0..1, added to wtN_xa), moved by an LFO / envelope route */
    MD_EQ1_FREQ, MD_EQ1_GAIN, MD_EQ2_FREQ, MD_EQ2_GAIN, MD_EQ3_FREQ, MD_EQ3_GAIN, MD_EQ4_FREQ, MD_EQ4_GAIN,     /* the master EQ's band Freq (a fraction of the log scale) and Gain (a fraction of 48 dB): read by the engine from the newest voice (eqMod) */
    MD_DISTV_DRIVE, MD_DISTV_CUT, MD_DISTV_RES, MD_DISTV_BLEND,     /* the Vital distortion's Drive and filter Cutoff, Res and Blend, each a fraction of the control's range: read by the engine from the newest voice (distVMod) */
    MD_WT1_FINE, MD_WT2_FINE, MD_WT3_FINE,     /* an oscillator's pitch in semitones, 2 per unit of the route: a vibrato of a few cents (Vital's osc Tune knob spans 2 st), which the 48-per-unit Pitch destination cannot hold in a 127-step amount */
    MD_FLT_BLEND, MD_FLT2_BLEND,     /* Vital's voice filter Blend knob (a fraction of its 0..2 range): added to the stored blend of the Blend, Phaser and Comb filters before the block is run */
    MD_VOICE_PITCH, MD_VOICE_FINE,     /* Vital's voice_transpose (48 semitones per unit of the route) and voice_tune (2 per unit): the whole voice, every oscillator, the sub, the samples and the filters' key tracking */
    MD_FLT_CTRANS, MD_FLT2_CTRANS,     /* Vital's comb filter Blend Transpose (a fraction of its 0..84 semitone range), added to the stored transpose of the Vital comb law */
    MD_COUNT
};
/* Saved presets store the destination INDEX: a new destination goes at the end, never in front of one that has shipped (0.9.8 shipped MD_FLT_BLEND = 64). */
static_assert(MD_WT1_FINE == 61 && MD_FLT_BLEND == 64 && MD_FLT2_BLEND == 65 && MD_VOICE_PITCH == 66 && MD_VOICE_FINE == 67 && MD_FLT_CTRANS == 68 && MD_FLT2_CTRANS == 69, "a mod destination moved: presets store the index");

/* The destinations whose route is added to a level KNOB, before the knob's taper. */
inline bool isLevelDst(int d) {
    return d == MD_WT1_LEVEL || d == MD_WT2_LEVEL || d == MD_WT3_LEVEL || d == MD_SUB_LEVEL || d == MD_NOISE_LEVEL || (d >= MD_SMP1_LEVEL && d <= MD_SMP3_LEVEL);
}

inline constexpr int kModEnvs = 4;      /* the routable envelopes: 1 and 2 are on the pages, 3 and 4 (Vital presets) are hidden */

struct VoiceContext {
    const float *pots = nullptr;            /* TB_PARAM_COUNT raw values   */
    const Wavetable *table[kOscs] = {};
    std::shared_ptr<const SampleSet> sample3; /* the third's */
    std::shared_ptr<const SampleSet> sample2; /* the second sample oscillator's set (smp2_*), the same way */
    std::shared_ptr<const SampleSet> sample; /* the sample oscillator's set, if one is loaded; a voice keeps a share while it plays from it (sample.h) */
    int streamBudget = kStreamBudget;       /* this voice's share, see kStreamBudget */
    AnalogTables *analog = nullptr;
    float modWheel = 0.0f;                  /* 0..1  */
    float aftertouch = 0.0f;                /* 0..1  */
    float pitchBendSemis = 0.0f;            /* +/- pb_range already applied */
    float bpm = 120.0f;
    float lfoFree[8] = {};                  /* where each Free-mode LFO is in its cycle (0..1) */
    float *bus[4] = {};                     /* the FX buses' inputs (bus 1 L, R, bus 2 L, R) the voices add their sends to; null: that bus is not in use */
    float *dry[2] = {};                     /* the sources routed Out: past the filter and the effects, added to the output after them (null: nowhere to put them) */
};

inline constexpr int kLfos = 8, kLfoRoutes = 16, kLfoShape = 16, kLfoHi = 64, kRnds = 4;
/* Route sources, by number (a saved preset stores them): 1..4 LFO 1..4, 5..8 random, 9 velocity, 10 aftertouch, 11..14 LFO 5..8. The first four LFOs are Serum's and Vital's; 5..8 are Vital's own. */
inline constexpr int kLfosA = 4, kSrcVel = kLfosA + kRnds + 1, kSrcAft = kSrcVel + 1, kRouteSrcs = kSrcAft + (kLfos - kLfosA);
/* LFO l's slot among the route sources (0-based): the first four sit first, LFO 5..8 after aftertouch. */
inline constexpr int lfoSlot(int l) { return l < kLfosA ? l : kSrcAft + (l - kLfosA); }
/* The strides the code below walks the parameter table with: if the generator ever reorders them this stops the build. */
static_assert(TB_P_LFO2_RATE - TB_P_LFO1_RATE == 3 + kLfoShape && TB_P_LFO1_S0 == TB_P_LFO1_RATE + 2 &&
              TB_P_LFO1_MODE == TB_P_LFO1_RATE + 1 && TB_P_LFO1_SMOOTH == TB_P_LFO1_S15 + 1 &&
              TB_P_LFO2_SMOOTH - TB_P_LFO1_SMOOTH == 3 + kLfoShape, "LFO parameters must be consecutive: rate, mode, 16 shape points, smooth");
static_assert(TB_P_LFO2_TEMPO == TB_P_LFO1_TEMPO + 1 && TB_P_LFO4_TEMPO == TB_P_LFO1_TEMPO + 3, "the LFO Tempo switches are consecutive");
static_assert(TB_P_LFO1_H0 == TB_P_LFO1_HR + 1 && TB_P_LFO1_H63 - TB_P_LFO1_H0 == kLfoHi - 1 && TB_P_LFO2_HR - TB_P_LFO1_HR == 1 + kLfoHi, "the LFO Hi-Res switch and its 64 points are consecutive");
static_assert(TB_P_LFO1_S15 - TB_P_LFO1_S0 == kLfoShape - 1, "LFO shape points must be consecutive");
/* LFO 5..8 (hidden "Vital LFO 5-8" bank): rate, mode, 16 shape points, smooth, Hi-Res switch, 64 points, delay, fade, phase, tempo, in that order, 88 to an LFO */
static_assert(TB_P_LFO5_MODE == TB_P_LFO5_RATE + 1 && TB_P_LFO5_S0 == TB_P_LFO5_RATE + 2 && TB_P_LFO5_S15 - TB_P_LFO5_S0 == kLfoShape - 1 && TB_P_LFO5_SMOOTH == TB_P_LFO5_S15 + 1 &&
              TB_P_LFO5_HR == TB_P_LFO5_SMOOTH + 1 && TB_P_LFO5_H0 == TB_P_LFO5_HR + 1 && TB_P_LFO5_H63 - TB_P_LFO5_H0 == kLfoHi - 1 && TB_P_LFO5_DELAY == TB_P_LFO5_H63 + 1 &&
              TB_P_LFO5_FADE == TB_P_LFO5_DELAY + 1 && TB_P_LFO5_PHASE == TB_P_LFO5_DELAY + 2 && TB_P_LFO5_TEMPO == TB_P_LFO5_DELAY + 3 &&
              TB_P_LFO6_RATE - TB_P_LFO5_RATE == 88 && TB_P_LFO8_TEMPO - TB_P_LFO5_TEMPO == 3 * 88, "LFO 5..8 parameters must be consecutive, 88 to an LFO");
static_assert(TB_P_LR9_DST == TB_P_LR9_SRC + 1 && TB_P_LR9_AMT == TB_P_LR9_SRC + 2 && TB_P_LR9_BIP == TB_P_LR9_SRC + 3 && TB_P_LR16_SRC - TB_P_LR9_SRC == 7 * 4, "LFO routes 9..16 are src, dst, amt, bip, consecutive");

/* Where LFO l keeps its parameters in the table, for all eight. */
struct LfoKeys { int rate, mode, s0, smooth, hr, h0, delay, fade, phase, tempo; };
inline constexpr LfoKeys lfoKeys(int l)
{
    if (l < kLfosA)
        return { TB_P_LFO1_RATE + l * (TB_P_LFO2_RATE - TB_P_LFO1_RATE), TB_P_LFO1_MODE + l * (TB_P_LFO2_MODE - TB_P_LFO1_MODE), TB_P_LFO1_S0 + l * (TB_P_LFO2_S0 - TB_P_LFO1_S0),
                 TB_P_LFO1_SMOOTH + l * (TB_P_LFO2_SMOOTH - TB_P_LFO1_SMOOTH), TB_P_LFO1_HR + l * (TB_P_LFO2_HR - TB_P_LFO1_HR), TB_P_LFO1_H0 + l * (TB_P_LFO2_H0 - TB_P_LFO1_H0),
                 TB_P_LFO1_DELAY + l, TB_P_LFO1_FADE + l, TB_P_LFO1_PHASE + l, TB_P_LFO1_TEMPO + l };
    const int b = TB_P_LFO5_RATE + (l - kLfosA) * 88;
    return { b, b + 1, b + 2, b + 18, b + 19, b + 20, b + 84, b + 85, b + 86, b + 87 };
}
/* Route r's source parameter (its destination, amount and polarity follow it): routes 1..8 and 9..16 are two runs of four parameters each. */
inline constexpr int lrSrc(int r) { return r < 8 ? TB_P_LR1_SRC + r * 4 : TB_P_LR9_SRC + (r - 8) * 4; }
static_assert(TB_P_RN2_STYLE - TB_P_RN1_STYLE == 3 && TB_P_RN1_RATE == TB_P_RN1_STYLE + 1 && TB_P_RN1_TEMPO == TB_P_RN1_STYLE + 2, "the random modulators' parameters must be style, rate, tempo, consecutive");
static_assert(TB_P_LR2_SRC - TB_P_LR1_SRC == 4 && TB_P_LR1_DST == TB_P_LR1_SRC + 1 && TB_P_LR1_AMT == TB_P_LR1_SRC + 2 &&
              TB_P_LR1_BIP == TB_P_LR1_SRC + 3, "LFO route parameters must be src, dst, amt, bip, consecutive");

/* An LFO's rate pot in Hz: 0.05 to 120, logarithmic. */
inline float potLfoHz(float v) { return 0.05f * std::pow(2400.0f, pot01(v)); }

/* LFO l's rate in Hz as it plays: with its Tempo switch on (a synced LFO of the source preset) the pot is the rate at 120 BPM and the host tempo scales it; off, the pot's Hz as it always was.
 * No host tempo (0) counts as 120, the same clamp the delay and the granular oscillator use. */
inline float lfoHz(const float *P, int l, float bpm)
{
    const float hz = potLfoHz(P[lfoKeys(l).rate]);
    if (P[lfoKeys(l).tempo] < 0.5f) return hz;
    return hz * (bpm >= 20.0f ? std::clamp(bpm, 40.0f, 300.0f) : 120.0f) / 120.0f;
}

/* An LFO's Smooth pot as the time constant of a one-pole low-pass on its output: 2 ms to 0.7 s, logarithmic; 0 is off. Serum's Smooth was measured
 * as such a filter, of the same absolute time constant at any LFO rate (calibration/dev/probe_lfo_smooth.py). */
inline float potLfoSmoothTau(float v) { return v < 0.5f ? 0.0f : 0.002f * std::pow(350.0f, pot01(v)); }

/* Vital's LFO Delay (0..4 s), Fade In (0..8 s) and Phase (0..1 of a cycle), linear over the pot (hidden "Vital LFO" bank; 0 leaves an LFO as it was). */
inline float lfoDelaySec(const float *P, int l) { return 4.0f * pot01(P[lfoKeys(l).delay]); }
inline float lfoFadeSec(const float *P, int l) { return 8.0f * pot01(P[lfoKeys(l).fade]); }
inline float lfoPhaseOff(const float *P, int l) { return pot01(P[lfoKeys(l).phase]); }
/* Vital's envelope Delay and Hold: 0..4 s as 4 x (pot/127)^4 (its knobs are a quartic of 0..sqrt 2). */
inline float potEnvStage(float v) { const float x = pot01(v), x2 = x * x; return 4.0f * x2 * x2; }

inline int lfoModeOf(const float *P, int lfo) { return (int) std::lround(P[lfoKeys(lfo).mode]); }
inline bool lfoIsFree(const float *P, int lfo) { return lfoModeOf(P, lfo) == 1; }          /* 0 Trig, 1 Free, 2 Env */
inline bool lfoIsOneShot(const float *P, int lfo) { return lfoModeOf(P, lfo) == 2; }
/* Modes 3 and 4 are not oscillators: the LFO is a lookup of the note being played in its Hi-Res table (Vital's Key and Key-in-octave route sources, which a route can bend with a curve of its own).
 * Mode 3 (Key) reads point note / 2, 0..63; mode 4 (Key Oct) reads point (note mod 12), 0..11. */
inline bool lfoIsKey(const float *P, int lfo) { return lfoModeOf(P, lfo) >= 3; }
inline float lfoKeyValue(const float *P, int lfo, float note)
{
    const int n = std::clamp((int) std::lround(note), 0, 127);
    const int i = lfoModeOf(P, lfo) == 3 ? std::min(n / 2, kLfoHi - 1) : n % 12;
    return pot01(P[lfoKeys(lfo).h0 + i]);
}

/* The shape: 16 points of one cycle, 0..127 for 0..1, read round the loop with linear interpolation; or, with the LFO's Hi-Res switch on, its 64 points read the same way. */
inline float lfoShapeAt(const float *P, int lfo, float phase, bool oneShot = false)
{
    if (P[lfoKeys(lfo).hr] >= 0.5f) {
        const float x = oneShot ? std::min(phase, 1.0f) * (float) (kLfoHi - 1) : phase * (float) kLfoHi;
        const int i0 = oneShot ? std::min((int) x, kLfoHi - 1) : ((int) x) & (kLfoHi - 1);
        const float fr = x - std::floor(x);
        const int base = lfoKeys(lfo).h0;
        const float a = P[base + i0], b = P[base + (oneShot ? std::min(i0 + 1, kLfoHi - 1) : ((i0 + 1) & (kLfoHi - 1)))];
        return pot01(a + (b - a) * fr);
    }
    /* a one-shot (Env) LFO runs the table from its first point to its last once: the last point is the held end value */
    const float x = oneShot ? std::min(phase, 1.0f) * (float) (kLfoShape - 1) : phase * (float) kLfoShape;
    const int i0 = oneShot ? std::min((int) x, kLfoShape - 1) : ((int) x) & (kLfoShape - 1);
    const float fr = x - std::floor(x);
    const int base = lfoKeys(lfo).s0;
    const float a = P[base + i0], b = P[base + (oneShot ? std::min(i0 + 1, kLfoShape - 1) : ((i0 + 1) & (kLfoShape - 1)))];
    return pot01(a + (b - a) * fr);
}

/* Vital's level law, for a Vital preset (the Vital Unison switch marks one). Measured through the plugin with a sine on an oscillator or a sample slot: the level knob a route adds to is not capped at 1 (a knob of 1 and
 * a route of 1 plays 12 dB up, 2 squared), and the knob's own part does not start where it will sit: a note begins with it at about 0.78 and it glides to its value in some 100 ms (a static level of 0.5 starts
 * 8 dB up, a level of 1 starts 4 dB down, and an envelope route on a level of 0 adds to that 0.78 at the note's start). The glide is two exponentials, 4 ms and 32 ms. */
inline float vitalLevelBase(float base, float age)
{
    if (age > 0.3f) return base;
    const float g = 0.35f * std::exp(-age * 250.0f) + 0.65f * std::exp(-age * (1.0f / 0.032f));
    return base + (0.78f - base) * g;
}

class Voice {
public:
    explicit Voice(AnalogTables &analog) : sub(analog), noise(analog) {}

    void setSampleRate(double sr)
    {
        sampleRate = sr;
        for (auto &o : osc) o.setSampleRate(sr);
        sub.setSampleRate(sr); noise.setSampleRate(sr);
        filter.setSampleRate(sr);
        filter2.setSampleRate(sr);
        vb1.setSampleRate(sr);
        vb2.setSampleRate(sr);
        sk1.setSampleRate(sr); sk2.setSampleRate(sr); ld1.setSampleRate(sr); ld2.setSampleRate(sr); dt1.setSampleRate(sr); dt2.setSampleRate(sr); dg1.setSampleRate(sr); dg2.setSampleRate(sr);
        comb.setSampleRate(sr);
        comb2.setSampleRate(sr);
        phaser.setSampleRate(sr);
        vph1.setSampleRate(sr);
        vph2.setSampleRate(sr);
        fm1.setSampleRate(sr);
        fm2.setSampleRate(sr);
        phaser2.setSampleRate(sr);
        noiseHp.setSampleRate(sr); noiseLp.setSampleRate(sr);
        filterADSR.setSampleRate(sr);
        adsr.setSampleRate(sr);
        for (auto &e : modEnv) e.setSampleRate(sr);
        noteSmoother.setSampleRate(sr);
    }

    bool isActive() const  { return active; }
    float distWetMod() const { return modOff[MD_DIST_WET]; }            /* LFO / spare-envelope routes to the Distortion's Wet */
    float compWetMod() const { return modOff[MD_COMP_WET]; }            /* LFO / spare-envelope routes to the Comp's Wet */
    float utilOutMod(int rack) const { return modOff[MD_UTIL_OUT + rack]; }   /* rack 0 main, 1 and 2 the buses: the Level Out knob's offset */
    float distVMod(int i) const { return modOff[MD_DISTV_DRIVE + i]; }          /* 0..3: Drive, filter Cutoff, Res, Blend */
    float eqMod(int i) const { return modOff[MD_EQ1_FREQ + i]; }          /* 0..7: band 1 Freq, band 1 Gain, band 2 Freq ... */
    float fxfWetMod() const { return modOff[MD_FXF_WET]; }              /* LFO / spare-envelope routes to the Filter FX's Wet */
    float fxfCutoffMod() const { return modOff[MD_FXF_FREQ]; }          /* LFO / spare-envelope routes to the Filter FX cutoff, as a fraction of the filter's width */
    float filterEnvelope() const { return filterADSR.getOutput(); }       /* the Filter FX in the effects block follows it */
    bool isReleasing() const { return adsr.getState() == AnalogADSR::State::release; }
    int  currentNote() const { return midiNote; }
    uint32_t serialNumber() const { return serial; }
    float envOutput() const { return adsr.getOutput(); }

    /* Wavetable streams this voice is rendering now. */
    int streamCount() const
    {
        int n = 0;
        for (int o = 0; o < kOscs; o++)
            if (oscGain[o] > 0.0f) n += vp[o].voices;
        return active ? n : 0;
    }

    void start(const VoiceContext &c, int note, float vel, uint32_t serial_,
               float glideFromNote)
    {
        midiNote = note;
        velocity = vel;
        serial = serial_;
        active = true;
        fastKill = false;

        for (int o = 0; o < kOscs; o++) { osc[o].setWavetable(c.table[o]); modOsc[o].setWavetable(c.table[o]); }
        startSamples(c, note, std::clamp((int) std::lround(vel * 127.0f), 1, 127));

        const float *P = c.pots;
        const int glideMode = (int) P[TB_P_GLIDE_MODE];
        glissando  = glideMode == 1;
        portamento = glideMode == 2;
        if (glideFromNote >= 0.0f && (glissando || portamento)) {
            noteSmoother.setTime(glideSmootherTime(P, std::fabs((float) note - glideFromNote)));
            noteSmoother.setValueUnsmoothed(glideFromNote / 127.0f);
            noteSmoother.setValue((float) note / 127.0f);
        } else {
            noteSmoother.setValueUnsmoothed((float) note / 127.0f);
        }

        filter.reset();
        fltPrev = -1.0f;
        filter2.reset();
        fltPrev2 = -1.0f;
        vb1.reset(); vb2.reset(); sk1.reset(); sk2.reset(); ld1.reset(); ld2.reset(); dt1.reset(); dt2.reset(); dg1.reset(); dg2.reset();
        comb.reset();
        comb2.reset();
        phaser.reset();
        vph1.reset();
        vph2.reset();
        fm1.reset();
        fm2.reset();
        phaser2.reset();
        noiseHp.reset(); noiseLp.reset();
        noiseHpSet = noiseLpSet = 0.0f;
        filterADSR.reset();
        for (auto &e : modEnv) e.reset();

        updateParams(c, 0);

        /* oscillator phases: free-running (the old retrig-off default —
         * the retrig switch was cut from the surface) */
        static_assert(TB_P_WT2_RPH == TB_P_WT1_RPH + 1 && TB_P_WT3_RPH == TB_P_WT1_RPH + 2 && TB_P_WT1_PHS == TB_P_WT1_RPH + 3 && TB_P_WT3_PHS == TB_P_WT1_PHS + 2, "Osc Phase layout");
        /* Vital: a voice starts at random x Random Phase of a cycle plus the Phase offset; Random Phase 127 and Phase 0 are the free-running start above */
        for (int oi = 0; oi < kOscs; oi++) {
            float phases[kMaxUnison];
            const float rph = pot01(P[TB_P_WT1_RPH + oi]), phs = pot01(P[TB_P_WT1_PHS + oi]);
            for (auto &ph : phases) {
                ph = (float) rand() / (float) RAND_MAX * rph + phs;
                ph -= std::floor(ph);
            }
            osc[oi].noteOn(phases, kMaxUnison);
        }
        for (auto &m : modOsc) m.noteOn(0.0f);
        sub.noteOn(0.0f);
        noise.noteOn(0.0f);
        /* Serum's noise is mono with a random left/right level imbalance per note (2.3 dB on average, calibration/noise_stereo: correlation 1.000, side/mid -13..-29 dB) */
        noisePan = ((float) rand() / (float) RAND_MAX * 2.0f - 1.0f) * 0.27f;
        for (int l = 0; l < kLfos; l++)             /* Trig restarts at the top of the cycle, Free joins the running one */
            { lfoPhase[l] = lfoIsFree(P, l) ? c.lfoFree[l] : 0.0f; lfoSmInit[l] = false; lfoDelayLeft[l] = lfoDelaySec(P, l); lfoFadeAmp[l] = 0.0f; }
        resetRandoms(P);

        filterADSR.noteOn();
        for (auto &e : modEnv) e.noteOn();

        adsr.reset();
        adsr.noteOn();
    }

    /* Mono-mode retrigger without phase reset. */
    void retrigger(const VoiceContext &c, int note, float vel, bool glideNow = true)
    {
        const float *P = c.pots;
        midiNote = note;
        velocity = vel;
        startSamples(c, note, std::clamp((int) std::lround(vel * 127.0f), 1, 127));
        if ((glissando || portamento) && glideNow) {
            noteSmoother.setTime(glideSmootherTime(P, std::fabs((float) note - noteSmoother.getCurrentValue() * 127.0f)));
            noteSmoother.setValue((float) note / 127.0f);
        } else {
            noteSmoother.setValueUnsmoothed((float) note / 127.0f);
        }
        bool legato = P[TB_P_LEGATO] > 0.5f && !isReleasing();       /* a releasing voice has to be brought back in whatever the legato switch says */
        if (!legato) {              /* env retrig switches cut: always retrig */
            for (int l = 0; l < kLfos; l++)
                if (!lfoIsFree(P, l)) { lfoPhase[l] = 0.0f; lfoSmInit[l] = false; lfoDelayLeft[l] = lfoDelaySec(P, l); lfoFadeAmp[l] = 0.0f; }
            resetRandoms(P);
            levelAge = 0.0f;
            filterADSR.noteOn();
        for (auto &e : modEnv) e.noteOn();
            adsr.noteOn();
        }
    }

    /* A sample slot is either the SamplePlayer or, in its Granular kind, a GrainCloud on the same set; only one of the two is on. */
    void startSamples(const VoiceContext &c, int note, int vel)
    {
        const float *P = c.pots;
        const auto one = [&](SamplePlayer &sp, GrainCloud &gc, const std::shared_ptr<const SampleSet> &set, int kindParam) {
            if (P[kindParam] >= 0.5f) { sp.release(); gc.start(set, note, vel); }
            else { gc.release(); sp.start(set, note, vel); }
        };
        one(smp, gran, c.sample, TB_P_SMP_KIND);
        one(smp2, gran2, c.sample2, TB_P_SMP2_KIND);
        one(smp3, gran3, c.sample3, TB_P_SMP3_KIND);
    }
    void releaseSamples() { smp.release(); smp2.release(); smp3.release(); gran.release(); gran2.release(); gran3.release(); }

    void stop(bool allowTail)
    {
        adsr.noteOff();
        filterADSR.noteOff();
        for (auto &e : modEnv) e.noteOff();
        if (!allowTail) {
            active = false; releaseSamples();
        }
    }

    void kill()
    {
        fastKill = true;
        adsr.noteOff(); filterADSR.noteOff();
        for (auto &e : modEnv) e.noteOff();
    }

    void render(const VoiceContext &c, float *outL, float *outR, int n)
    {
        if (!active) return;

        /* Re-point at the context's tables every block: the loader may have
         * swapped them since noteStarted, and the block's shared_ptr is the
         * only thing keeping a table alive. A held raw pointer would dangle.
         * A swap mid-note lands as a waveform step at this block's first
         * sample — fade the block in from silence (2.9 ms) to mask it. */
        bool swapped = false;
        for (int o = 0; o < kOscs; o++) {
            swapped |= lastTable[o] && c.table[o] != lastTable[o];
            lastTable[o] = c.table[o];
            osc[o].setWavetable(c.table[o]);
            modOsc[o].setWavetable(c.table[o]);
        }

        updateParams(c, n);

        /* The block's buffers are cleared for the n samples used, not the whole kBlock: a short call (the demo and clip players split blocks at their notes)
         * would otherwise pay for clearing all of them every time. */
        const auto clr = [n](float *a) { std::memset(a, 0, sizeof(float) * (size_t) n); };
        float preL[kBlock], preR[kBlock];
        float pre2L[kBlock], pre2R[kBlock];                        /* the sources that go to Filter 2 */
        float postL[kBlock], postR[kBlock];
        clr(preL); clr(preR); clr(pre2L); clr(pre2R); clr(postL); clr(postR);

        const float *P = c.pots;

        /* Each source goes through the filter (pre) or straight to the amp (post: Serum's "direct" routing). Through the filters it has a
         * balance t from -1 (Filter 1 only) to +1 (Filter 2 only): Filter 1 gets (1 - t) / 2 of it and Filter 2 (1 + t) / 2, as Serum's
         * (calibration/dev/probe_filter2.py). Filter 2 only runs if some source reaches it. */
        bool usesF2 = false;
        float dryL[kBlock], dryR[kBlock];
        clr(dryL); clr(dryR);
        bool dryUsed = false;
        auto route = [&](bool direct, int balPot, auto &&renderTo) {
            if (direct) { renderTo(postL, postR); return; }
            const float t = std::clamp(P[balPot] / 63.5f - 1.0f, -1.0f, 1.0f);
            if (t <= -0.999f) { renderTo(preL, preR); return; }
            usesF2 = true;
            if (t >= 0.999f) { renderTo(pre2L, pre2R); return; }
            float tl[kBlock], tr[kBlock];
            clr(tl); clr(tr);
            renderTo(tl, tr);
            const float wa = 0.5f * (1.0f - t), wb = 0.5f * (1.0f + t);
            for (int i = 0; i < n; i++) {
                preL[i] += wa * tl[i];  preR[i] += wa * tr[i];
                pre2L[i] += wb * tl[i]; pre2R[i] += wb * tr[i];
            }
        };
        /* The FX buses: a source can also send to Bus 1 and Bus 2 (gain = pot/127, which the converter writes as Serum's level cubed), taken
         * before the filter and after the amp envelope (calibration/dev/probe_bus.py), and its route can be None, which leaves it on the buses alone,
         * or Out, which takes it past the effects as well. */
        float busL[2][kBlock], busR[2][kBlock];
        clr(busL[0]); clr(busL[1]); clr(busR[0]); clr(busR[1]);
        bool busUsed = false;
        auto addSource = [&](float routePot, int balPot, int sendPot, auto &&renderTo) {
            const bool none = routePot >= 1.5f && routePot < 2.5f, out = routePot >= 2.5f, direct = routePot >= 0.5f && routePot < 1.5f;
            const float s1 = c.bus[0] ? P[sendPot] * (1.0f / 127.0f) : 0.0f, s2 = c.bus[2] ? P[sendPot + 1] * (1.0f / 127.0f) : 0.0f;
            const bool sends = s1 > 0.0f || s2 > 0.0f;
            if (!sends) {
                if (out) { renderTo(dryL, dryR); dryUsed = true; }
                else if (!none) route(direct, balPot, renderTo);
                return;
            }
            float tl[kBlock], tr[kBlock];
            clr(tl); clr(tr);
            renderTo(tl, tr);
            auto addTo = [&](float *l, float *r) { for (int i = 0; i < n; i++) { l[i] += tl[i]; r[i] += tr[i]; } };
            if (out) { addTo(dryL, dryR); dryUsed = true; }
            else if (!none) route(direct, balPot, addTo);
            busUsed = true;
            for (int i = 0; i < n; i++) {
                busL[0][i] += s1 * tl[i]; busR[0][i] += s1 * tr[i];
                busL[1][i] += s2 * tl[i]; busR[1][i] += s2 * tr[i];
            }
        };
        static const int kOscPost[kOscs] = { TB_P_WT1_POST, TB_P_WT2_POST, TB_P_WT3_POST };
        static const int kOscBal[kOscs] = { TB_P_WT1_BAL, TB_P_WT2_BAL, TB_P_WT3_BAL };
        /* The FM / PD / RM / AM warps take the Sub or another oscillator as their modulator, at unit level whatever its own level (even silent). Each is rendered once
         * here (the oscillator as a single voice: its table, position, pitch, sync and bend, none of its own warps); the audible Sub, if any, is its signal scaled, so
         * the two stay in step. */
        float subSig[kBlock], oscSig[kOscs][kBlock];
        const float *srcs[5] = { nullptr, nullptr, nullptr, nullptr, nullptr };
        bool needSrc[5] = { false, false, false, false, false };
        for (int o = 0; o < kOscs; o++) {
            if (!(oscGain[o] > 0.0f)) continue;
            if (vp[o].xwarp >= 3 && vp[o].xwarp <= 6 && vp[o].xamount > 0.0f) needSrc[vp[o].xsrc] = true;
            if (vp[o].xwarp2 >= 3 && vp[o].xwarp2 <= 6 && vp[o].xamount2 > 0.0f) needSrc[vp[o].xsrc2] = true;
        }
        /* Warp Chain (Vital's oscillator-to-oscillator ring modulation): a modulator keeps its own FM / PD / RM / AM warps, so the sources it reads are needed too. */
        const bool chain = P[TB_P_XCHAIN] > 0.5f;
        if (chain)
            for (int pass = 0; pass < kOscs; pass++)
                for (int j = 0; j < kOscs; j++) {
                    if (!needSrc[j + 1]) continue;
                    if (vp[j].xwarp >= 3 && vp[j].xwarp <= 6 && vp[j].xamount > 0.0f) needSrc[vp[j].xsrc] = true;
                    if (vp[j].xwarp2 >= 3 && vp[j].xwarp2 <= 6 && vp[j].xamount2 > 0.0f) needSrc[vp[j].xsrc2] = true;
                }
        const bool subSigOn = needSrc[0];
        float noiseSig[kBlock];
        if (needSrc[4]) {                                          /* the Noise as a modulator: white, unit standard deviation, whatever the Noise level (Serum's noise warps work at level 0) */
            for (int i = 0; i < n; i++) noiseSig[i] = modNoise.next();
            srcs[4] = noiseSig;
        }
        if (subSigOn) {
            float unusedR[kBlock];
            clr(unusedR);
            for (int i = 0; i < n; i++) subSig[i] = 0.0f;
            StereoOscillator::Params unit = subParams;
            unit.leftGain = 1.0f; unit.rightGain = 0.0f;
            sub.processAdding(subNote, unit, subSig, unusedR, n);
            srcs[0] = subSig;
        }
        bool modDone[kOscs] = {};
        for (int pass = 0; pass < kOscs; pass++)                   /* with Warp Chain, a modulator waits for the modulators it reads (a cycle is cut on the last pass) */
            for (int j = 0; j < kOscs; j++) {
                if (!needSrc[j + 1] || modDone[j]) continue;
                VoicedWTParams m = vp[j];
                m.voices = 1; m.gain = 1.0f; m.pan = 0.0f; m.spread = 0.0f;
                if (!chain) m.xwarp = m.xwarp2 = 0;
                else {
                    const auto waits = [&](int w, float a, int s) { return w >= 3 && w <= 6 && a > 0.0f && s >= 1 && s - 1 != j && !modDone[s - 1] && pass < kOscs - 1; };
                    if (waits(m.xwarp, m.xamount, m.xsrc) || waits(m.xwarp2, m.xamount2, m.xsrc2)) continue;
                }
                float unusedR[kBlock];
                clr(unusedR);
                for (int i = 0; i < n; i++) oscSig[j][i] = 0.0f;
                modOsc[j].processAdding(oscNote[j], m, oscSig[j], unusedR, n, chain ? srcs : nullptr);
                srcs[j + 1] = oscSig[j];
                modDone[j] = true;
            }
        for (int o = 0; o < kOscs; o++)
            if (oscGain[o] > 0.0f)
                addSource(P[kOscPost[o]], kOscBal[o], TB_P_WT1_BUS1 + 2 * o, [&](float *l, float *r) { osc[o].processAdding(oscNote[o], vp[o], l, r, n, srcs); });
        if (subParams.leftGain + subParams.rightGain > 0.0f)
            addSource(P[TB_P_SUB_POST], TB_P_SUB_BAL, TB_P_SUB_BUS1, [&](float *l, float *r) {
                if (!subSigOn) { sub.processAdding(subNote, subParams, l, r, n); return; }
                for (int i = 0; i < n; i++) { l[i] += subSig[i] * subParams.leftGain; r[i] += subSig[i] * subParams.rightGain; }
            });
        if (noiseParams.leftGain + noiseParams.rightGain > 0.0f)
            addSource(P[TB_P_NOISE_POST], TB_P_NOISE_BAL, TB_P_NOISE_BUS1, [&](float *l, float *r) {
                if (noiseHpHz <= 0.0f && noiseLpHz <= 0.0f) { noise.processAdding(60.0f, noiseParams, l, r, n); return; }
                float tl[kBlock], tr[kBlock];                          /* the noise through its own colour filters first (Serum's Color: a 12 dB/octave low-pass and high-pass) */
                clr(tl); clr(tr);
                noise.processAdding(60.0f, noiseParams, tl, tr, n);
                if (noiseHpHz > 0.0f) noiseHp.process(tl, tr, n);
                if (noiseLpHz > 0.0f) noiseLp.process(tl, tr, n);
                for (int i = 0; i < n; i++) { l[i] += tl[i]; r[i] += tr[i]; }
            });
        /* A sample slot's oscillator Pan (Serum, measured with the wavetable and the granular oscillator alike): t = pan / 50 (hard left / right at +-50 percent),
         * L = (1 - t)(1 + 0.172 t), R = (1 + t)(1 - 0.172 t) relative to the centre; on a stereo file it is a balance (each channel keeps its own side's gain). */
        const auto smpPan = [&](float pan, float *l, float *r, const auto &src) {
            if (pan == 0.0f) { src(l, r); return; }
            const float t = std::clamp(pan / 50.0f, -1.0f, 1.0f), gl = (1.0f - t) * (1.0f + 0.172f * t), gr = (1.0f + t) * (1.0f - 0.172f * t);
            float tl[kBlock], tr[kBlock];
            clr(tl); clr(tr);
            src(tl, tr);
            for (int i = 0; i < n; i++) { l[i] += gl * tl[i]; r[i] += gr * tr[i]; }
        };
        const bool vitalLevel = P[TB_P_UNI_VITAL] >= 0.5f;
        const auto sampleKnob = [&](float base, float routed) {                      /* the level knob of a sample slot (squared by the caller): Vital's law above, or the 0..1 clamp it always had */
            return vitalLevel ? std::min(std::max(vitalLevelBase(base, levelAge) + routed, 0.0f), 4.0f) : std::clamp(base + routed, 0.0f, 1.0f);
        };
        const float knobL = sampleKnob(pot01(P[TB_P_SMP_LEVEL]), lfoLvl[MD_SMP1_LEVEL]);
        if (smp.on && knobL > 0.0f) {                  /* the sample oscillator: its level knob is squared, as the wavetables' are */
            const float knob = knobL;
            const bool fixedPitch = P[TB_P_SMP_FIXED] >= 0.5f;                 /* a sample that does not follow the keyboard (Serum's noise slot samples) */
            const float semis = (fixedPitch ? 0.0f : (float) (midiNote - smp.r->root) + c.pitchBendSemis + voicePitchMod()) + P[TB_P_SMP_TUNE] + P[TB_P_SMP_FINE] / 100.0f;
            addSource(P[TB_P_SMP_POST], TB_P_SMP_BAL, TB_P_SMP_BUS1, [&](float *l, float *r) { smpPan(P[TB_P_SMP_PAN], l, r, [&](float *a, float *b) { smp.processAdding(semis, knob * knob, (float) sampleRate, a, b, n, std::min((int) P[TB_P_SMP_UNI], (int) P[TB_P_UNI_CAP]), potDetune(P[TB_P_SMP_DET]), pot01(P[TB_P_SMP_SPREAD])); }); });
        }
        const float knob2L = sampleKnob(pot01(P[TB_P_SMP2_LEVEL]), lfoLvl[MD_SMP2_LEVEL]);
        if (smp2.on && knob2L > 0.0f) {                /* the second sample oscillator: the same, with its own controls */
            const float knob = knob2L;
            const bool fixedPitch = P[TB_P_SMP2_FIXED] >= 0.5f;
            const float semis = (fixedPitch ? 0.0f : (float) (midiNote - smp2.r->root) + c.pitchBendSemis + voicePitchMod()) + P[TB_P_SMP2_TUNE] + P[TB_P_SMP2_FINE] / 100.0f;
            addSource(P[TB_P_SMP2_POST], TB_P_SMP2_BAL, TB_P_SMP2_BUS1, [&](float *l, float *r) { smpPan(P[TB_P_SMP2_PAN], l, r, [&](float *a, float *b) { smp2.processAdding(semis, knob * knob, (float) sampleRate, a, b, n, std::min((int) P[TB_P_SMP2_UNI], (int) P[TB_P_UNI_CAP]), potDetune(P[TB_P_SMP2_DET]), pot01(P[TB_P_SMP2_SPREAD])); }); });
        }
        const float knob3L = sampleKnob(pot01(P[TB_P_SMP3_LEVEL]), lfoLvl[MD_SMP3_LEVEL]);
        if (smp3.on && knob3L > 0.0f) {                /* the third (Serum has three oscillators, any of them a sample) */
            const float knob = knob3L;
            const bool fixedPitch = P[TB_P_SMP3_FIXED] >= 0.5f;
            const float semis = (fixedPitch ? 0.0f : (float) (midiNote - smp3.r->root) + c.pitchBendSemis + voicePitchMod()) + P[TB_P_SMP3_TUNE] + P[TB_P_SMP3_FINE] / 100.0f;
            addSource(P[TB_P_SMP3_POST], TB_P_SMP3_BAL, TB_P_SMP3_BUS1, [&](float *l, float *r) { smpPan(P[TB_P_SMP3_PAN], l, r, [&](float *a, float *b) { smp3.processAdding(semis, knob * knob, (float) sampleRate, a, b, n, std::min((int) P[TB_P_SMP3_UNI], (int) P[TB_P_UNI_CAP]), potDetune(P[TB_P_SMP3_DET]), pot01(P[TB_P_SMP3_SPREAD])); }); });
        }

        /* The granular kind of each sample slot (granular.h): Density / Grain Length are knobs that a route adds to, Scan Rate is a percent (a route of a adds 4a). */
        const auto addGrains = [&](GrainCloud &gc, int levelP, int fixedP, int tuneP, int fineP, int postP, int balP, int busP, int densP, int lenP, int scanP, int startP, int winP, int winpP, int skewP, int mDens, int xb, int xc, int uniP, int detP, int spreadP, int mLvl, int panP) {
            const float knob = sampleKnob(pot01(P[levelP]), lfoLvl[mLvl]);
            if (!gc.on || !(knob > 0.0f)) return;
            const bool fixedPitch = P[fixedP] >= 0.5f;
            const float semis = (fixedPitch ? 0.0f : (float) (midiNote - gc.r->root) + c.pitchBendSemis + voicePitchMod()) + P[tuneP] + P[fineP] / 100.0f;
            GrainParams gp;
            gp.kd = std::clamp(pot01(P[densP]) + modOff[mDens], 0.0f, 1.0f);
            const float kl = std::clamp(pot01(P[lenP]) + modOff[mDens + 1], 0.0f, 1.0f);
            gp.lenSeconds = grainLengthSeconds(kl);
            gp.scanPct = (P[scanP] + 400.0f * modOff[mDens + 2]) * (P[xc] >= 0.5f ? 2.0f : 1.0f);          /* ScanRange 1: the scan runs at twice the speed (measured) */
            /* tempo (measured on Serum, docs/GRANULAR_PROBE_NOTES.md): Tempo lock scales the scan by host / base tempo; Tempo sync scans 0.0625 x 2^step of the file per second at
             * 120 BPM, scaled by the host tempo; the BPM modes snap the grain length to 1/128 s x 2^i (i = 0..13) and the birth rate to 2^m per second, both at 120 BPM and scaled by
             * the tempo, i and m stepping 14 times across the knob */
            const float hostBpm = c.bpm >= 20.0f ? std::clamp(c.bpm, 40.0f, 300.0f) : 120.0f;
            const int scm = (int) P[xc + 1];
            if (scm == 1) gp.scanPct *= hostBpm / std::max(P[xc + 2], 20.0f);
            else if (scm == 2) {
                const float fileSec = (float) gc.r->frames / (float) gc.r->rate;
                gp.scanPct = (P[scanP] < 0.0f ? -1.0f : 1.0f) * 100.0f * fileSec * 0.0625f * std::pow(2.0f, P[xc + 3]) * hostBpm / 120.0f;
            }
            if (P[xc + 4] >= 0.5f) {
                const int i = std::clamp((int) std::floor(14.0f * kl - 0.158f), 0, 13);
                gp.lenSeconds = 0.0078125f * (float) (1 << i) * 120.0f / hostBpm;
            }
            if (P[xc + 5] >= 0.5f) {
                const int m = std::clamp((int) std::floor(14.0f * gp.kd - 3.82f) - 2, -6, 8);
                gp.birthsPerSec = std::pow(2.0f, (float) m) * hostBpm / 120.0f;
            }
            gp.uni = std::min((int) P[uniP], (int) P[TB_P_UNI_CAP]);
            if ((int) P[TB_P_UNI_CAP] < 16) gp.nCap = 2.0f * (float) P[TB_P_UNI_CAP];          /* Unison Cap below 16 also thins a dense cloud: at most 2 grains at once per step of the cap */
            if (gp.uni > 1) { gp.uniDet = 4.0f * std::pow(pot01(P[detP]), 1.25f); gp.uniSpread = pot01(P[spreadP]); }          /* the granular detune: 4 d^1.25 semitones across the stack */
            gp.start = P[startP] * 0.01f;
            gp.winShape = (int) P[winP]; gp.winParam = P[winpP]; gp.winSkew = P[skewP];
            gp.loopMode = (int) P[xb]; gp.loopStart = P[xb + 1] * 0.01f; gp.loopEnd = P[xb + 2] * 0.01f; gp.end = P[xb + 3] * 0.01f;      /* xb: the slot's 12 params from _GLOOP (gen_params _gran2) */
            gp.manual = P[xb + 4] >= 0.5f; gp.position = P[xb + 5] * 0.01f;
            gp.rDir = P[xb + 6] * 0.01f; gp.rGain = P[xb + 7] * 0.01f; gp.rPitch = P[xb + 8]; gp.rOffset = P[xb + 9] * 0.01f; gp.rLen = P[xb + 10] * 0.01f; gp.rPan = P[xb + 11] * 0.01f;
            addSource(P[postP], balP, busP, [&](float *l, float *r) { smpPan(P[panP], l, r, [&](float *a, float *b) { gc.processAdding(gp, semis, knob * knob, (float) sampleRate, a, b, n); }); });
        };
        addGrains(gran, TB_P_SMP_LEVEL, TB_P_SMP_FIXED, TB_P_SMP_TUNE, TB_P_SMP_FINE, TB_P_SMP_POST, TB_P_SMP_BAL, TB_P_SMP_BUS1, TB_P_SMP_GDENS, TB_P_SMP_GLEN, TB_P_SMP_GSCAN, TB_P_SMP_GSTART, TB_P_SMP_GWIN, TB_P_SMP_GWINP, TB_P_SMP_GSKEW, MD_SMP1_GDENS, TB_P_SMP_GLOOP, TB_P_SMP_GSCX, TB_P_SMP_UNI, TB_P_SMP_DET, TB_P_SMP_SPREAD, MD_SMP1_LEVEL, TB_P_SMP_PAN);
        addGrains(gran2, TB_P_SMP2_LEVEL, TB_P_SMP2_FIXED, TB_P_SMP2_TUNE, TB_P_SMP2_FINE, TB_P_SMP2_POST, TB_P_SMP2_BAL, TB_P_SMP2_BUS1, TB_P_SMP2_GDENS, TB_P_SMP2_GLEN, TB_P_SMP2_GSCAN, TB_P_SMP2_GSTART, TB_P_SMP2_GWIN, TB_P_SMP2_GWINP, TB_P_SMP2_GSKEW, MD_SMP2_GDENS, TB_P_SMP2_GLOOP, TB_P_SMP2_GSCX, TB_P_SMP2_UNI, TB_P_SMP2_DET, TB_P_SMP2_SPREAD, MD_SMP2_LEVEL, TB_P_SMP2_PAN);
        addGrains(gran3, TB_P_SMP3_LEVEL, TB_P_SMP3_FIXED, TB_P_SMP3_TUNE, TB_P_SMP3_FINE, TB_P_SMP3_POST, TB_P_SMP3_BAL, TB_P_SMP3_BUS1, TB_P_SMP3_GDENS, TB_P_SMP3_GLEN, TB_P_SMP3_GSCAN, TB_P_SMP3_GSTART, TB_P_SMP3_GWIN, TB_P_SMP3_GWINP, TB_P_SMP3_GSKEW, MD_SMP3_GDENS, TB_P_SMP3_GLOOP, TB_P_SMP3_GSCX, TB_P_SMP3_UNI, TB_P_SMP3_DET, TB_P_SMP3_SPREAD, MD_SMP3_LEVEL, TB_P_SMP3_PAN);
        static_assert(TB_P_SMP_GDENM == TB_P_SMP_GSCX + 5 && TB_P_SMP2_GDENM == TB_P_SMP2_GSCX + 5 && TB_P_SMP3_GDENM == TB_P_SMP3_GSCX + 5, "the granular tempo params of a slot are contiguous");
        static_assert(TB_P_SMP_GRPAN == TB_P_SMP_GLOOP + 11 && TB_P_SMP2_GRPAN == TB_P_SMP2_GLOOP + 11 && TB_P_SMP3_GRPAN == TB_P_SMP3_GLOOP + 11, "the granular scan / loop / random params of a slot are contiguous");

        /* velocity */
        float vgain = (P[TB_P_VCA_VLAW] >= 0.5f ? velocityToGainSerum(velocity, ampVelTrack) : velocityToGain(velocity, ampVelTrack)) * ampModGain;
        for (int i = 0; i < n; i++) {
            preL[i] *= vgain;  preR[i] *= vgain;
            pre2L[i] *= vgain; pre2R[i] *= vgain;
            postL[i] *= vgain; postR[i] *= vgain;
            dryL[i] *= vgain;  dryR[i] *= vgain;
        }

        /* A voice filter's output: to the main path (Master), to the buses alone (None) or past the effects (Out), and a send to each bus taken after the filter
         * (Serum's routing slots 5 and 6). The velocity was applied to the sources, so a send takes it off again: sends do not follow it. */
        const float busGain = P[TB_P_VCA_VLAW] >= 0.5f ? velocityToGainSerum(velocity, 0.0f) : velocityToGain(velocity, ampVelTrack);
        auto filterOut = [&](const float *l, const float *r, int outPot, int sendPot) {
            const int dest = (int) P[outPot];
            for (int i = 0; i < n; i++) {
                if (dest == 0) { postL[i] += l[i]; postR[i] += r[i]; }
                else if (dest == 2) { dryL[i] += l[i]; dryR[i] += r[i]; }
            }
            if (dest == 2) dryUsed = true;
            const float rel = vgain > 1e-6f ? 1.0f / vgain : 0.0f;
            for (int b = 0; b < 2; b++) {
                const float s = c.bus[2 * b] ? P[sendPot + b] * (1.0f / 127.0f) * rel : 0.0f;
                if (s <= 0.0f) continue;
                busUsed = true;
                for (int i = 0; i < n; i++) { busL[b][i] += s * l[i]; busR[b][i] += s * r[i]; }
            }
        };

        /* Filter drive: saturate going in, so the harmonics are what the filter then
         * shapes, and again coming out, so a resonant peak rounds off instead of
         * ringing past everything. The gain ramps across the block because a knob
         * step would otherwise be a level step. */
        /* Filter 2 in series into Filter 1 (Serum's routing slot 6 sent to the other filter): Filter 2 runs first and its output is added to Filter 1's input. */
        const bool f2First = (int) P[TB_P_FLT2_OUT] == 3;
        const bool vDriveLaw = P[TB_P_FLT_VDL] >= 0.5f;
        float dryAL[kBlock], dryAR[kBlock], dryBL[kBlock], dryBR[kBlock];        /* a voice filter's input, kept when its Mix (Vital's, hidden) is below 100 % */
        const float mixA = P[TB_P_FLT1_MIX] * (1.0f / 127.0f), mixB = P[TB_P_FLT2_MIX] * (1.0f / 127.0f);
        auto runF2 = [&]() {
            const bool mixing2 = mixB < 0.999f;
            if (mixing2) for (int i = 0; i < n; i++) { dryBL[i] = pre2L[i]; dryBR[i] = pre2R[i]; }
            const bool plain2 = !(fmOn2 || vph2.on || combType2);                                                   /* Vital's analog filter takes 1 / (1 + 2 res) off the passband whatever its drive; a comb, phaser or formant filter does not */
            const bool driving2 = !sk2.on && !ld2.on && !dt2.on && !dg2.on && (drive2Mix > 0.0f || drive2MixTarget > 0.0f || (vDriveLaw && plain2 && P[TB_P_FLT2_RES] > 0.0f));
            if (driving2) { if (vDriveLaw) { const float a2 = 1.0f / (1.0f + 2.0f * pot01(P[TB_P_FLT2_RES])); vitalFilterDriveIn(pre2L, pre2R, n, drive2Gain, drive2GainTarget, a2, plain2 ? a2 : 1.0f); } else driveIn(pre2L, pre2R, n, drive2Gain, drive2GainTarget, drive2Mix, drive2MixTarget); }
            if (sk2.on) sk2.process(pre2L, pre2R, n);
            else if (ld2.on) ld2.process(pre2L, pre2R, n);
            else if (dt2.on) dt2.process(pre2L, pre2R, n);
            else if (dg2.on) dg2.process(pre2L, pre2R, n);
            else if (fmOn2) fm2.process(pre2L, pre2R, n, fmSet2);
            else if (vph2.on) vph2.process(pre2L, pre2R, n, fltTarget2, combRes2, vph2Blend, vph2Inv, vphClean);
            else if (combType2 >= 5) phaser2.process(pre2L, pre2R, n, 2 * (combType2 - 4), combRes2, fltTarget2);
            else if (combType2 && vcMode2) comb2.processVital(pre2L, pre2R, n, combType2 <= 2 ? 0 : combType2 - 2, combRes2, fltTarget2, vcBlend2, vcTrans2, vcMode2 == 2);
            else if (combType2) comb2.process(pre2L, pre2R, n, combType2 >= 3, (combType2 & 1) ? 1 : -1, combRes2, fltTarget2);
            else if (vb2.on) runBlend(vb2, fltTarget2, fltQ2, pre2L, pre2R, n);
            else runFilter(filter2, fltPrev2, fltTarget2, fltQ2, pre2L, pre2R, n);
            if (driving2 && !vDriveLaw) driveOut(pre2L, pre2R, n, drive2Mix, drive2MixTarget);
            if (P[TB_P_FLT2_GAIN] > 0.0f) {
                const float g = std::pow(10.0f, P[TB_P_FLT2_GAIN] / 127.0f * 24.0f / 20.0f);
                for (int i = 0; i < n; i++) { pre2L[i] *= g; pre2R[i] *= g; }
            }
            if (mixing2) for (int i = 0; i < n; i++) { pre2L[i] = dryBL[i] + (pre2L[i] - dryBL[i]) * mixB; pre2R[i] = dryBR[i] + (pre2R[i] - dryBR[i]) * mixB; }
        };
        if (f2First) {
            usesF2 = true;
            runF2();
            for (int i = 0; i < n; i++) { preL[i] += pre2L[i]; preR[i] += pre2R[i]; }
        }
        const bool mixing = mixA < 0.999f;
        if (mixing) for (int i = 0; i < n; i++) { dryAL[i] = preL[i]; dryAR[i] = preR[i]; }
        const bool plain1 = !(fmOn1 || vph1.on || combType);
        const bool driving = !sk1.on && !ld1.on && !dt1.on && !dg1.on && (driveMix > 0.0f || driveMixTarget > 0.0f || (vDriveLaw && plain1 && P[TB_P_FLT_RES] > 0.0f));
        if (driving) { if (vDriveLaw) { const float a1 = 1.0f / (1.0f + 2.0f * pot01(P[TB_P_FLT_RES])); vitalFilterDriveIn(preL, preR, n, driveGain, driveGainTarget, a1, plain1 ? a1 : 1.0f); } else driveIn(preL, preR, n, driveGain, driveGainTarget, driveMix, driveMixTarget); }
        if (sk1.on) sk1.process(preL, preR, n);
        else if (ld1.on) ld1.process(preL, preR, n);
        else if (dt1.on) dt1.process(preL, preR, n);
        else if (dg1.on) dg1.process(preL, preR, n);
        else if (fmOn1) fm1.process(preL, preR, n, fmSet1);
        else if (vph1.on) vph1.process(preL, preR, n, fltTarget, combRes, vph1Blend, vph1Inv, vphClean);
        else if (combType >= 5) phaser.process(preL, preR, n, 2 * (combType - 4), combRes, fltTarget);
        else if (combType && vcMode) comb.processVital(preL, preR, n, combType <= 2 ? 0 : combType - 2, combRes, fltTarget, vcBlend, vcTrans, vcMode == 2);
        else if (combType) comb.process(preL, preR, n, combType >= 3, (combType & 1) ? 1 : -1, combRes, fltTarget);
        else if (vb1.on) runBlend(vb1, fltTarget, fltQ, preL, preR, n);
        else runFilter(filter, fltPrev, fltTarget, fltQ, preL, preR, n);
        if (driving && !vDriveLaw) driveOut(preL, preR, n, driveMix, driveMixTarget);
        if (P[TB_P_FLT_GAIN] > 0.0f) {                                  /* Serum's filter Drive as the gain it is */
            const float g = std::pow(10.0f, P[TB_P_FLT_GAIN] / 127.0f * 24.0f / 20.0f);
            for (int i = 0; i < n; i++) { preL[i] *= g; preR[i] *= g; }
        }
        if (mixing) for (int i = 0; i < n; i++) { preL[i] = dryAL[i] + (preL[i] - dryAL[i]) * mixA; preR[i] = dryAR[i] + (preR[i] - dryAR[i]) * mixA; }
        driveGain = driveGainTarget;
        driveMix = driveMixTarget;

        if ((int) P[TB_P_FLT1_OUT] == 3 && !f2First) {             /* Filter 1 in series into Filter 2 */
            usesF2 = true;
            for (int i = 0; i < n; i++) { pre2L[i] += preL[i]; pre2R[i] += preR[i]; }
        }
        if (usesF2) {
            if (!f2First) runF2();
            filterOut(pre2L, pre2R, TB_P_FLT2_OUT, TB_P_FLT2_BUS1);
        } else {
            fltPrev2 = -1.0f;                                       /* a filter that was not run restarts from its target */
        }
        drive2Gain = drive2GainTarget;
        drive2Mix = drive2MixTarget;

        filterOut(preL, preR, TB_P_FLT1_OUT, TB_P_FLT1_BUS1);

        if (busUsed || dryUsed) {                                   /* the amp envelope on the main path, the sends and the Out sources alike; a send does not follow the velocity */
            for (int i = 0; i < n; i++) {
                adsr.process();
                const float g = adsr.getOutput();
                postL[i] *= g; postR[i] *= g;
                dryL[i] *= g; dryR[i] *= g;
                for (int b = 0; b < 2; b++) { busL[b][i] *= g * busGain; busR[b][i] *= g * busGain; }
            }
            for (int b = 0; b < 2; b++)
                if (c.bus[2 * b])
                    for (int i = 0; i < n; i++) { c.bus[2 * b][i] += busL[b][i]; c.bus[2 * b + 1][i] += busR[b][i]; }
            if (c.dry[0])
                for (int i = 0; i < n; i++) { c.dry[0][i] += dryL[i]; c.dry[1][i] += dryR[i]; }
        } else {
            adsr.processMultiplying(postL, postR, n);
        }
        if (swapped) {
            for (int i = 0; i < n; i++) {
                float g = (float) i / (float) n;
                postL[i] *= g;
                postR[i] *= g;
            }
        }

        if (adsr.getState() == AnalogADSR::State::idle) {
            active = false; releaseSamples();
        }

        for (int i = 0; i < n; i++) {
            outL[i] += postL[i];
            outR[i] += postR[i];
        }

        noteSmoother.process(n);
    }

private:
        void updateParams(const VoiceContext &c, int blockSize)
    {
        const float *P = c.pots;
        if (blockSize == 0) levelAge = 0.0f; else levelAge += (float) blockSize / (float) sampleRate;       /* seconds since the note began, for Vital's level glide */

        /* ---- envelopes ---- */
        /* The Decay macro moves both decays by the same number of pot steps (64 is neutral): a step is
         * the same ratio of time on either envelope, so the two stay in proportion. */
        const float dOff = P[TB_P_DECAY] - 64.0f;
        auto decayPot = [dOff](float v) { return std::min(std::max(v + dOff, 0.0f), 127.0f); };
        filterADSR.setDelay(potEnvStage(P[TB_P_FLT_DELAY]));
        filterADSR.setHold(potEnvStage(P[TB_P_FLT_HOLD]));
        filterADSR.setAttack(potAttackTime(P[TB_P_FLT_A]));
        filterADSR.setAttackPower(attackPower(P[TB_P_FLT_APOW]), P[TB_P_FLT_APOW] >= 0.5f, false);
        filterADSR.setDecay(potEnvTime(decayPot(P[TB_P_FLT_D])));
        filterADSR.setDecayPower(P[TB_P_FLT_DPOW] >= 0.5f ? (P[TB_P_FLT_DPOW] - 64.0f) * (20.0f / 63.0f) : 0.0f, false);      /* Vital's decay curve on the filter envelope (0 keeps the analog fall) */
        filterADSR.setSustainLevel(pot01(P[TB_P_FLT_S]));
        filterADSR.setRelease(potEnvTime(P[TB_P_FLT_R]));
        filterADSR.setControlRate(P[TB_P_UNI_VITAL] >= 0.5f);
        if (blockSize > 0) { if (P[TB_P_UNI_VITAL] >= 0.5f) filterADSR.processControl(blockSize); else filterADSR.process(blockSize); }

        adsr.setDelay(potEnvStage(P[TB_P_VCA_DELAY]));
        adsr.setHold(potEnvStage(P[TB_P_VCA_HOLD]));
        adsr.setAttack(potAttackTime(P[TB_P_VCA_A]));
        adsr.setAttackPower(attackPower(P[TB_P_VCA_APOW]), P[TB_P_VCA_APOW] >= 0.5f, true);
        adsr.setDecay(potEnvTime(decayPot(P[TB_P_VCA_D])));
        adsr.setDecayPower(P[TB_P_VCA_DPOW] >= 0.5f ? (P[TB_P_VCA_DPOW] - 64.0f) * (20.0f / 63.0f) : 0.0f);
        adsr.setSustainLevel(pot01(P[TB_P_VCA_S]));
        adsr.setRelease(fastKill ? 0.01f : potEnvTime(P[TB_P_VCA_R]));

        /* ---- the two spare envelopes, and where they point ----
         * Each contributes env * amount to ONE destination. Amount is bipolar
         * so a sweep can fall as well as rise, which is the shape that makes a
         * wavetable read as a struck instrument. */
        for (int i = 0; i < MD_COUNT; i++) modOff[i] = lfoLvl[i] = 0.0f;
        {
            static const struct { int a, d, s, r, dst, amt, apow, delay, hold, dpow; } k[kModEnvs] = {
                { TB_P_ME1_A, TB_P_ME1_D, TB_P_ME1_S, TB_P_ME1_R, TB_P_ME1_DST, TB_P_ME1_AMT, TB_P_ME1_APOW, TB_P_ME1_DELAY, TB_P_ME1_HOLD, TB_P_ME1_DPOW },
                { TB_P_ME2_A, TB_P_ME2_D, TB_P_ME2_S, TB_P_ME2_R, TB_P_ME2_DST, TB_P_ME2_AMT, TB_P_ME2_APOW, TB_P_ME2_DELAY, TB_P_ME2_HOLD, TB_P_ME2_DPOW },
                { TB_P_ME3_A, TB_P_ME3_D, TB_P_ME3_S, TB_P_ME3_R, TB_P_ME3_DST, TB_P_ME3_AMT, TB_P_ME3_APOW, TB_P_ME3_DELAY, TB_P_ME3_HOLD, TB_P_ME3_DPOW },
                { TB_P_ME4_A, TB_P_ME4_D, TB_P_ME4_S, TB_P_ME4_R, TB_P_ME4_DST, TB_P_ME4_AMT, TB_P_ME4_APOW, TB_P_ME4_DELAY, TB_P_ME4_HOLD, TB_P_ME4_DPOW },
            };
            for (int i = 0; i < kModEnvs; i++) {
                if (i >= 2 && (int) P[k[i].dst] == MD_NONE) continue;           /* the hidden envelopes cost nothing unless a preset points them somewhere */
                modEnv[i].setDelay(potEnvStage(P[k[i].delay]));
                modEnv[i].setHold(potEnvStage(P[k[i].hold]));
                modEnv[i].setAttack(potAttackTime(P[k[i].a]));
                modEnv[i].setAttackPower(attackPower(P[k[i].apow]), P[k[i].apow] >= 0.5f, false);
                modEnv[i].setDecay(potEnvTime(P[k[i].d]));
                modEnv[i].setDecayPower(P[k[i].dpow] >= 0.5f ? (P[k[i].dpow] - 64.0f) * (20.0f / 63.0f) : 0.0f, false);
                modEnv[i].setSustainLevel(pot01(P[k[i].s]));
                modEnv[i].setRelease(potEnvTime(P[k[i].r]));
                modEnv[i].setControlRate(P[TB_P_UNI_VITAL] >= 0.5f);
                if (blockSize > 0) { if (P[TB_P_UNI_VITAL] >= 0.5f) modEnv[i].processControl(blockSize); else modEnv[i].process(blockSize); }
                const int dst = (int) P[k[i].dst];
                if (dst > MD_NONE && dst < MD_COUNT) {
                    const float e = modEnv[i].getOutput() * potBipolar(P[k[i].amt]);
                    /* Serum adds an envelope route to the oscillator volume KNOB and the knob's taper (gain = knob^2) comes after, as for the LFOs:
                     * from volume 0 a route of depth a gives gain a^2, not a (calibration/dev/probe_noise_route.py: noise slot and WT oscillator, 12 dB per halving of the depth) */
                    if (isLevelDst(dst)) lfoLvl[dst] += e;
                    else modOff[dst] += e;
                }
            }
        }

        /* ---- the LFOs, and the routes that carry them to the same destinations ----
         * Each runs at the block rate. A route adds lfo * amount to its destination: Uni with the LFO swinging 0..1, Bi with it
         * swinging -1..+1 (the polarity is the route's own, as Serum stores it). */
        {
            float lfoOut[kRouteSrcs] = {};
            bool srcUsed[kRouteSrcs + 1] = {};        /* LFO 5..8 only run when a route takes them (the first four always did) */
            for (int r = 0; r < kLfoRoutes; r++)
                if ((int) P[lrSrc(r) + 1] > MD_NONE) { const int sv = (int) P[lrSrc(r)]; if (sv >= 1 && sv <= kRouteSrcs) srcUsed[sv] = true; }
            for (int l = 0; l < kLfos; l++) {
                if (l >= kLfosA && !srcUsed[lfoSlot(l) + 1]) continue;
                const int ol = lfoSlot(l);
                if (lfoIsKey(P, l)) { lfoOut[ol] = lfoKeyValue(P, l, noteSmoother.getCurrentValue() * 127.0f); lfoSmInit[l] = false; continue; }
                /* Vital's Delay holds the LFO where it started until the time is up; the time that is left over in this block moves it (synth_lfo.cpp processControlRate) */
                const float full = blockSize > 0 ? (float) blockSize / (float) sampleRate : 0.0f;
                float adv = full;
                if (lfoDelayLeft[l] > 0.0f) { const float used = std::min(adv, lfoDelayLeft[l]); lfoDelayLeft[l] -= used; adv -= used; }
                if (blockSize > 0) {
                    /* with no Delay running the step is written the way it always was, so Serum presets stay bit-identical */
                    lfoPhase[l] += adv == full ? lfoHz(P, l, c.bpm) * (float) blockSize / (float) sampleRate : lfoHz(P, l, c.bpm) * adv;
                    if (lfoIsOneShot(P, l)) lfoPhase[l] = std::min(lfoPhase[l], 1.0f);        /* Env: plays once, then holds its end */
                    else lfoPhase[l] -= std::floor(lfoPhase[l]);
                }
                const float phOff = lfoPhaseOff(P, l);
                float ph = lfoPhase[l] + phOff;
                if (phOff > 0.0f) ph = lfoIsOneShot(P, l) ? std::min(ph, 1.0f) : ph - std::floor(ph);
                lfoOut[ol] = lfoShapeAt(P, l, ph, lfoIsOneShot(P, l));                             // 0..1
                const float tau = potLfoSmoothTau(P[lfoKeys(l).smooth]);
                const float fadeSec = lfoFadeSec(P, l);
                if (fadeSec > 0.0f && tau <= 0.0f) {                 /* Fade In blends from the LFO's start value to its running value (Vital ignores it while the LFO is smoothed) */
                    lfoFadeAmp[l] = std::min(1.0f, lfoFadeAmp[l] + adv / std::max(adv, fadeSec));
                    const float start = lfoShapeAt(P, l, phOff, lfoIsOneShot(P, l));
                    lfoOut[ol] = start + (lfoOut[ol] - start) * lfoFadeAmp[l];
                }
                if (tau > 0.0f) {
                    /* Serum's smoother starts at the LFO's first value (measured), then follows it with the block-rate one-pole. Vital's starts at 0, climbs to the value, and stands still while a Delay runs
                     * (its time step is the time since the delay ended) */
                    const bool vital = P[TB_P_LFO_VLAW] >= 0.5f, first = !lfoSmInit[l];
                    if (first) { lfoSm[l] = vital ? 0.0f : lfoOut[ol]; lfoSmInit[l] = true; }
                    if ((vital || !first) && adv > 0.0f)
                        lfoSm[l] += (1.0f - std::exp(adv == full ? -(float) blockSize / ((float) sampleRate * tau) : -adv / tau)) * (lfoOut[ol] - lfoSm[l]);
                    lfoOut[ol] = lfoSm[l];
                } else lfoSmInit[l] = false;
            }
            stepRandoms(P, c.bpm, blockSize, lfoOut + kLfosA);
            lfoOut[kSrcVel - 1] = std::clamp(velocity, 0.0f, 1.0f);              /* Velocity: the note's, 0..1, fixed for the note */
            lfoOut[kSrcAft - 1] = std::clamp(c.aftertouch, 0.0f, 1.0f);     /* Aftertouch: the channel pressure now (0 when the pads do not send it) */
            for (int r = 0; r < kLfoRoutes; r++) {
                const int rb = lrSrc(r);
                const int src = (int) P[rb];
                const int dst = (int) P[rb + 1];
                if (src >= 1 && src <= kRouteSrcs && dst > MD_NONE && dst < MD_COUNT) {
                    const float u = lfoOut[src - 1];
                    const float v = P[rb + 3] >= 1.0f ? 2.0f * u - 1.0f : u;
                    const float amt = v * potBipolar(P[rb + 2]);
                    /* Serum moves the volume KNOB, and the knob's taper comes after: a level route is added to the knob
                     * position here and squared with it below. The spare envelopes' level routes are added to the knob
                     * the same way (above). */
                    if (isLevelDst(dst))
                        lfoLvl[dst] += amt;
                    else if (dst == MD_WT1_TUNE || dst == MD_WT2_TUNE)
                        /* Serum's pitch routes run from a fraction of a semitone (vibrato) to octaves: the amount is squared, so the
                         * pot's fine steps are fine and its end is 4 x 12 = 48 semitones */
                        modOff[dst] += std::copysign(amt * amt, amt) * 4.0f;
                    else
                        modOff[dst] += amt;
                }
            }
        }

        /* ---- oscillators ---- */
        float baseNote = noteSmoother.getCurrentValue() * 127.0f;
        if (glissando) baseNote = (float) (int) (baseNote + 0.5f);
        baseNote += c.pitchBendSemis + voicePitchMod();                       /* Vital's voice transpose / tune routes move the whole voice */

        /* WT3 is the plain one: no bend or formant pots, and only position,
         * level and sync can be modulated. MD_NONE's offset is always zero,
         * so it stands in for a destination the oscillator does not have. */
        static const struct OscKeys {
            int pos, level, tune, uni, det, spread, pan, sync, bend, form;
            int dPos, dLvl, dTun, dBnd, dFrm, dPan, dSync, dPit, dXa;
            int shape, sgain, sout, xw, xa, xs, xw2, xa2, xs2;
        } kOsc[kOscs] = {
            { TB_P_WT1_POS, TB_P_WT1_LEVEL, TB_P_WT1_TUNE, TB_P_WT1_UNI, TB_P_WT1_DETUNE,
              TB_P_WT1_SPREAD, TB_P_WT1_PAN, TB_P_WT1_SYNC, TB_P_WT1_BEND, TB_P_WT1_FORMANT,
              MD_WT1_POS, MD_WT1_LEVEL, MD_WT1_TUNE, MD_WT1_BEND, MD_WT1_FORMANT, MD_WT1_PAN,
              MD_WT1_SYNC, MD_WT1_PITCH, MD_WT1_WARP, TB_P_WT1_WSHAPE, TB_P_WT1_SGAIN, TB_P_WT1_SOUT, TB_P_WT1_XW, TB_P_WT1_XA, TB_P_WT1_XS, TB_P_WT1_XW2, TB_P_WT1_XA2, TB_P_WT1_XS2 },
            { TB_P_WT2_POS, TB_P_WT2_LEVEL, TB_P_WT2_TUNE, TB_P_WT2_UNI, TB_P_WT2_DETUNE,
              TB_P_WT2_SPREAD, TB_P_WT2_PAN, TB_P_WT2_SYNC, TB_P_WT2_BEND, TB_P_WT2_FORMANT,
              MD_WT2_POS, MD_WT2_LEVEL, MD_WT2_TUNE, MD_WT2_BEND, MD_WT2_FORMANT, MD_WT2_PAN,
              MD_WT2_SYNC, MD_WT2_PITCH, MD_WT2_WARP, TB_P_WT2_WSHAPE, TB_P_WT2_SGAIN, TB_P_WT2_SOUT, TB_P_WT2_XW, TB_P_WT2_XA, TB_P_WT2_XS, TB_P_WT2_XW2, TB_P_WT2_XA2, TB_P_WT2_XS2 },
            { TB_P_WT3_POS, TB_P_WT3_LEVEL, TB_P_WT3_TUNE, TB_P_WT3_UNI, TB_P_WT3_DETUNE,
              TB_P_WT3_SPREAD, TB_P_WT3_PAN, TB_P_WT3_SYNC, -1, -1,
              MD_WT3_POS, MD_WT3_LEVEL, MD_NONE, MD_NONE, MD_NONE, MD_NONE, MD_WT3_SYNC, MD_WT3_PITCH, MD_WT3_WARP, TB_P_WT3_WSHAPE, TB_P_WT3_SGAIN, TB_P_WT3_SOUT, TB_P_WT3_XW, TB_P_WT3_XA, TB_P_WT3_XS, TB_P_WT3_XW2, TB_P_WT3_XA2, TB_P_WT3_XS2 },
        };

        static const int kUStack[kOscs] = { TB_P_WT1_USTACK, TB_P_WT2_USTACK, TB_P_WT3_USTACK }, kUPow[kOscs] = { TB_P_WT1_UPOW, TB_P_WT2_UPOW, TB_P_WT3_UPOW }, kUBlend[kOscs] = { TB_P_WT1_UBLEND, TB_P_WT2_UBLEND, TB_P_WT3_UBLEND };
        int unison[kOscs];
        for (int o = 0; o < kOscs; o++) {
            const OscKeys &k = kOsc[o];
            VoicedWTParams &v = vp[o];

            /* tune modulation reads as an octave at full amount */
            static const int kFixed[kOscs] = { TB_P_WT1_FIXED, TB_P_WT2_FIXED, TB_P_WT3_FIXED };
            /* a fixed oscillator ignores the key (Serum's Pitch Track off): its pitch is MIDI note 0 plus the tune (measured: DR - Kick Minimal) */
            oscNote[o] = (P[kFixed[o]] >= 0.5f ? 0.0f : baseNote) + P[k.tune] + modOff[k.dTun] * 12.0f + modOff[k.dPit] * 48.0f + modOff[MD_WT1_FINE + o] * 2.0f;

            v.position = std::clamp(pot01(P[k.pos]) + modOff[k.dPos], 0.0f, 1.0f);
            {
                if (P[TB_P_UNI_VITAL] >= 0.5f) {
                    const float knob = std::max(vitalLevelBase(pot01(P[k.level]), levelAge) + lfoLvl[k.dLvl], 0.0f);
                    v.gain = std::clamp(knob * knob + modOff[k.dLvl], 0.0f, 16.0f);
                } else {
                    const float knob = std::clamp(pot01(P[k.level]) + lfoLvl[k.dLvl], 0.0f, 1.0f);
                    v.gain = std::clamp(knob * knob + modOff[k.dLvl], 0.0f, 1.0f);
                }
            }
            v.pan      = std::clamp(potBipolar(P[k.pan]) + modOff[k.dPan], -1.0f, 1.0f);
            v.vitalLaw = P[TB_P_UNI_VITAL] >= 0.5f;                    /* Vital's unison: the Detune knob is the stack's half-width, 24 * (pot / 127)^2 semitones, and the stack style applies */
            v.detune   = v.vitalLaw ? 48.0f * pot01(P[k.det]) * pot01(P[k.det]) : potDetune(P[k.det]);
            v.detPower = P[kUPow[o]];
            v.blend = std::clamp(P[kUBlend[o]] * 0.01f, 0.0f, 1.0f);
            v.voicesWanted = std::clamp((int) P[k.uni], 1, 16);
            v.stackStyle = v.vitalLaw ? std::clamp((int) P[kUStack[o]], 0, 10) : 0;
            v.spread   = pot01(P[k.spread]);
            v.sync     = std::clamp(pot01(P[k.sync]) + modOff[k.dSync], 0.0f, 1.0f);
            v.bend     = k.bend < 0 ? 0.0f
                       : std::clamp(potBipolar(P[k.bend]) + modOff[k.dBnd], -1.0f, 1.0f);
            v.formant  = k.form < 0 ? 0.0f
                       : std::clamp(potBipolar(P[k.form]) + modOff[k.dFrm], -1.0f, 1.0f);
            /* a Dist* oscillator warp: the shaper's pre-gain 0.02..300 and output gain 0.5..60, both log over the 0..127 pot */
            v.shape = std::clamp((int) P[k.shape], 0, 5);
            if (v.shape >= 2 && v.shape != 5) {                        /* the pre-gain and output gain are read only by the Dist shapers 2..4 (two pow calls a voice per block otherwise) */
                v.shapeGain = 0.02f * std::pow(15000.0f, P[k.sgain] / 127.0f);
                v.shapeOut = 0.5f * std::pow(120.0f, P[k.sout] / 127.0f);
            }
            /* shape 5: a measured Serum waveshaper (warp_curves.h) - Gain is its index, Out its amount over 0..127 */
            v.curveMode = std::clamp((int) P[k.sgain], 0, 63);
            v.curveAmt = std::clamp(P[k.sout] / 127.0f, 0.0f, 1.0f);
            v.xwarp = std::clamp((int) P[k.xw], 0, 7);                   /* Serum's filter / FM / PD / RM / AM warps (wt_oscillator.h), the amount in thousandths */
            v.xamount = std::clamp(P[k.xa] * 0.001f + modOff[k.dXa], 0.0f, 1.0f);
            v.xsrc = std::clamp((int) P[k.xs], 0, 4);
            v.xwarp2 = std::clamp((int) P[k.xw2], 0, 7);
            v.xamount2 = std::clamp(P[k.xa2] * 0.001f, 0.0f, 1.0f);
            v.xsrc2 = std::clamp((int) P[k.xs2], 0, 4);
            v.xvital = P[TB_P_XCHAIN] > 0.5f;
            oscGain[o] = v.gain;

            /* An oscillator counts from its pot and its routing, not its live
             * gain: an envelope crossing zero must not move anyone's unison. */
            bool on = P[k.level] > 0.0f || (int) P[TB_P_ME1_DST] == k.dLvl
                                        || (int) P[TB_P_ME2_DST] == k.dLvl
                                        || (int) P[TB_P_ME3_DST] == k.dLvl
                                        || (int) P[TB_P_ME4_DST] == k.dLvl;
            for (int r = 0; r < kLfoRoutes && !on; r++)
                on = P[lrSrc(r)] >= 1.0f && (int) P[lrSrc(r) + 1] == k.dLvl;
            unison[o] = on ? std::clamp(std::min((int) P[k.uni], (int) P[TB_P_UNI_CAP]), 1, kMaxUnison) : 0;   /* Unison Cap: the live limit on every stack */
        }
        fitUnison(unison, c.streamBudget);
        for (int o = 0; o < kOscs; o++)
            vp[o].voices = std::max(unison[o], 1);

        /* ---- sub + noise ---- */
        subNote = baseNote + P[TB_P_SUB_TUNE];
        const float subKnob = std::clamp(pot01(P[TB_P_SUB_LEVEL]) + lfoLvl[MD_SUB_LEVEL], 0.0f, 1.0f);
        float subLvl = std::clamp(subKnob * subKnob + modOff[MD_SUB_LEVEL],
                                  0.0f, 1.0f);
        float subPan = 0.0f;                 /* pan control cut: centered */
        switch ((int) P[TB_P_SUB_WAVE]) {
        case 0: subParams.wave = Wave::sine; break;
        case 1: subParams.wave = Wave::triangle; break;
        case 2: subParams.wave = Wave::sawUp; break;
        case 3: subParams.wave = Wave::pulse; subParams.pw = 0.50f; break;
        case 4: subParams.wave = Wave::pulse; subParams.pw = 0.25f; break;
        default: subParams.wave = Wave::pulse; subParams.pw = 0.125f; break;
        }
        subParams.leftGain  = subLvl * (1.0f - subPan);
        subParams.rightGain = subLvl * (1.0f + subPan);

        const float nzKnob = std::clamp(pot01(P[TB_P_NOISE_LEVEL]) + lfoLvl[MD_NOISE_LEVEL], 0.0f, 1.0f);
        float nzLvl = std::clamp(nzKnob * nzKnob + modOff[MD_NOISE_LEVEL],
                                 0.0f, 1.0f);
        const float nzPan = noisePan;        /* no pan control; the random per-note imbalance */
        const int nzType = (int) P[TB_P_NOISE_TYPE];
        noiseParams.wave = nzType == 0 ? Wave::whiteNoise : nzType == 4 ? Wave::brownNoise : Wave::pinkNoise;
        const float nzHpPot = P[TB_P_NOISE_HP], nzLpPot = P[TB_P_NOISE_LP];
        noiseHpHz = nzType == 2 ? 950.0f : nzType == 3 ? 1190.0f : nzHpPot >= 1.0f ? 20.0f * std::pow(1000.0f, nzHpPot / 127.0f) : 0.0f;
        noiseLpHz = nzLpPot < 126.5f ? 20.0f * std::pow(1000.0f, nzLpPot / 127.0f) : 0.0f;
        if (noiseHpHz > 0.0f && noiseHpHz != noiseHpSet) {
            noiseHp.setType(Filter::highpass);
            noiseHp.setSlope(Filter::db12);
            noiseHp.setParams(noiseHpHz, kFilterQ);
            noiseHpSet = noiseHpHz;
        }
        if (noiseLpHz > 0.0f && noiseLpHz != noiseLpSet) {
            noiseLp.setType(Filter::lowpass);
            noiseLp.setSlope(Filter::db12);
            noiseLp.setParams(noiseLpHz, kFilterQ);
            noiseLpSet = noiseLpHz;
        }
        noiseParams.leftGain  = nzLvl * (1.0f - nzPan);
        noiseParams.rightGain = nzLvl * (1.0f + nzPan);

        /* ---- filter drive ---- */
        driveGainTarget = potDriveGain(P[TB_P_FLT_DRIVE]);
        driveMixTarget = potDriveMix(P[TB_P_FLT_DRIVE]);
        drive2GainTarget = potDriveGain(P[TB_P_FLT2_DRIVE]);
        drive2MixTarget = potDriveMix(P[TB_P_FLT2_DRIVE]);
        if (blockSize == 0) {               /* a new note starts where the knob is */
            driveGain = driveGainTarget;
            driveMix = driveMixTarget;
            drive2Gain = drive2GainTarget;
            drive2Mix = drive2MixTarget;
        }

        /* ---- amp ---- */
        ampVelTrack = pot01(P[TB_P_VCA_VEL]);
        ampModGain = std::clamp(1.0f + modOff[MD_AMP], 0.0f, 2.0f);

        /* ---- filter (the original's law, verbatim) ---- */
        {
            const float filterWidth = 12.0f * std::log2(20000.0f / 440.0f) + 69.0f; /* midi(20k) */
            float env = filterADSR.getOutput();
            float sens = pot01(P[TB_P_FLT_VEL]);
            sens = velocity * sens + 1.0f - sens;

            float n = potFilterNote(P[TB_P_FLT_FREQ]);
            n += ((float) midiNote - 60.0f) * pot01(P[TB_P_FLT_KEY]);
            n += env * sens * potBipolar(P[TB_P_FLT_ENV]) * filterWidth;
            n += modOff[MD_FLT_FREQ] * filterWidth;

            const int ft = std::clamp((int) P[TB_P_FLT_TYPE], 0, 18);
            combType = ft >= 8 && ft < 16 ? ft - 7 : 0;                                  /* 1 Comb +, 2 Comb -, 3 Flange +, 4 Flange - */
            if (combType || ft >= 16) n += (P[TB_P_FLT_CFINE] - 64.0f) * 0.01f;              /* a comb is tuned in cents */
            float f = midiNoteToHz(n);
            float maxFreq = std::min(20000.0f, (float) (sampleRate / 2.0));
            f = std::clamp(f, combType ? 3.0f : 4.0f, maxFreq);

            float res = std::clamp(pot01(P[TB_P_FLT_RES]) + modOff[MD_FLT_RES],
                                   0.0f, 1.0f);
            float q = kFilterQ / (1.0f - res * 0.99f);
            if (P[TB_P_FLT_VDL] >= 0.5f) q = vitalFilterDriveQ(q, pot01(P[TB_P_FLT_DRIVE]));

            static const Filter::Type kTypes[8] = {
                Filter::lowpass, Filter::lowpass, Filter::highpass, Filter::highpass,
                Filter::bandpass, Filter::bandpass, Filter::notch, Filter::notch };
            combRes = res;
            vcMode = (int) P[TB_P_VCOMB]; vcBlend = blendOf(P[TB_P_CBLEND], MD_FLT_BLEND); vcTrans = ctransOf(P[TB_P_CTRANS], MD_FLT_CTRANS);   /* the Vital comb's blend and transpose (comb.h processVital) */
            int t = ft >= 8 ? 1 : ft & 7;
            filter.setType(kTypes[t]);
            filter.setSlope((t & 1) ? Filter::db24 : Filter::db12);
            vph1.on = P[TB_P_FLT_VB] >= 1.5f && P[TB_P_FLT_VB] < 2.5f; vph1Blend = blendOf(P[TB_P_FLT_BLEND], MD_FLT_BLEND); vph1Inv = (ft & 1) != 0;   /* Vital's Phaser filter: the type's low bit is its Style */
            vb1.on = P[TB_P_FLT_VB] >= 0.5f && P[TB_P_FLT_VB] < 1.5f && !combType && ft < 8;             /* Vital's Blend knob: a crossfade of the low / band / high outputs (see VBlend) */
            if (vb1.on) vb1.set(blendOf(P[TB_P_FLT_BLEND], MD_FLT_BLEND), (t & 1) != 0, q, P[TB_P_FLT_VBG] * (20.0f / 127.0f));
            sk1.on = P[TB_P_FLT_VSK] >= 0.5f && P[TB_P_FLT_VSK] < 1.5f && !combType && ft < 8;
            if (sk1.on) sk1.set(f, res, driveGainTarget, blendOf(P[TB_P_FLT_BLEND], MD_FLT_BLEND), (t & 1) != 0);
            ld1.on = P[TB_P_FLT_VSK] >= 1.5f && P[TB_P_FLT_VSK] < 2.5f && !combType && ft < 8;
            if (ld1.on) ld1.set(f, res, driveGainTarget, blendOf(P[TB_P_FLT_BLEND], MD_FLT_BLEND), (t & 1) != 0);
            dt1.on = P[TB_P_FLT_VSK] >= 2.5f && P[TB_P_FLT_VSK] < 3.5f && !combType && ft < 8;
            if (dt1.on) dt1.set(f, res, driveGainTarget, blendOf(P[TB_P_FLT_BLEND], MD_FLT_BLEND), (t & 1) != 0);
            dg1.on = P[TB_P_FLT_VSK] >= 3.5f && !combType && ft < 8;
            if (dg1.on) dg1.set(f, res, driveGainTarget, blendOf(P[TB_P_FLT_BLEND], MD_FLT_BLEND), (t & 1) != 0);
            fmOn1 = P[TB_P_FLT_VB] >= 2.5f || ft >= 16;     /* Vital's Formant model (X / Y between the style's vowels, Spread and Transpose, Resonance) or one of Serum's (Freq and Var: the measured tables), formant.h */
            if (ft >= 16) fmSet1 = serumFormantSet(ft - 16, n * kSerumFormantPerNote, P[TB_P_FLT_VAR] * (1.0f / 127.0f), res);
            else if (fmOn1) fmSet1 = vitalFormantSet((int) P[TB_P_FLT_FST], P[TB_P_FLT_FX] * (1.0f / 127.0f), P[TB_P_FLT_FY] * (1.0f / 127.0f), (P[TB_P_FLT_FSP] - 64.0f) * (1.0f / 64.0f),
                                                 (P[TB_P_FLT_FTR] - 64.0f) * (12.0f / 64.0f), P[TB_P_FLT_FRES] * (1.0f / 127.0f));
            fltTarget = f; fltQ = q;           /* applied in steps across the block (see runFilter) */

            /* Filter 2: the same law with its own knobs (and its own share of the filter envelope), and the destination the LFOs and
             * the spare envelopes can point at. */
            float n2 = potFilterNote(P[TB_P_FLT2_FREQ]);
            n2 += ((float) midiNote - 60.0f) * pot01(P[TB_P_FLT2_KEY]);
            n2 += env * sens * potBipolar(P[TB_P_FLT2_ENV]) * filterWidth;
            n2 += modOff[MD_FLT2_FREQ] * filterWidth;
            const int ft2 = std::clamp((int) P[TB_P_FLT2_TYPE], 0, 18);
            combType2 = ft2 >= 8 && ft2 < 16 ? ft2 - 7 : 0;
            if (combType2 || ft2 >= 16) n2 += (P[TB_P_FLT2_CFINE] - 64.0f) * 0.01f;
            fltTarget2 = std::clamp(midiNoteToHz(n2), combType2 ? 3.0f : 4.0f, maxFreq);
            combRes2 = std::clamp(pot01(P[TB_P_FLT2_RES]), 0.0f, 1.0f);
            vcMode2 = (int) P[TB_P_VCOMB2]; vcBlend2 = blendOf(P[TB_P_CBLEND2], MD_FLT2_BLEND); vcTrans2 = ctransOf(P[TB_P_CTRANS2], MD_FLT2_CTRANS);
            fltQ2 = kFilterQ / (1.0f - std::clamp(pot01(P[TB_P_FLT2_RES]), 0.0f, 1.0f) * 0.99f);
            if (P[TB_P_FLT_VDL] >= 0.5f) fltQ2 = vitalFilterDriveQ(fltQ2, pot01(P[TB_P_FLT2_DRIVE]));
            const int t2 = ft2 >= 8 ? 1 : ft2 & 7;
            filter2.setType(kTypes[t2]);
            filter2.setSlope((t2 & 1) ? Filter::db24 : Filter::db12);
            vph2.on = P[TB_P_FLT2_VB] >= 1.5f && P[TB_P_FLT2_VB] < 2.5f; vph2Blend = blendOf(P[TB_P_FLT2_BLEND], MD_FLT2_BLEND); vph2Inv = (ft2 & 1) != 0;
            vb2.on = P[TB_P_FLT2_VB] >= 0.5f && P[TB_P_FLT2_VB] < 1.5f && !combType2 && ft2 < 8;
            if (vb2.on) vb2.set(blendOf(P[TB_P_FLT2_BLEND], MD_FLT2_BLEND), (t2 & 1) != 0, fltQ2, P[TB_P_FLT2_VBG] * (20.0f / 127.0f));
            sk2.on = P[TB_P_FLT2_VSK] >= 0.5f && P[TB_P_FLT2_VSK] < 1.5f && !combType2 && ft2 < 8;
            if (sk2.on) sk2.set(fltTarget2, combRes2, drive2GainTarget, blendOf(P[TB_P_FLT2_BLEND], MD_FLT2_BLEND), (t2 & 1) != 0);
            ld2.on = P[TB_P_FLT2_VSK] >= 1.5f && P[TB_P_FLT2_VSK] < 2.5f && !combType2 && ft2 < 8;
            if (ld2.on) ld2.set(fltTarget2, combRes2, drive2GainTarget, blendOf(P[TB_P_FLT2_BLEND], MD_FLT2_BLEND), (t2 & 1) != 0);
            dt2.on = P[TB_P_FLT2_VSK] >= 2.5f && P[TB_P_FLT2_VSK] < 3.5f && !combType2 && ft2 < 8;
            if (dt2.on) dt2.set(fltTarget2, combRes2, drive2GainTarget, blendOf(P[TB_P_FLT2_BLEND], MD_FLT2_BLEND), (t2 & 1) != 0);
            dg2.on = P[TB_P_FLT2_VSK] >= 3.5f && !combType2 && ft2 < 8;
            if (dg2.on) dg2.set(fltTarget2, combRes2, drive2GainTarget, blendOf(P[TB_P_FLT2_BLEND], MD_FLT2_BLEND), (t2 & 1) != 0);
            fmOn2 = P[TB_P_FLT2_VB] >= 2.5f || ft2 >= 16;
            if (ft2 >= 16) fmSet2 = serumFormantSet(ft2 - 16, n2 * kSerumFormantPerNote, P[TB_P_FLT2_VAR] * (1.0f / 127.0f), combRes2);
            else if (fmOn2) fmSet2 = vitalFormantSet((int) P[TB_P_FLT2_FST], P[TB_P_FLT2_FX] * (1.0f / 127.0f), P[TB_P_FLT2_FY] * (1.0f / 127.0f), (P[TB_P_FLT2_FSP] - 64.0f) * (1.0f / 64.0f),
                                                 (P[TB_P_FLT2_FTR] - 64.0f) * (12.0f / 64.0f), P[TB_P_FLT2_FRES] * (1.0f / 127.0f));
        }
    }

    /* Scale the stacks to fit the budget, keeping their proportions and every
     * oscillator that is on. Rounding can leave the sum over by one or two when
     * a small stack is held at 1, so the largest gives way until it fits. */
    static void fitUnison(int *unison, int budget)
    {
        int want = 0;
        for (int o = 0; o < kOscs; o++) want += unison[o];
        if (want <= budget) return;
        int have = 0;
        for (int o = 0; o < kOscs; o++) {
            if (unison[o] > 0) unison[o] = std::max(1, unison[o] * budget / want);
            have += unison[o];
        }
        while (have > budget) {
            int *largest = std::max_element(unison, unison + kOscs);
            if (*largest <= 1) break;
            (*largest)--;
            have--;
        }
    }

    VoicedWTOscillator osc[kOscs] = { VoicedWTOscillator(kMaxUnison),
                                      VoicedWTOscillator(kMaxUnison),
                                      VoicedWTOscillator(kMaxUnison) };
    VoicedWTParams vp[kOscs];
    VoicedWTOscillator modOsc[kOscs] = { VoicedWTOscillator(1), VoicedWTOscillator(1), VoicedWTOscillator(1) };     /* the oscillators as FM / PD / RM / AM modulators */
    StereoOscillator sub, noise;
    FastGauss modNoise { 1.0f };                               /* the Noise as the modulator of an FM / PD / RM warp */
    StereoOscillator::Params subParams, noiseParams;
    /* The filter's cutoff moves with the envelope and the LFOs, and the coefficients were set once per block: a step at each block edge,
     * which at a low cutoff on a loud low note is a click (a one-sample spike at the first block edge of a note, and at every edge while the
     * envelope is moving). Now the cutoff is stepped in kFltSub pieces across the block, in log frequency. */
    static constexpr int kFltSub = 4;
    void runFilter(Filter &flt, float &prev, float target, float q, float *l, float *r, int n)
    {
        if (prev < 0.0f) prev = target;
        const float lp = std::log(prev), lt = std::log(target);
        /* A step every 32 samples or so: the 4 of a full block, fewer for a short call (the demo, clip and arp players split the block at their notes), which
         * otherwise designed the filter 4 times over for a few samples. A full block is stepped exactly as before. */
        const int steps = std::min(std::max(n / 32, 1), kFltSub);
        int done = 0;
        for (int k = 1; k <= steps; k++) {
            const int end = k == steps ? n : (n * k) / steps;
            if (end <= done) continue;
            flt.setParams(std::exp(lp + (lt - lp) * (float) k / (float) steps), q);
            flt.process(l + done, r + done, end - done);
            done = end;
        }
        prev = target;
    }
    /* Vital's filter Blend knob on its first four models, measured through the plugin (calibration/vital_vfilter_probe.py): 0 low-pass, 1 band-pass, 2 high-pass, and between two neighbours the
     * ends are weighted (1 - x) for the outer one and sqrt(1 - (1 - x)^2) for the band-pass (equal power: the sum of squares stays 1), x being the distance from the outer end. A 24 dB style is two
     * such 12 dB stages in a row with the same weights (the second at the fixed Q). Vital's band-pass peaks 6 dB under the biquad's (3 dB per stage in a 24 dB style), and that peak rises with the
     * resonance (0.45 dB per dB of Q over a 12 dB filter, 0.9 over a 24 dB one, on the first stage). */
    struct VBlend {
        bool on = false, steep = false;
        Filter f[4];                                   /* stage 1: f[0] the nearer end, f[1] the next; stage 2 (24 dB): f[2], f[3] */
        float prev[4] = { -1.0f, -1.0f, -1.0f, -1.0f };
        float wa[2] = { 1.0f, 1.0f }, wb[2] = { 0.0f, 0.0f };
        void setSampleRate(double sr) { for (Filter &x : f) x.setSampleRate(sr); }
        void reset() { for (int i = 0; i < 4; i++) { f[i].reset(); prev[i] = -1.0f; } }
        void set(float blend, bool steep_, float q, float dropDb)
        {
            steep = steep_;
            blend = std::clamp(blend, 0.0f, 2.0f);
            const bool upper = blend > 1.0f;
            const float x = upper ? blend - 1.0f : blend;                             /* 0 at the nearer end of the pair .. 1 at the next */
            const float res = std::clamp((1.0f - kFilterQ / std::max(q, kFilterQ)) / 0.99f, 0.0f, 1.0f);
            const float outer = (upper ? x : 1.0f - x) * std::pow(10.0f, -dropDb * std::pow(res, 0.9f) * 0.05f);   /* the weight of the low-pass (lower pair) or the high-pass (upper pair); the passband falls with the resonance */
            const float oe = upper ? x : 1.0f - x, band = std::sqrt(std::max(0.0f, 1.0f - oe * oe));
            const float dbq = 20.0f * std::log10(std::max(q, kFilterQ) / kFilterQ);
            for (int st = 0; st < (steep ? 2 : 1); st++) {
                const float outerSt = st == 0 ? outer : oe;                                 /* the passband drop is taken once, on the first stage */
                const float bpdb = steep ? (st == 0 ? -3.0f + 0.9f * dbq : -3.0f) : -6.0f + 0.45f * dbq;
                const float bpg = std::pow(10.0f, bpdb * 0.05f) * band;
                wa[st] = upper ? bpg : outerSt;
                wb[st] = upper ? outerSt : bpg;
                f[2 * st].setType(upper ? Filter::bandpass : Filter::lowpass);
                f[2 * st + 1].setType(upper ? Filter::highpass : Filter::bandpass);
                f[2 * st].setSlope(Filter::db12);
                f[2 * st + 1].setSlope(Filter::db12);
            }
        }
    };
    void blendStage(Filter &a, float &pa, Filter &b, float &pb, float wa, float wb, float target, float q, float *l, float *r, int n)
    {
        if (wb <= 0.004f) { runFilter(a, pa, target, q, l, r, n); for (int i = 0; i < n; i++) { l[i] *= wa; r[i] *= wa; } return; }          /* one end only: the other filter is not run */
        if (wa <= 0.004f) { runFilter(b, pb, target, q, l, r, n); for (int i = 0; i < n; i++) { l[i] *= wb; r[i] *= wb; } return; }
        float tl[kBlock], tr[kBlock];
        std::copy(l, l + n, tl); std::copy(r, r + n, tr);
        runFilter(a, pa, target, q, l, r, n);
        runFilter(b, pb, target, q, tl, tr, n);
        for (int i = 0; i < n; i++) { l[i] = l[i] * wa + tl[i] * wb; r[i] = r[i] * wa + tr[i] * wb; }
    }
    void runBlend(VBlend &v, float target, float q, float *l, float *r, int n)
    {
        blendStage(v.f[0], v.prev[0], v.f[1], v.prev[1], v.wa[0], v.wb[0], target, q, l, r, n);
        if (v.steep) blendStage(v.f[2], v.prev[2], v.f[3], v.prev[3], v.wa[1], v.wb[1], target, kFilterQ, l, r, n);
    }
    /* The drive stage before a filter (tanh of the gained signal, mixed in) and after it (tanh alone), both ramped across the block. */
    static void driveIn(float *l, float *r, int n, float g0, float gT, float m0, float mT)
    {
        const float gStep = (gT - g0) / (float) n, mStep = (mT - m0) / (float) n;
        float g = g0, m = m0;
        for (int i = 0; i < n; i++) {
            l[i] += m * (fastTanh(g * l[i]) - l[i]);
            r[i] += m * (fastTanh(g * r[i]) - r[i]);
            g += gStep; m += mStep;
        }
    }
    static void driveOut(float *l, float *r, int n, float m0, float mT)
    {
        const float mStep = (mT - m0) / (float) n;
        float m = m0;
        for (int i = 0; i < n; i++) {
            l[i] += m * (fastTanh(l[i]) - l[i]);
            r[i] += m * (fastTanh(r[i]) - r[i]);
            m += mStep;
        }
    }
    float fltTarget = 1000.0f, fltPrev = -1.0f, fltQ = kFilterQ;
    float fltTarget2 = 1000.0f, fltPrev2 = -1.0f, fltQ2 = kFilterQ;
    float drive2Gain = 1.0f, drive2Mix = 0.0f, drive2GainTarget = 1.0f, drive2MixTarget = 0.0f;

    Filter filter, filter2, noiseHp, noiseLp;
    VBlend vb1, vb2;                              /* the voice filters' Blend crossfades (used in place of filter / filter2 while .on) */
    VitalSK sk1, sk2;                             /* Vital's Analog (Sallen-Key) filter, used in place of everything else for a filter while .on (vital_sk.h) */
    VitalLadder ld1, ld2;                         /* Vital's Ladder filter, the same way */
    VitalDirty dt1, dt2;                          /* and its Dirty filter */
    VitalDigital dg1, dg2;                        /* and its Digital filter */
    CombFilter comb, comb2;                       /* the comb / flange / phaser filter types (comb.h); 0 = the biquad is used, 1..4 = Comb +, Comb -, Flange +, Flange -, 5..8 = Phase 12..48 + */
    PhaserFilter phaser, phaser2;
    VitalPhaserFilter vph1, vph2;                 /* Vital's Phaser voice filter (comb.h); flt_vb = 2 */
    FormantBank fm1, fm2;                         /* Vital's Formant voice filter (formant.h); flt_vb = 3 */
    FormantSet fmSet1, fmSet2;
    bool fmOn1 = false, fmOn2 = false;
    float vph1Blend = 1.0f, vph2Blend = 1.0f; bool vph1Inv = false, vph2Inv = false, vphClean = false;
    int combType = 0, combType2 = 0;
    float combRes = 0.0f, combRes2 = 0.0f;
    int vcMode = 0, vcMode2 = 0;                  /* 0 Serum's comb law, 1 Vital blend, 2 Vital band spread */
    float vcBlend = 0.0f, vcBlend2 = 0.0f, vcTrans = 0.0f, vcTrans2 = 0.0f;
    float noiseHpHz = 0.0f, noiseHpSet = 0.0f, noiseLpHz = 0.0f, noiseLpSet = 0.0f, noisePan = 0.0f;
    SamplePlayer smp, smp2, smp3;
    GrainCloud gran, gran2, gran3;      /* the same slots in their Granular kind */
    AnalogADSR filterADSR, adsr;
    AnalogADSR modEnv[kModEnvs];        /* the spare, routable envelopes */
    /* a voice filter's Blend knob (a 0..127 pot = 0..2) with the routes into it (a fraction of the 0..2 range) added */
    float voicePitchMod() const { return modOff[MD_VOICE_PITCH] * 48.0f + modOff[MD_VOICE_FINE] * 2.0f; }
    /* the Vital comb's Blend Transpose in semitones: the pot's, and a route's fraction of Vital's 0..84 range added (held to that range as Vital does, only while a route moves it) */
    float ctransOf(float potV, int dst) const { const float t = (potV - 64.0f) * 0.75f; return modOff[dst] != 0.0f ? std::clamp(t + modOff[dst] * 84.0f, 0.0f, 84.0f) : t; }
    float blendOf(float potV, int dst) const { return std::clamp(potV * (1.0f / 127.0f) + modOff[dst], 0.0f, 1.0f) * 2.0f; }
    float modOff[MD_COUNT] = {};        /* their summed offset per destination */
    /* Vital's random modulators (random_lfo.cpp), one set per voice: a new random value is drawn for every note. Perlin and Sine glide from the last value to the next over each cycle; Sample and Hold
     * steps to the next at the end of the cycle; Lorenz runs the attractor (a sample-rate loop, a block at a time). Each reads 0..1, as Vital's do. */
    uint32_t rnSeed = 12345u;
    float rnLast[kRnds] = {}, rnNext[kRnds] = {}, rnPhase[kRnds] = {}, rnLor[kRnds][3] = {};
    float rnDraw() { rnSeed ^= rnSeed << 13; rnSeed ^= rnSeed >> 17; rnSeed ^= rnSeed << 5; return (float) (rnSeed >> 8) * (2.0f / 16777216.0f) - 1.0f; }      /* -1 .. 1 */
    void resetRandoms(const float *P)
    {
        static uint32_t counter = 1u;
        rnSeed = (counter++ * 2654435761u) ^ 0x9e3779b9u; if (!rnSeed) rnSeed = 1u;
        for (int i = 0; i < kRnds; i++) {
            rnPhase[i] = 0.0f; rnLast[i] = rnDraw(); rnNext[i] = rnDraw();
            rnLor[i][0] = rnDraw(); rnLor[i][1] = rnDraw(); rnLor[i][2] = rnDraw() + 37.6f;
        }
        (void) P;
    }
    void stepRandoms(const float *P, float bpm, int blockSize, float *out)
    {
        bool used[kRnds] = {};
        for (int r = 0; r < kLfoRoutes; r++) {
            const int src = (int) P[lrSrc(r)];
            if (src > kLfosA && src <= kLfosA + kRnds && (int) P[lrSrc(r) + 1] > MD_NONE) used[src - kLfosA - 1] = true;
        }
        for (int i = 0; i < kRnds; i++) {
            if (!used[i]) { out[i] = 0.5f; continue; }
            const int base = TB_P_RN1_STYLE + i * (TB_P_RN2_STYLE - TB_P_RN1_STYLE);
            const int style = std::clamp((int) P[base], 0, 3);
            float hz = potLfoHz(P[base + 1]);
            if (P[base + 2] >= 0.5f) hz *= (bpm >= 20.0f ? std::clamp(bpm, 40.0f, 300.0f) : 120.0f) / 120.0f;
            const float blk = blockSize > 0 ? (float) blockSize : 0.0f;
            if (style == 3) {                                                           /* Lorenz: dx = a (y - x), dy = (b - z) x - y, dz = x y - c z, scaled to 0..1 (random_lfo.cpp) */
                const float t = std::min(0.01f, hz * (0.5f / (float) sampleRate));
                float x = rnLor[i][0], y = rnLor[i][1], z = rnLor[i][2];
                for (int k = 0; k < blockSize; k++) { const float dx = (y - x) * 10.0f, dy = (-z + 28.0f) * x - y, dz = x * y - z * (8.0f / 3.0f); x += dx * t; y += dy * t; z += dz * t; }
                rnLor[i][0] = x; rnLor[i][1] = y; rnLor[i][2] = z;
                out[i] = std::clamp(x * (1.0f / 40.0f) + 0.5f, 0.0f, 1.0f);
                continue;
            }
            rnPhase[i] += hz * blk / (float) sampleRate;
            while (rnPhase[i] >= 1.0f) { rnPhase[i] -= 1.0f; rnLast[i] = rnNext[i]; rnNext[i] = rnDraw(); }
            const float t = rnPhase[i];
            float v;
            if (style == 1) v = rnLast[i];                                              /* Sample and Hold */
            else if (style == 0) v = ((rnLast[i] * t) + ((rnNext[i] * (t - 1.0f)) - (rnLast[i] * t)) * (t * t * (3.0f - 2.0f * t))) * 2.0f;      /* Perlin: perlinInterpolate */
            else v = rnLast[i] + (rnNext[i] - rnLast[i]) * (0.5f + 0.5f * std::sin((t * 0.5f - 0.25f) * 6.2831853f));      /* Sine: sinInterpolate, sin(2 pi (t / 2 - 1 / 4)) */
            out[i] = std::clamp(v * 0.5f + 0.5f, 0.0f, 1.0f);
        }
    }
    float lfoPhase[kLfos] = {};
    float lfoDelayLeft[kLfos] = {};      /* seconds of Vital LFO Delay still to wait, and how far the Fade In has got (0..1) */
    float lfoFadeAmp[kLfos] = {};
    float lfoSm[kLfos] = {};             /* each LFO's smoothed output, and whether it has started since the note began */
    bool lfoSmInit[kLfos] = {};
    float levelAge = 0.0f;               /* seconds since the note (or its non-legato retrigger) began */
    float lfoLvl[MD_COUNT] = {};         /* LFO offsets to the level KNOBS (before the taper) */
    ValueSmoother<float> noteSmoother;
    const Wavetable *lastTable[kOscs] = {};

    double sampleRate = 44100.0;
    float oscNote[kOscs] = { 60.0f, 60.0f, 60.0f }, oscGain[kOscs] = {};
    float subNote = 48.0f;
    float velocity = 0.0f, ampVelTrack = 1.0f, ampModGain = 1.0f;
    float driveGain = 1.0f, driveMix = 0.0f, driveGainTarget = 1.0f, driveMixTarget = 0.0f;
    int midiNote = -1;
    uint32_t serial = 0;
    bool active = false, fastKill = false, glissando = false, portamento = false;
};

} // namespace tb
