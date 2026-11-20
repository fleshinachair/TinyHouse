/* The master FX block: width, delay, reverb and a four-band tone EQ, in that order, between
 * the voices and the output stage.
 *
 * Why it is in the synth. A Schwung preset can only set this module's own parameters, never
 * another slot's FX, so a preset whose sound depends on its reverb and EQ would arrive
 * without them. These are the effects the Serum deep house pack leans on, with the ranges
 * its presets use, and nothing more. It is a bypass at default, so a patch that sets none of
 * them is bit-identical to one built without this file.
 *
 * Everything here runs on the SPI callback, so there is no allocation after construction
 * and no per-sample division or transcendental. Each stage skips itself when it is off.
 */
#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>

#include "param_map.h"
#include "saturate.h"
#include "vital_sk.h"
#include "comb.h"
#include "conv.h"
#include "../ported/filter.h"

namespace tb {

/* ------------------------------------------------------------------ pot maps */

enum EqType { EQ_OFF = 0, EQ_BELL, EQ_LOSHELF, EQ_HISHELF, EQ_HIPASS, EQ_LOPASS,
              EQ_VLOSHELF, EQ_VBELL, EQ_VHISHELF, EQ_VHIPASS, EQ_VLOPASS, EQ_VNOTCH };         /* the last six are Vital's EQ bands (Biquad::setVital) */
inline bool eqIsCut(int t) { return t == EQ_HIPASS || t == EQ_LOPASS || t == EQ_VHIPASS || t == EQ_VLOPASS || t == EQ_VNOTCH; }

/* 20 Hz .. 20 kHz, exponential. */
inline float potEqFreq(float v) { return 20.0f * std::pow(1000.0f, pot01(v)); }
/* +/-24 dB, 64 is flat. */
inline float potEqGain(float v) { return potBipolar(v) * 24.0f; }

/* Q from the pot, which holds Serum's Reso (0..100) as 0..127. Serum's own mapping, measured
 * (calibration/fx_probe.py eqfit): a bell or a cut goes from very wide to narrow, and a
 * shelf's slope from gentle to resonant. */
inline float potEqQ(int type, float v)
{
    if (type >= EQ_VLOSHELF) return std::min(std::max(v * (1.0f / 127.0f), 0.0f), 1.0f);      /* Vital's own: the resonance knob, 0..1 */
    static const float reso[9] = { 0, 10, 25, 40, 50, 60, 75, 90, 100 };
    static const float qBell[9] = { 0.10f, 0.10f, 0.16f, 0.58f, 1.25f, 2.4f, 5.5f, 10.0f, 14.0f };
    static const float qShelf[9] = { 0.40f, 0.40f, 0.44f, 0.51f, 0.64f, 0.79f, 1.13f, 1.67f, 2.10f };
    const float *q = (type == EQ_LOSHELF || type == EQ_HISHELF) ? qShelf : qBell;
    const float r = std::min(std::max(v * (100.0f / 127.0f), 0.0f), 100.0f);
    int i = 0;
    while (i < 7 && r > reso[i + 1]) i++;
    const float x = (r - reso[i]) / (reso[i + 1] - reso[i]);
    return std::exp(std::log(q[i]) + x * (std::log(q[i + 1]) - std::log(q[i])));
}

/* ------------------------------------------------------------------ biquad EQ */

/* Anti-denormal. A filter or feedback state left to decay on silence walks down through the denormal range,
 * where ARM cores take many times longer per operation: a reverb tail or a ringing EQ then costs more CPU as
 * it gets quieter, and a block that misses its deadline is a crackle. Below 1e-15 (-300 dB) a state is no
 * sound at all, so it is flushed to exactly 0. The hardware flush-to-zero bit is set as well (see
 * tablor_plugin.cpp); this holds where it is not, and costs a compare. */
inline float sanitize(float x) { return std::fabs(x) < 1e-15f ? 0.0f : x; }

struct Biquad {
    float b0 = 1, b1 = 0, b2 = 0, a1 = 0, a2 = 0;

    /* Vital's EQ bands, which are its Digital filter (DigitalSvf, a trapezoidal state-variable filter) in the shapes the EQ module picks: the low band is a shelf or a high-pass, the band a
     * bell or a notch, the high band a shelf or a low-pass. The filter is out = hi s^2 + band s + lo over s^2 + k s + 1 (s in units of the cutoff, k = 1 / (0.5 + 15.5 res^3), res the
     * Resonance knob, squared first), and a trapezoidal filter is the bilinear transform at the pre-warped cutoff tan(pi f / sr). A shelf or bell at gain A (an amplitude) has lo, hi and band set by the
     * blend (-1 low shelf, 0 bell, 1 high shelf), as vital/src/synthesis/filters/digital_svf.cpp does it (its setupFilter also shifts a shelf's cutoff by A^(blend / 4), but the filter reads the
     * unshifted one: measured through the plugin, the shelves fit with no shift to 0.00 dB). */
    void setVital(int type, float fc, float gainDb, float res, float sr)
    {
        const float r2 = std::min(std::max(res, 0.0f), 1.0f), r3 = r2 * r2;                 /* the Resonance knob is squared on its way in (measured: knob 0.5 gives a resonance of 0.742, 0.7 gives 2.32, 1 gives 16), then cubed */
        const float k = 1.0f / (0.5f + 15.5f * r3 * r3 * r3);
        const float A = std::pow(10.0f, std::min(std::max(gainDb, -15.0f), 15.0f) / 20.0f);
        float lo = 0, band = 0, hi = 0;
        if (type == EQ_VLOSHELF || type == EQ_VBELL || type == EQ_VHISHELF) {
            const float blend = type == EQ_VLOSHELF ? -1.0f : type == EQ_VBELL ? 0.0f : 1.0f;
            const float lowBellT = std::min(std::max(blend + 1.0f, 0.0f), 1.0f), bellHighT = std::min(std::max(blend, 0.0f), 1.0f), bandT = 1.0f - blend * blend;
            const float sA = std::sqrt(A);
            lo = A + (1.0f - A) * lowBellT;
            hi = 1.0f + (A - 1.0f) * bellHighT;
            band = k * sA * (1.0f + (sA - 1.0f) * bandT);
        } else if (type == EQ_VHIPASS) { hi = 1.0f; }
        else if (type == EQ_VLOPASS) { lo = 1.0f; }
        else { lo = 1.0f; hi = 1.0f; }                                          /* notch */
        const float f = std::min(std::max(fc, 1.0f), sr * 0.499f);
        const float c = 1.0f / std::tan(3.14159265f * f / sr), c2 = c * c;
        const float B0 = hi * c2 + band * c + lo, B1 = 2.0f * (lo - hi * c2), B2 = hi * c2 - band * c + lo;
        const float A0 = c2 + k * c + 1.0f, A1 = 2.0f * (1.0f - c2), A2 = c2 - k * c + 1.0f;
        const float inv = 1.0f / A0;
        b0 = B0 * inv; b1 = B1 * inv; b2 = B2 * inv; a1 = A1 * inv; a2 = A2 * inv;
    }

    /* RBJ cookbook. fc is clamped below Nyquist so a 20 kHz band cannot go unstable. */
    void set(int type, float fc, float gainDb, float q, float sr)
    {
        if (type >= EQ_VLOSHELF) { setVital(type, std::min(std::max(fc, 10.0f), sr * 0.499f), gainDb, q, sr); return; }
        fc = std::min(std::max(fc, 10.0f), sr * 0.45f);
        const float w0 = 6.28318530718f * fc / sr;
        const float cw = std::cos(w0), sw = std::sin(w0);
        const float al = sw / (2.0f * std::max(q, 0.05f));
        const float A = std::pow(10.0f, gainDb / 40.0f);
        float B0, B1, B2, A0, A1, A2;
        switch (type) {
        case EQ_BELL:
            B0 = 1 + al * A; B1 = -2 * cw; B2 = 1 - al * A;
            A0 = 1 + al / A; A1 = -2 * cw; A2 = 1 - al / A;
            break;
        case EQ_LOSHELF: {
            const float r = 2 * std::sqrt(A) * al;
            B0 = A * ((A + 1) - (A - 1) * cw + r);
            B1 = 2 * A * ((A - 1) - (A + 1) * cw);
            B2 = A * ((A + 1) - (A - 1) * cw - r);
            A0 = (A + 1) + (A - 1) * cw + r;
            A1 = -2 * ((A - 1) + (A + 1) * cw);
            A2 = (A + 1) + (A - 1) * cw - r;
            break;
        }
        case EQ_HISHELF: {
            const float r = 2 * std::sqrt(A) * al;
            B0 = A * ((A + 1) + (A - 1) * cw + r);
            B1 = -2 * A * ((A - 1) + (A + 1) * cw);
            B2 = A * ((A + 1) + (A - 1) * cw - r);
            A0 = (A + 1) - (A - 1) * cw + r;
            A1 = 2 * ((A - 1) - (A + 1) * cw);
            A2 = (A + 1) - (A - 1) * cw - r;
            break;
        }
        case EQ_HIPASS:
            B0 = (1 + cw) / 2; B1 = -(1 + cw); B2 = (1 + cw) / 2;
            A0 = 1 + al; A1 = -2 * cw; A2 = 1 - al;
            break;
        case EQ_LOPASS:
            B0 = (1 - cw) / 2; B1 = 1 - cw; B2 = (1 - cw) / 2;
            A0 = 1 + al; A1 = -2 * cw; A2 = 1 - al;
            break;
        default:
            b0 = 1; b1 = b2 = a1 = a2 = 0;
            return;
        }
        const float inv = 1.0f / A0;
        b0 = B0 * inv; b1 = B1 * inv; b2 = B2 * inv; a1 = A1 * inv; a2 = A2 * inv;
    }
};

struct StereoBiquad {
    Biquad c;
    float z1[2] = { 0, 0 }, z2[2] = { 0, 0 };

    inline float tick(int ch, float x)
    {
        const float y = c.b0 * x + z1[ch];
        z1[ch] = sanitize(c.b1 * x - c.a1 * y + z2[ch]);
        z2[ch] = sanitize(c.b2 * x - c.a2 * y);
        return y;
    }
    void clear() { z1[0] = z1[1] = z2[0] = z2[1] = 0; }
};

/* Vital's distortion filter (calibration/vital_distfilt_probe.py): a two-pole state-variable filter. Measured against a saw through the real plugin: the corner is the MIDI note's
 * frequency, the Q follows the resonance by the table below (0.5 .. 14.2), and the blend mixes the low-pass (1 - b), the band-pass peaking at Q and the high-pass so that their POWER
 * weights are (1 - b)^2 and 1 - (1 - b)^2 (a real mix of the two quadrature outputs does exactly that), which keeps the peak at the corner at Q across the blend. */
struct VitalDistFilter {
    float a1 = 0, a2 = 0, a3 = 0, k = 2, wl = 1, wb = 0, wh = 0;
    float ic1[2] = { 0, 0 }, ic2[2] = { 0, 0 };

    static float qOfRes(float res)
    {
        static const float q[21] = { 0.500f, 0.502f, 0.515f, 0.552f, 0.624f, 0.742f, 0.918f, 1.164f, 1.490f, 1.909f, 2.430f, 3.063f, 3.818f, 4.700f, 5.714f, 6.860f, 8.133f, 9.522f, 11.009f, 12.572f, 14.179f };
        const float x = std::min(std::max(res, 0.0f), 1.0f) * 20.0f;
        const int i = std::min((int) x, 19);
        const float f = x - (float) i;
        return q[i] * std::pow(q[i + 1] / q[i], f);
    }
    void set(float note, float res, float blend, float sr)
    {
        const float hz = std::min(440.0f * std::pow(2.0f, (note - 69.0f) / 12.0f), 0.45f * sr);
        const float g = std::tan(3.14159265f * hz / sr);
        k = 1.0f / qOfRes(res);
        a1 = 1.0f / (1.0f + g * (g + k)); a2 = g * a1; a3 = g * a2;
        const float b = std::min(std::max(blend, 0.0f), 2.0f);
        if (b <= 1.0f) { wl = 1.0f - b; wh = 0.0f; wb = std::sqrt(1.0f - wl * wl); }
        else           { wh = b - 1.0f; wl = 0.0f; wb = std::sqrt(1.0f - wh * wh); }
    }
    inline float tick(int c, float x)
    {
        const float v3 = x - ic2[c];
        const float v1 = a1 * ic1[c] + a2 * v3;
        const float v2 = ic2[c] + a2 * ic1[c] + a3 * v3;
        ic1[c] = sanitize(2.0f * v1 - ic1[c]);
        ic2[c] = sanitize(2.0f * v2 - ic2[c]);
        return wl * v2 + wb * v1 + wh * (x - k * v1 - v2);
    }
    void clear() { ic1[0] = ic1[1] = ic2[0] = ic2[1] = 0; }
};

/* ------------------------------------------------------------------ wet and dry laws */

/* Serum's wet knob is not a straight crossfade, and neither law below is a guess: both were
 * measured on a burst through Serum's own delay and reverb (calibration/fx_probe.py).
 * The wet side is a smoothstep: the tail at wet 10, 25, 50, 75 was 0.027, 0.15, 0.51, 0.87 of
 * its wet-100 level. The dry side holds full level up to 25 % and then fades out by
 * smoothstep, which gave -2.1 dB at 50 % and -11.7 dB at 75 %. */
inline float smooth01(float x) { x = std::min(std::max(x, 0.0f), 1.0f); return x * x * (3.0f - 2.0f * x); }
inline float wetGain(float w01) { return smooth01(w01); }
inline float dryGain(float w01) { return w01 <= 0.25f ? 1.0f : 1.0f - smooth01((w01 - 0.25f) / 0.75f); }

/* ------------------------------------------------------------------ the block */

struct FxSettings {
    /* All in physical units: Engine::fxSettings fills them from the pots. Mixes are 0..1. */
    float wideMix = 0, wideRate = 0.5f, wideDepth = 0.5f;
    float delayMix = 0, delayMs = 250, delayRMs = 250, delayFb = 0.4f, delayLoHz = 20, delayHiHz = 16000;
    int   delayPing = 0;
    bool  delayVital = false;                                     /* Vital's own delay (processDelayVital): the style and the equal-power dry / wet knob */
    int   delayStyle = 0;
    float delayVMix = 0;
    float delay2Mix = 0, delay2Ms = 250, delay2RMs = 250, delay2Fb = 0.4f, delay2LoHz = 20, delay2HiHz = 16000;   /* a second delay after the first (Serum racks with two) */
    int   delay2Ping = 0;
    float revMix = 0, revT60 = 2.0f, revDampHz = 8000, revLoHz = 20, revWidth = 1.0f;
    /* Vital's reverb (processReverbVital): Off keeps Serum's. Mix is an equal-power crossfade; Decay a T60 in seconds; the notes are MIDI notes. */
    bool  revVital = false;
    float revVSize = 0.5f, revVDecay = 2.0f, revVPreLo = 0, revVPreHi = 110, revVLoNote = 0, revVLoDb = 0, revVHiNote = 90, revVHiDb = -1, revVChorus = 0.2f, revVChorusHz = 0.25f, revVDelayS = 0;
    float convMix = 0;        /* Serum's Conv Wet 0..1: the dry part cos(pi w / 2), the convolved part sin^2(pi w / 2) of the Wet-100 level (calibration/probe_conv_blend.py) */
    struct Band { int type = EQ_OFF; float hz = 1000, gainDb = 0, q = 1; } band[4];
    Band msBand[2][4];        /* the EQs inside a SplitMS node: [0] the Mid branch, [1] the Side branch (fxmap.ms_branches) */
    bool eqEnable = true;     /* the EQ's own switch: it fades in and out, the bands keep running */
    float distDrive = 0, distMix = 1;       /* distortion: drive 0..1 (0 is off), wet part 0..1 */
    int   distMode = 0;                     /* 0 soft (tanh), 1 asymmetric, 2 hard clip, 3 sine, 4 hold (the calibrated law only) */
    int   distHold = 1;                     /* mode 4: Serum's Downsample, a sample-and-hold of this many samples */
    bool  distCal = false;                  /* the calibrated law: out = level (1 - s^2) x + s out curve(gain x), s = distMix */
    float distGain = 1, distOut = 1, distLevel = 1;
    bool  distVital = false;                /* Vital's measured law (calibration/vital_dist_probe.py): out = (1 - mix) x + mix curve(gain x), the curve against Vital's own full scale; distGain is its gain, distMode its curve (0 tanh, 2 hard clip, 3 sine fold, 4 hold, 5 linear fold) */
    float distDriveDb = 0;                  /* the Vital law's drive in dB (distGain is its linear gain) */
    int   distFlt = 0;                      /* Vital's distortion filter: 0 off, 1 before the curve, 2 after it; on the wet part only */
    float distFltNote = 60, distFltRes = 0, distFltBlend = 0;      /* cutoff as a MIDI note, resonance 0..1, blend 0 low-pass .. 1 band-pass .. 2 high-pass */
    bool  distAudio = false;                /* Vital's keyboard-following LFO on the distortion, run at audio rate inside the stage (the voices' LFOs only step once per block) */
    float distAudioAmt[4] = { 0, 0, 0, 0 }; /* its route amounts to Drive, filter Cutoff, Res, Blend (a fraction of each control's range) */
    float distAudioTrans = 0;               /* semitones added to the newest note for its frequency */
    float distAudioShape[16] = {};          /* one cycle, 0..1 */
    int   distPre = 0;                      /* a filter before the curve, on the distorted part: 0 off, 1 low-pass, 2 high-pass (the calibrated law only) */
    float distPreHz = 1000, distPreQ = 1;
    float compMix = 0;                      /* compressor: parallel part 0..1 (0 is off) */
    float compThreshDb = 0, compRatio = 1, compAttackMs = 10, compReleaseMs = 100, compMakeupDb = 0, compKneeDb = 12;
    float compBoostDb = 12;                 /* Comp Boost Cap: the most the multiband compressor's upward part may add to a band, dB */
    int   fxOrder = 0;                      /* FX Order: the rank of the main chain's stage order among the 7! permutations (0 = the default order) */
    bool  compMulti = false;                /* three bands, each with its own threshold and gain, and upward compression below */
    float compThreshBandDb[3] = { 0, 0, 0 }, compBandGainDb[3] = { 0, 0, 0 }, compBelow = 0;
    /* Vital's compressor (processCompVital, derived from Vital's effects/compressor.cpp): per band an upper threshold and ratio (downward) and a lower threshold and ratio (upward when positive,
     * downward expansion when negative), a gain, the shared attack / release knobs, and which of the bands are split off. Levels are the engine's, converted by the Vital converter. */
    bool  compVital = false;
    int   vcBands = 0;                      /* 0 three bands, 1 low + (mid and high as one), 2 (low and mid as one) + high, 3 one band */
    float vcUpThrDb[3] = { 12, 12, 12 }, vcLoThrDb[3] = { -100, -100, -100 }, vcUpRatio[3] = { 0, 0, 0 }, vcLoRatio[3] = { 0, 0, 0 }, vcGainDb[3] = { 0, 0, 0 };
    float vcAttack = 0.5f, vcRelease = 0.5f;
    /* Serum's Chorus, Flanger and Phaser. Each mix is 0 when its switch is off (the stage then fades out and is skipped). */
    float chorMix = 0, chorRate = 0.5f, chorD1Ms = 5.0f, chorD2Ms = 5.2f, chorDepth = 0.5f, chorFb = 0, chorFiltHz = 20000;
    bool  chorHpf = false;
    bool  flanVital = false;                 /* Vital's flanger (processFlangerVital): Depth scales the delay, Feedback bipolar, Phase the right side's offset */
    float flanVCenterNote = 64;              /* its centre: the delay is 1 / this note's frequency (a MIDI note) */
    bool  phasVital = false;                 /* Vital's phaser (processPhaserVital): 4 / 8 / 12 all-pass stages, Depth in semitones (0..48), Feedback the resonance, a linear Mix */
    float phasVCenterNote = 64, phasVBlend = 1;      /* the sweep's centre as a MIDI note, and Blend 0..2 (the stage count: 4, 8, 12) */
    bool  chorVital = false;                 /* Vital's chorus module (processChorusVital): Mix an equal-power crossfade, Delay 1 / 2 exponential, Feedback bipolar, a band-pass in the loop */
    int   chorVoices = 1;                    /* its voice pairs, 1..4 */
    float chorVCutNote = 8, chorVSpread = 1; /* the loop filter: centre as a MIDI note, spread 0..1 (a radius of 96 notes at 1) */
    float flanMix = 0, flanRate = 0.5f, flanDepth = 0.5f, flanFb = 0, flanPhase = 0;
    float phasMix = 0, phasRate = 0.5f, phasDepth = 0.5f, phasDepth2 = 0, phasFreqHz = 300, phasFb = 0, phasPhase = 0;
    int   phasPoles = 4;
    /* Serum's HyperD ahead of the distortion: voices (1 is off), detune 0..1 (how far each voice's delay moves), rate 0..1 (how fast), wet 0..1. */
    int   hydVoices = 1;
    float hydDetune = 0, hydRate = 0.4f, hydWet = 0;
    /* Serum's Split / SplitMS: the stages in splitStages (1 HyperD, 2 Distortion, 4 Chorus / Flanger / Phaser, 32 Width, 64 Conv, 128 Reverb) work on one band only: 1 the low band below splitHz, 2 the high band
     * above it (a 4th-order Linkwitz-Riley crossover), 3 the Mid, 4 the Side. 0 is off. */
    int   splitBand = 0, splitStages = 0;
    float splitHz = 250;
};

namespace fxdetail {
/* sin(2 pi x) for x in cycles, any range: fold to a quarter wave and a 9th order polynomial (error 3e-9, under the float rounding). The library's sinf costs about
 * twenty times as much, and the chorus, flanger, phaser and widener call it per sample. */
/* log2 and exp2 for the compressors' per-sample dB maths (a log10 and a pow per band per sample): errors 2e-6 in log2 (6e-6 dB) and 1.2e-7 relative in exp2. */
inline float fastLog2(float x)                                          /* x a positive normal float */
{
    uint32_t u; std::memcpy(&u, &x, 4);
    int e = (int) ((u >> 23) & 255u) - 127;
    u = (u & 0x7fffffu) | 0x3f800000u;
    float m; std::memcpy(&m, &u, 4);
    if (m > 1.41421356f) { m *= 0.5f; e += 1; }
    const float t = m - 1.0f;
    return (float) e + t * (1.4427135f + t * (-0.7211319f + t * (0.47934815f + t * (-0.3674897f + t * (0.32215387f + t * -0.20659233f)))));
}
inline float fastExp2(float x)
{
    x = std::min(std::max(x, -126.0f), 126.0f);
    const float fl = std::floor(x), f = x - fl;
    const float p = f * (0.69315247f + f * (0.24015282f + f * (0.055835898f + f * (0.0089734188f + f * 0.0018852789f))));
    const uint32_t u = (uint32_t) ((int) fl + 127) << 23;
    float sc; std::memcpy(&sc, &u, 4);
    return sc * (1.0f + p);
}
inline float sinCyc(float x)
{
    const float r = x - std::floor(x + 0.5f);                          /* [-0.5, 0.5) */
    const float a = r > 0.25f ? 0.5f - r : (r < -0.25f ? -0.5f - r : r);
    const float t = 4.0f * a, t2 = t * t;
    return t * (1.5707963f + t2 * (-0.64596336f + t2 * (0.07968848f + t2 * (-0.0046722279f + t2 * 0.00015082053f))));
}
/* Reverb line lengths in samples at 44.1 kHz (mutually prime, 25..59 ms) and the input
 * diffusers. The output level kRevOut was measured against Serum's reverb. */
inline constexpr int kLineLen[8] = { 1087, 1283, 1481, 1699, 1907, 2131, 2347, 2593 };
inline constexpr int kApLen[4] = { 113, 197, 311, 431 };
inline constexpr float kApGain = 0.6f;
inline constexpr float kRevOut = 2.25f;          /* Serum's tail on a played note, held 1 s and released (calibration/dev/reverb_engine_only.py, 54 presets with a master reverb): with the older 1.50
 * (the steady-noise level, wet 100 = -0.5 dB re the dry) the engine's tail ran a median 2-5 dB under Serum's at 0.1-1.5 s after the release. At 2.25 the medians are -0.9 / -2.8 / -2.1 dB, and the
 * level the reverb adds to the held note stays within +0.3 dB of Serum's. 3.00 would centre them but put steady noise 5.5 dB hot (test_fx), so 2.25 is the compromise; the spread between presets (4-6 dB) is not a gain problem. */
inline constexpr float kDelayOut = 1.17f;        /* the echo of a noise burst at wet 100 against Serum's (the filter now acts on it, calibration/probe_delay_filter.py); was 0.94 with the filter on the repeats only */        /* a tap at wet 100: -0.5 dB, the energy of a 0.3 s burst through Serum's delay */
inline constexpr float kTiny = 1e-20f;           /* no longer used: sanitize() flushes feedback states instead */
inline constexpr float kTwoPi = 6.28318530718f;
inline constexpr float kWideGain = 2.5f;          /* full width reaches Serum's strongest chorus/Hyper, about -3 dB side re mid */
}

/* What the loop's normalisation leaves over, measured: steady noise at wet 100 through this
 * reverb was -10.4, -7.9, -7.9, -9.2 and -11.0 dB re the input at 0.4, 1, 2, 6 and 15 s, and
 * Serum's stays within +/-1.5 dB of 0. The gain that brings each to 0, interpolated in log T. */
inline float revLevel(float t60)
{
    static const float T[6] = { 0.4f, 1.0f, 2.0f, 6.0f, 15.0f, 30.0f };
    static const float dB[6] = { 9.9f, 7.4f, 7.4f, 8.7f, 10.5f, 11.5f };
    if (t60 <= T[0]) return std::pow(10.0f, dB[0] / 20.0f);
    int i = 0;
    while (i < 4 && t60 > T[i + 1]) i++;
    const float x = std::min((std::log(t60) - std::log(T[i])) / (std::log(T[i + 1]) - std::log(T[i])), 1.0f);
    return std::pow(10.0f, (dB[i] + x * (dB[i + 1] - dB[i])) / 20.0f);
}

/* The three modulated effects. Their wet signals were measured against Serum's (calibration/probe_modfx*.py): the chorus's four
 * voices are -3.5 dB re the input at Wet 100 before the filter (Serum's default Filt is a 12 dB/oct low-pass near 1 kHz, which is why its
 * total power reads -10 dB) and the mix follows the same law as the delay and reverb (-2.3 dB at 50 %); the flanger is -1.5 dB at Wet 100
 * and the phaser level with the input. Rates are in Hz, delays in ms. */
class ModFx {
public:
    static constexpr int kBuf = 4096;                                  /* 93 ms at 44.1 kHz, a power of two */

    explicit ModFx(float sampleRate = 44100.0f) : sr(sampleRate)
    {
        for (int c = 0; c < 2; c++) { chBuf[c].assign(kBuf, 0.0f); flBuf[c].assign(kBuf, 0.0f); }
    }

    void set(const FxSettings &s) { st = s; }
    bool active() const { return st.chorMix > 0 || st.flanMix > 0 || st.phasMix > 0 || chorCur > 0 || flanCur > 0 || phasCur > 0; }

    void process(float *L, float *R, int n)
    {
        if (st.chorMix > 0 || chorCur > 0) { if (st.chorVital) processChorusVital(L, R, n); else processChorus(L, R, n); chorRan = true; }
        else if (chorRan) { clearChorus(); chorRan = false; }
        if (st.flanMix > 0 || flanCur > 0) { if (st.flanVital) processFlangerVital(L, R, n); else processFlanger(L, R, n); flanRan = true; }
        else if (flanRan) { clearFlanger(); flanRan = false; }
        if (st.phasMix > 0 || phasCur > 0) { if (st.phasVital) processPhaserVital(L, R, n); else processPhaser(L, R, n); phasRan = true; }
        else if (phasRan) { clearPhaser(); phasRan = false; }
    }

private:
    static float tap(const std::vector<float> &b, int wp, float delaySamples)
    {
        const float d = std::min(std::max(delaySamples, 1.0f), (float) (kBuf - 3));
        const float rp = (float) wp - d + (float) kBuf;
        const int i0 = (int) rp;
        const float fr = rp - (float) i0;
        const float a = b[(size_t) (i0 & (kBuf - 1))], c = b[(size_t) ((i0 + 1) & (kBuf - 1))];
        return a + (c - a) * fr;
    }

    void clearChorus()
    {
        for (int c = 0; c < 2; c++) { std::fill(chBuf[c].begin(), chBuf[c].end(), 0.0f); chFb[c] = chLp[c] = chLp2[c] = 0.0f; }
        for (auto &l : vl) { if (!l.buf.empty()) std::fill(l.buf.begin(), l.buf.end(), 0.0f); l.lp = l.hp = 0.0f; l.fresh = true; }
        vWetPrev = 0.0f; vDryPrev = 1.0f; chorCur = 0.0f;
    }

    /* Vital's chorus, derived from Vital's synthesis/modules/chorus_module.cpp and (mono style) effects/delay.cpp, https://github.com/mtytel/vital, Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later (see THIRD-PARTY.md). Per block of 64 samples: the sweep phase moves on by rate x block, and each of the
     * 1..4 voice pairs has two taps (Delay 1, Delay 2) per side, the right side a quarter cycle on, the second tap half a cycle on, the voices spread by i / 4 / voices of a cycle. A tap's delay is the mix of its own
     * base and the two bases' mean (voice 0 its own, the last voice the mean) plus depth x 30 ms x (1 + sin / 2) (so depth also lengthens the delay). The delay's FREQUENCY chases its target with a 20 ms half-life.
     * Each line: read, write hardTanh(in + fb read) with a +-8 ceiling, through two one-poles whose difference is a band-pass (gain 1 + f_low / f_high), and the wet part is half the sum of the taps over all the voice pairs. */
    struct VLine { std::vector<float> buf; int wp = 0; float lp = 0, hp = 0, freq = 100, period = 100; bool fresh = true; };
    void processChorusVital(float *L, float *R, int n)
    {
        const int nv = std::min(std::max(st.chorVoices, 1), 4);
        const float depth = std::min(std::max(st.chorDepth, 0.0f), 1.0f) * 0.03f;
        const float fb = std::min(std::max(st.chorFb, -1.0f), 1.0f);
        const float w = std::min(std::max(st.chorMix, 0.0f), 1.0f);
        const float wetNew = std::sin(w * 1.5707963f), dryNew = std::cos(w * 1.5707963f);
        const float d1 = st.chorD1Ms * 0.001f, d2 = st.chorD2Ms * 0.001f, avg = 0.5f * (d1 + d2);
        const float minNy = 0.48f * sr;
        const float radius = std::max(st.chorVSpread * 96.0f, 0.0f);
        const float fLow = std::min(std::max(440.0f * std::pow(2.0f, (st.chorVCutNote + radius - 69.0f) / 12.0f), 1.0f), minNy);       /* the loop's first one-pole corner (Vital's low_frequency) */
        const float fHigh = std::min(std::max(440.0f * std::pow(2.0f, (st.chorVCutNote - radius - 69.0f) / 12.0f), 1.0f), minNy);      /* the corner of the second one-pole, which is subtracted */
        auto coef = [&](float f) { const float x = f * 3.14159265f / sr; return std::tan(x / (x + 1.0f)); };
        const float cLow = coef(fLow), cHigh = coef(fHigh), gain = fHigh / fLow + 1.0f;
        for (auto &l : vl) if (l.buf.empty()) l.buf.assign(kBuf, 0.0f);
        const float maxPeriod = (float) (kBuf - 4);
        int done = 0;
        while (done < n) {
            const int m = std::min(64, n - done);
            vPh += st.chorRate * (float) m / sr;
            vPh -= std::floor(vPh);
            const float decay = std::pow(0.5f, (float) m / (0.02f * sr));
            const float wetA = vWetPrev, dryA = vDryPrev, dW = (wetNew - wetA) / (float) m, dD = (dryNew - dryA) / (float) m;
            float per0[4][2][2], per1[4][2][2];
            for (int i = 0; i < nv; i++) {
                const float pairOff = (float) i * 0.25f / (float) nv, t = i > 0 ? (float) i / ((float) nv - 1.0f) : 0.0f;
                for (int k = 0; k < 2; k++)
                    for (int c = 0; c < 2; c++) {
                        VLine &l = vl[(i * 2 + k) * 2 + c];
                        const float ph = vPh + (c ? 0.25f : 0.0f) + (k ? 0.5f : 0.0f) + pairOff;
                        const float mod = std::sin(ph * 6.2831853f) * 0.5f + 1.0f;
                        const float own = k ? d2 : d1;
                        const float base = own + (avg - own) * t;
                        const float target = 1.0f / std::max(0.00001f, mod * depth + base);
                        if (l.fresh) { l.freq = target; l.period = std::min(std::max(sr / target, 3.0f), maxPeriod); l.fresh = false; }
                        l.freq = target + decay * (l.freq - target);
                        const float p = std::min(std::max(sr / l.freq, 3.0f), maxPeriod);
                        per0[i][k][c] = l.period; per1[i][k][c] = 0.5f * (l.period + p);
                        l.period = per1[i][k][c];
                    }
            }
            for (int s = 0; s < m; s++) {
                const float fr = (float) (s + 1) / (float) m;
                const float wetG = wetA + dW * (float) (s + 1), dryG = dryA + dD * (float) (s + 1);
                float *io[2] = { &L[done + s], &R[done + s] };
                for (int c = 0; c < 2; c++) {
                    const float x = *io[c];
                    float acc = 0.0f;
                    for (int i = 0; i < nv; i++)
                        for (int k = 0; k < 2; k++) {
                            VLine &l = vl[(i * 2 + k) * 2 + c];
                            const float per = per0[i][k][c] + (per1[i][k][c] - per0[i][k][c]) * fr;
                            float *const b = l.buf.data();
                            const float rp = (float) l.wp - per + (float) kBuf;
                            const int i1 = (int) rp;
                            const float f = rp - (float) i1;
                            const float y0 = b[(size_t) ((i1 - 1) & (kBuf - 1))], y1 = b[(size_t) (i1 & (kBuf - 1))];
                            const float y2 = b[(size_t) ((i1 + 1) & (kBuf - 1))], y3 = b[(size_t) ((i1 + 2) & (kBuf - 1))];
                            const float c1 = 0.5f * (y2 - y0), c2 = y0 - 2.5f * y1 + 2.0f * y2 - 0.5f * y3, c3 = 0.5f * (y3 - y0) + 1.5f * (y1 - y2);
                            const float rd = ((c3 * f + c2) * f + c1) * f + y1;
                            acc += 0.5f * rd;
                            float wr = (x + rd * fb) * 0.125f;
                            wr = std::min(std::max(wr, -1.0f), 1.0f) * 8.0f;                                                     /* hardTanh(x / 8) * 8 */
                            float d = cLow * (wr * gain - l.lp); l.lp += d; const float lo = l.lp; l.lp += d;                  /* Vital's one-pole (tickBasic) */
                            d = cHigh * (lo - l.hp); l.hp += d; const float hi = l.hp; l.hp += d;
                            b[(size_t) l.wp] = sanitize(lo - hi);
                            l.wp = (l.wp + 1) & (kBuf - 1);
                        }
                    *io[c] = dryG * x + wetG * acc;
                }
            }
            vWetPrev = wetNew; vDryPrev = dryNew;
            done += m;
        }
        chorCur = w;
    }
    void clearFlanger()
    {
        for (int c = 0; c < 2; c++) { std::fill(flBuf[c].begin(), flBuf[c].end(), 0.0f); flFb[c] = 0.0f; vfl[c].fresh = true; if (!vfl[c].buf.empty()) std::fill(vfl[c].buf.begin(), vfl[c].buf.end(), 0.0f); }
        vFlWetPrev = 0.0f; vFlDryPrev = 1.0f; flanCur = 0.0f;
    }
    void clearPhaser()
    {
        for (int c = 0; c < 2; c++) { for (int k = 0; k < kMaxPoles; k++) apX[c][k] = apY[c][k] = 0.0f; phFb[c] = 0.0f; for (float &x : vphSt[c]) x = 0.0f; vphLows[c] = vphHighs[c] = vphOut[c] = 0.0f; }
        phasCur = 0.0f;
    }

    /* Four voices: two per side, the left pair half a cycle apart and the right pair a quarter cycle behind them, so the sides
     * decorrelate (Serum's default chorus measures a left/right correlation of 0). */
    void processChorus(float *L, float *R, int n)
    {
        const float target = st.chorMix, dg = (target - chorCur) / (float) n;
        const float dph = st.chorRate / sr, swing = st.chorDepth * 0.005f * sr;
        const float d1 = st.chorD1Ms * 0.001f * sr, d2 = st.chorD2Ms * 0.001f * sr;
        /* two one-poles make the 12 dB/oct filter; each sits at fc / 0.644 so the pair is 3 dB down at fc */
        const float a = 1.0f - std::exp(-fxdetail::kTwoPi * std::min(st.chorFiltHz / 0.644f, 0.45f * sr) / sr);
        const float fb = std::min(st.chorFb, 0.8f), voice = 0.50f;
        static const float off[2][2] = { { 0.0f, 0.5f }, { 0.25f, 0.75f } };
        for (int i = 0; i < n; i++) {
            chorCur += dg;
            float *io[2] = { &L[i], &R[i] };
            for (int c = 0; c < 2; c++) {
                const float x = *io[c];
                chBuf[c][(size_t) chWp] = x + fb * chFb[c];
                const float t0 = tap(chBuf[c], chWp, d1 + swing * fxdetail::sinCyc(chPh + off[c][0]));
                const float t1 = tap(chBuf[c], chWp, d2 + swing * fxdetail::sinCyc(chPh + off[c][1]));
                const float wet = voice * (t0 + t1);
                chLp[c] = sanitize(chLp[c] + a * (wet - chLp[c]));
                chLp2[c] = sanitize(chLp2[c] + a * (chLp[c] - chLp2[c]));
                const float wf = st.chorHpf ? wet - chLp2[c] : chLp2[c];
                chFb[c] = sanitize(wf);
                *io[c] = x * dryGain(chorCur) + wf * wetGain(chorCur);
            }
            chWp = (chWp + 1) & (kBuf - 1);
            chPh += dph;
            if (chPh >= 1.0f) chPh -= 1.0f;
        }
        chorCur = target;
    }

    /* Vital's flanger, derived from Vital's synthesis/modules/flanger_module.cpp and (unfiltered style) effects/delay.cpp, https://github.com/mtytel/vital, Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later
     * (see THIRD-PARTY.md). Per block of 64 samples: the sweep phase advances, the left side's phase is minus half the Phase knob, the right side's plus half; a triangle (0..1 over a cycle) scales the delay
     * (1 / the centre note's frequency less 0.5 ms) by 1 + depth (2 tri - 1), and 0.5 ms is added back; the delay's FREQUENCY chases its target with a 20 ms half-life; the line writes hardTanh(in + fb read). */
    struct VFlLine { std::vector<float> buf; int wp = 0; float freq = 100, period = 100; bool fresh = true; };
    static float vtri(float x) { x -= std::floor(x); return 1.0f - std::fabs(2.0f * x - 1.0f); }
    void processFlangerVital(float *L, float *R, int n)
    {
        const float depth = std::min(std::max(st.flanDepth, 0.0f), 1.0f);
        const float fb = std::min(std::max(st.flanFb, -1.0f), 1.0f);
        const float w = std::min(std::max(st.flanMix, 0.0f), 1.0f);
        const float wetNew = std::sin(w * 1.5707963f), dryNew = std::cos(w * 1.5707963f);
        const float off = std::min(std::max(st.flanPhase, 0.0f), 1.0f);
        const float centerDelay = 1.0f / (440.0f * std::pow(2.0f, (st.flanVCenterNote - 69.0f) / 12.0f));
        const float maxPeriod = (float) (kBuf - 4);
        for (auto &l : vfl) if (l.buf.empty()) l.buf.assign(kBuf, 0.0f);
        int done = 0;
        while (done < n) {
            const int m = std::min(64, n - done);
            vFlPh += st.flanRate * (float) m / sr;
            vFlPh -= std::floor(vFlPh);
            const float decay = std::pow(0.5f, (float) m / (0.02f * sr));
            const float wetA = vFlWetPrev, dryA = vFlDryPrev, dW = (wetNew - wetA) / (float) m, dD = (dryNew - dryA) / (float) m;
            float per0[2], per1[2];
            for (int c = 0; c < 2; c++) {
                VFlLine &l = vfl[c];
                const float ph = vFlPh - off * 0.5f + (c ? off : 0.0f);
                const float mod = depth * (vtri(ph) * 2.0f - 1.0f) + 1.0f;
                const float delay = (centerDelay - 0.0005f) * mod + 0.0005f;
                const float target = 1.0f / std::max(delay, 1.0f / 20000.0f);
                if (l.fresh) { l.freq = target; l.period = std::min(std::max(sr / target, 3.0f), maxPeriod); l.fresh = false; }
                l.freq = target + decay * (l.freq - target);
                const float p = std::min(std::max(sr / l.freq, 3.0f), maxPeriod);
                per0[c] = l.period; per1[c] = 0.5f * (l.period + p); l.period = per1[c];
            }
            for (int s = 0; s < m; s++) {
                const float fr = (float) (s + 1) / (float) m;
                const float wetG = wetA + dW * (float) (s + 1), dryG = dryA + dD * (float) (s + 1);
                float *io[2] = { &L[done + s], &R[done + s] };
                for (int c = 0; c < 2; c++) {
                    VFlLine &l = vfl[c];
                    const float x = *io[c];
                    const float per = per0[c] + (per1[c] - per0[c]) * fr;
                    float *const b = l.buf.data();
                    const float rp = (float) l.wp - per + (float) kBuf;
                    const int i1 = (int) rp;
                    const float f = rp - (float) i1;
                    const float y0 = b[(size_t) ((i1 - 1) & (kBuf - 1))], y1 = b[(size_t) (i1 & (kBuf - 1))];
                    const float y2 = b[(size_t) ((i1 + 1) & (kBuf - 1))], y3 = b[(size_t) ((i1 + 2) & (kBuf - 1))];
                    const float c1 = 0.5f * (y2 - y0), c2 = y0 - 2.5f * y1 + 2.0f * y2 - 0.5f * y3, c3 = 0.5f * (y3 - y0) + 1.5f * (y1 - y2);
                    const float rd = ((c3 * f + c2) * f + c1) * f + y1;
                    b[(size_t) l.wp] = sanitize(std::min(std::max(x + rd * fb, -1.0f), 1.0f));
                    l.wp = (l.wp + 1) & (kBuf - 1);
                    *io[c] = dryG * x + wetG * 0.618f * rd;       /* measured on the plugin: the delayed part reaches the output 4.2 dB under the input at Wet 100 (dry 1, coherent sum 1 + 0.618 s at the other mixes), feedback unchanged */
                }
            }
            vFlWetPrev = wetNew; vFlDryPrev = dryNew;
            done += m;
        }
        flanCur = w;
    }

    /* Vital's phaser, derived from Vital's synthesis/effects/phaser.cpp and filters/phaser_filter.cpp (same notice). The cutoff note is centre + depth x a triangle (-1..1) per sample; the left side's phase is
     * a quarter of the Phase knob behind and the right side's ahead; the filter is 4 / 8 / 12 first-order all-pass stages (Blend 0 / 1 / 2 picks which tap), fed the input (drive 1 + resonance / 2) plus the tanh of the
     * resonance times the band left after removing the lows and highs of the last all-pass output; the wet is half the sum of the input and the all-pass output, mixed LINEARLY with the input. */
    void processPhaserVital(float *L, float *R, int n)
    {
        const float depthSemis = std::min(std::max(st.phasDepth, 0.0f), 1.0f) * 48.0f;
        const float res = std::min(std::max(st.phasFb, 0.0f), 1.0f);
        const float drive = res * 0.5f + 1.0f;
        const float blend = std::min(std::max(st.phasVBlend, 0.0f), 2.0f);
        const float p1 = std::min(std::max(1.0f - blend, 0.0f), 1.0f), p5 = std::min(std::max(blend - 1.0f, 0.0f), 1.0f), p3 = 1.0f - p1 - p5;
        const float off = std::min(std::max(st.phasPhase, 0.0f), 1.0f);
        const float mixNew = std::min(std::max(st.phasMix, 0.0f), 1.0f);
        const float mixA = vPhMixPrev, dMix = (mixNew - mixA) / (float) n;
        const float dph = st.phasRate / sr;
        auto coef = [&](float hz) { const float x = 3.14159265f * std::min(hz / sr, 1.0f); return std::tan(x / (x + 1.0f)); };
        for (int i = 0; i < n; i++) {
            const float mixG = mixA + dMix * (float) (i + 1);
            float *io[2] = { &L[i], &R[i] };
            for (int c = 0; c < 2; c++) {
                const float ph = vPhPh + (c ? 0.25f : -0.25f) * off;
                const float mod = vtri(ph) * 2.0f - 1.0f;
                const float note = st.phasVCenterNote + mod * depthSemis;
                const float cf = coef(440.0f * std::pow(2.0f, (note - 69.0f) / 12.0f));
                const float x = *io[c];
                float *S = vphSt[c];
                auto one = [&](float &s, float in, float cc) { const float d = cc * (in - s); s += d; const float o = s; s += d; return o; };
                const float lows = one(vphLows[c], vphOut[c], std::min(cf * 20.0f, 0.9f));
                const float highs = one(vphHighs[c], lows, cf * (1.0f / 20.0f));
                const float fs = std::tanh(res * (lows - highs));
                float a = drive * x + fs;
                for (int k = 0; k < 4; k++) { const float so = one(S[k], a, cf); a -= 2.0f * so; }
                const float o1 = a;
                for (int k = 4; k < 8; k++) { const float so = one(S[k], a, cf); a -= 2.0f * so; }
                const float o3 = a;
                for (int k = 8; k < 12; k++) { const float so = one(S[k], a, cf); a -= 2.0f * so; }
                const float o5 = a;
                vphOut[c] = sanitize(p1 * o1 + p3 * o3 + p5 * o5);
                const float wet = (x + vphOut[c]) * 0.5f;
                *io[c] = x + (wet - x) * mixG;
            }
            vPhPh += dph;
            if (vPhPh >= 1.0f) vPhPh -= std::floor(vPhPh);
        }
        vPhMixPrev = mixNew;
        phasCur = mixNew;
    }

    /* A short swept delay added to the signal (the comb), with feedback; the stereo phase offsets the right side's sweep. */
    void processFlanger(float *L, float *R, int n)
    {
        const float target = st.flanMix, dg = (target - flanCur) / (float) n;
        const float dph = st.flanRate / sr;
        const float base = 0.0003f * sr, span = 0.004f * sr, fb = std::min(st.flanFb, 0.8f);
        for (int i = 0; i < n; i++) {
            flanCur += dg;
            float *io[2] = { &L[i], &R[i] };
            for (int c = 0; c < 2; c++) {
                const float x = *io[c];
                const float lfo = 0.5f + 0.5f * fxdetail::sinCyc(flPh + (c ? st.flanPhase : 0.0f));
                flBuf[c][(size_t) flWp] = x + fb * flFb[c];
                const float d = tap(flBuf[c], flWp, base + span * st.flanDepth * lfo);
                flFb[c] = sanitize(d);
                const float wet = 0.6f * (x + d);
                *io[c] = x * dryGain(flanCur) + wet * wetGain(flanCur);
            }
            flWp = (flWp + 1) & (kBuf - 1);
            flPh += dph;
            if (flPh >= 1.0f) flPh -= 1.0f;
        }
        flanCur = target;
    }

    /* A chain of first-order all-passes swept in frequency, with feedback. Depth 2 spreads the stages apart. */
    void processPhaser(float *L, float *R, int n)
    {
        static constexpr int kStep = 16;
        const float target = st.phasMix, dg = (target - phasCur) / (float) n;
        const float dph = st.phasRate / sr, fb = std::min(st.phasFb, 0.8f);
        const int poles = std::min(std::max(st.phasPoles, 2), kMaxPoles);
        for (int i = 0; i < n; i++) {
            if ((i & (kStep - 1)) == 0) {
                for (int c = 0; c < 2; c++) {
                    const float lfo = fxdetail::sinCyc(phPh + (c ? st.phasPhase : 0.0f));
                    const float f = st.phasFreqHz * std::exp2(2.5f * st.phasDepth * lfo);
                    for (int k = 0; k < poles; k++) {
                        const float fk = std::min(std::max(f * std::exp2(((float) k - 0.5f * (float) (poles - 1)) * st.phasDepth2 * 0.35f), 20.0f), 0.45f * sr);
                        const float t = std::tan(3.14159265f * fk / sr);
                        apA[c][k] = (t - 1.0f) / (t + 1.0f);
                    }
                }
            }
            phasCur += dg;
            float *io[2] = { &L[i], &R[i] };
            for (int c = 0; c < 2; c++) {
                const float x = *io[c];
                float v = x + fb * phFb[c];
                for (int k = 0; k < poles; k++) {
                    const float y = apA[c][k] * v + apX[c][k] - apA[c][k] * apY[c][k];
                    apX[c][k] = v; apY[c][k] = sanitize(y);
                    v = y;
                }
                phFb[c] = sanitize(v);
                const float wet = 0.57f * (x + v);
                *io[c] = x * dryGain(phasCur) + wet * wetGain(phasCur);
            }
            phPh += dph;
            if (phPh >= 1.0f) phPh -= 1.0f;
        }
        phasCur = target;
    }

    static constexpr int kMaxPoles = 12;
    float sr;
    FxSettings st;
    std::vector<float> chBuf[2], flBuf[2];
    int chWp = 0, flWp = 0;
    float chPh = 0, flPh = 0, phPh = 0;
    float chFb[2] = {}, chLp[2] = {}, chLp2[2] = {}, flFb[2] = {}, phFb[2] = {};
    float apA[2][kMaxPoles] = {}, apX[2][kMaxPoles] = {}, apY[2][kMaxPoles] = {};
    float chorCur = 0, flanCur = 0, phasCur = 0;
    bool chorRan = false, flanRan = false, phasRan = false;
    VLine vl[16];
    float vPh = 0, vWetPrev = 0, vDryPrev = 1;
    VFlLine vfl[2];
    float vFlPh = 0, vFlWetPrev = 0, vFlDryPrev = 1;
    float vphSt[2][12] = {}, vphLows[2] = {}, vphHighs[2] = {}, vphOut[2] = {}, vPhPh = 0, vPhMixPrev = 0;
};

/* ------------------------------------------------------------------ HyperD ahead of the distortion */

/* Serum's HyperD (Hyper/Dimension) as the first stage of the block, where the distortion that follows clips the moving cloud together (Lead - Other Side and
 * Lead - Rollin have it in front of a drive-98 distortion; without it the clipped tone is a steady square-like wave where Serum's breathes and has even harmonics).
 * Measured on Serum (calibration/hyperd_sine.py, hyperd_fit.py): each voice is a copy of the signal on a delay line moved by a TRIANGLE wave, so for seconds at a time it is a
 * pitch-shifted copy at a fixed offset (a line a few Hz from the note, flipping sign at each vertex) rather than a wobble; the swing is linear in Detune (20 ms peak to peak at 100 %) and the
 * triangle's frequency grows as Rate squared (0.25 Hz at 50 %); the voices differ in phase and speed, are not normalised by their number (each is at unity: a 7 voice cloud is
 * +8 dB), and are mixed with the dry signal by Wet through the effects' smoothstep law. */
class Hyper {
public:
    static constexpr int kBuf = 8192;                  /* 186 ms at 44.1 kHz, a power of two */
    static constexpr int kMaxV = 8;

    explicit Hyper(float sampleRate = 44100.0f) : sr(sampleRate)
    {
        const float w0 = 2.0f * 3.14159265f * kHpHz / sr, cs = std::cos(w0), al = std::sin(w0) / (2.0f * 0.70710678f), a0 = 1.0f + al;      /* RBJ high-pass, Q 0.707 */
        hpB0 = (1.0f + cs) * 0.5f / a0; hpB1 = -(1.0f + cs) / a0; hpB2 = hpB0; hpA1 = -2.0f * cs / a0; hpA2 = (1.0f - al) / a0;
        for (int c = 0; c < 2; c++) buf[c].assign(kBuf, 0.0f);
        unsigned s = 12345u;
        auto rnd = [&s]() { s = s * 1664525u + 1013904223u; return (float) (s >> 8) * (1.0f / 16777216.0f); };
        for (int c = 0; c < 2; c++)
            for (int v = 0; v < kMaxV; v++) {
                ph[c][v] = rnd();
                spd[c][v] = kSpeedLo + (kSpeedHi - kSpeedLo) * rnd();
            }
    }

    void set(const FxSettings &s) { st = s; }
    bool active() const { return (st.hydVoices > 1 && st.hydWet > 0.0f) || cur > 0.0f; }

    void process(float *L, float *R, int n)
    {
        const bool on = st.hydVoices > 1 && st.hydWet > 0.0f;
        const float target = on ? st.hydWet : 0.0f, dg = (target - cur) / (float) n;
        const int nv = std::min(std::max(st.hydVoices, 1), kMaxV);
        const float swing = kDepthMs * 0.001f * sr * std::pow(st.hydDetune, kDepthExp);            /* peak to peak, in samples */
        const float rateHz = kRateHz * std::pow(2.0f * st.hydRate, kRateExp);
        const float voiceGain = kVoiceGain * std::pow((float) nv / kVoicesRef, kVoicesExp);        /* Serum's cloud grows a little slower than the number of voices (sine, 2 to 8 voices) */
        /* Per block, not per sample: each cloud voice's phase step (the same expression the sample loop had), half the swing, and the buffers' base pointers. A phase is never
         * negative and a step is far under one cycle, so the wrap is one compare and a subtract, which is what floor() gave. */
        float inc[2][kMaxV];
        for (int c = 0; c < 2; c++) for (int v = 0; v < nv; v++) inc[c][v] = rateHz * spd[c][v] / sr;
        const float halfSwing = 0.5f * swing;
        for (int i = 0; i < n; i++) {
            cur += dg;
            float *io[2] = { &L[i], &R[i] };
            const float dryG = dryGain(cur), wetG = wetGain(cur);
            for (int c = 0; c < 2; c++) {
                const float x = *io[c];
                float *const bc = buf[c].data();
                bc[(size_t) wp] = x;
                float acc = 0.0f;
                for (int v = 0; v < nv; v++) {
                    const float tri = 4.0f * std::fabs(ph[c][v] - 0.5f) - 1.0f;                       /* +1 at phase 0, -1 at 0.5 */
                    const float d = 2.0f + halfSwing * (1.0f + tri);
                    const float rp = (float) wp - d + (float) kBuf;
                    const int i1 = (int) rp;
                    const float fr = rp - (float) i1;
                    const float y0 = bc[(size_t) ((i1 - 1) & (kBuf - 1))], y1 = bc[(size_t) (i1 & (kBuf - 1))];
                    const float y2 = bc[(size_t) ((i1 + 1) & (kBuf - 1))], y3 = bc[(size_t) ((i1 + 2) & (kBuf - 1))];
                    const float c1 = 0.5f * (y2 - y0), c2 = y0 - 2.5f * y1 + 2.0f * y2 - 0.5f * y3, c3 = 0.5f * (y3 - y0) + 1.5f * (y1 - y2);
                    acc += ((c3 * fr + c2) * fr + c1) * fr + y1;
                    float p = ph[c][v] + inc[c][v];
                    if (p >= 1.0f) p -= std::floor(p);
                    ph[c][v] = p;
                }
                /* Serum's wet path has no bass: a 12 dB/oct high-pass near 150 Hz (white noise through the cloud, third-octave gain re the dry: -2 dB at 159 Hz, -7 at 126, -14 at 63, -20 at 50) */
                const float hp = hpB0 * acc + hpS1[c];
                hpS1[c] = hpB1 * acc - hpA1 * hp + hpS2[c];
                hpS2[c] = hpB2 * acc - hpA2 * hp;
                *io[c] = x * dryG + hp * voiceGain * wetG;
            }
            wp = (wp + 1) & (kBuf - 1);
        }
        cur = target;
        if (!on && cur <= 0.0f) { for (int c = 0; c < 2; c++) std::fill(buf[c].begin(), buf[c].end(), 0.0f); }
    }

private:
    /* fitted to Serum's HyperD alone on a sine and a saw (calibration/dev/hyperd_fit.py) */
    static constexpr float kDepthMs = 20.0f, kDepthExp = 1.0f, kRateHz = 0.25f, kRateExp = 1.8f, kSpeedLo = 0.3f, kSpeedHi = 1.8f, kVoiceGain = 0.95f, kVoicesRef = 7.0f, kVoicesExp = 0.3f, kHpHz = 150.0f;
    float sr;
    FxSettings st;
    std::vector<float> buf[2];
    int wp = 0;
    float cur = 0.0f;
    float spd[2][kMaxV] = {};
    float ph[2][kMaxV] = {};
    float hpB0 = 1, hpB1 = 0, hpB2 = 0, hpA1 = 0, hpA2 = 0, hpS1[2] = {}, hpS2[2] = {};
};

class Fx {
public:
    static constexpr int kLines = 8;
    struct DelayUnit { std::vector<float> buf[2]; int pos = 0; float wetCur = 0.0f, dryCur = 1.0f; float timeCur[2] = { 0, 0 }; float fbLp[2] = { 0, 0 }, fbHp[2] = { 0, 0 }; bool ran = false; };
    struct DelayPrm { float mix, ms, rms, fb, lo, hi; int ping; };
    static constexpr int kDelayMax = 1 << 17;       /* 2.97 s at 44.1 kHz */
    static constexpr int kChorusMax = 2048;         /* 46 ms */
    static constexpr int kAps = 4;

    explicit Fx(float sampleRate = 44100.0f) : sr(sampleRate)
    {
        chorusBuf.assign(kChorusMax, 0.0f);
        for (auto &d : dly) { d.buf[0].assign(kDelayMax, 0.0f); d.buf[1].assign(kDelayMax, 0.0f); }
        const float scale = sr / 44100.0f;
        for (int i = 0; i < kLines; i++) {
            lines[i].len = std::max(16, (int) (fxdetail::kLineLen[i] * scale));
            lines[i].buf.assign((size_t) lines[i].len, 0.0f);
        }
        for (int i = 0; i < kAps; i++)
            aps[i].buf.assign((size_t) std::max(8, (int) (fxdetail::kApLen[i] * scale)), 0.0f);
    }

    /* True if any stage would change the signal: the engine skips process() otherwise. */
    bool active() const { return anyActive || (st.convMix > 0.0f && convP) || convWetCur > 0.0f || convRan; }

    /* The convolver's plan (conv.h), built by the loader: the audio thread only swaps the pointer. A new plan starts from silence. */
    void setConvPlan(const std::shared_ptr<ConvPlan> &p)
    {
        if (p == convP) return;
        convP = p;
        if (convP) convP->reset();
        convWetCur = 0.0f; convDryCur = 1.0f; convRan = false;
    }

    /* The stage order as a lexicographic permutation rank over the default list (0 dist, 1 Filter FX, 2 comp, 3 chorus / flanger / phaser, 4 delay, 5 conv and reverb, 6 EQ). */
    void setFxOrder(int rank)
    {
        rank = std::min(std::max(rank, 0), 5039);
        int pool[7] = { 0, 1, 2, 3, 4, 5, 6 }, left = 7, f = 720;
        for (int k = 0; k < 7; k++) {
            const int d = rank / f;
            rank %= f;
            fxSeq[k] = pool[d];
            for (int j = d; j < left - 1; j++) pool[j] = pool[j + 1];
            left--;
            if (k < 6) f /= (6 - k);
        }
    }

    void set(const FxSettings &s)
    {
        st = s;
        setFxOrder(s.fxOrder);
        distMixBase = s.distMix; distWetModSeen = 0.0f;
        compMixBase = s.compMix; compWetModSeen = 0.0f;
        if (s.distVital && s.distFlt) distVf.set(s.distFltNote, s.distFltRes, s.distFltBlend, sr);
        distGainNow = s.distGain;
        for (float &x : distVModSeen) x = 0.0f;
        if (s.distPre) distPreBq.c.set(s.distPre == 1 ? EQ_LOPASS : EQ_HIPASS, s.distPreHz, 0.0f, s.distPreQ, sr);
        mod.set(s);
        hyper.set(s);
        if (s.splitBand == 1 || s.splitBand == 2) {
            for (int i = 0; i < 2; i++) { spLp[i].set(false, s.splitHz, sr); spHp[i].set(true, s.splitHz, sr); }
        }
        eqTarget = s.eqEnable ? 1.0f : 0.0f;
        distTarget = s.distVital ? (s.distMix > 0.0f ? 1.0f : 0.0f) : s.distCal ? (s.distMix > 0.0f || std::fabs(s.distLevel - 1.0f) > 1e-3f ? 1.0f : 0.0f) : (s.distDrive > 0.0f ? s.distMix : 0.0f);
        compTarget = (s.compRatio > 1.001f || s.compMulti) ? s.compMix : 0.0f;
        compDet = 1.0f - std::exp(-1.0f / (0.020f * sr));                       /* 20 ms rms window */
        compAtt = std::exp(-1.0f / (std::max(s.compAttackMs, 0.05f) * 0.001f * sr));
        compRel = std::exp(-1.0f / (std::max(s.compReleaseMs, 1.0f) * 0.001f * sr));
        compMk = std::pow(10.0f, s.compMakeupDb / 20.0f);
        if (s.compVital) {
            vcSplitA.set(120.0f, sr); vcSplitB[0].set(2500.0f, sr); vcSplitB[1].set(2500.0f, sr);
            for (int b = 0; b < 3; b++) vcBand[b].setup(s, b, sr);
        }
        if (s.compMulti) {                                   /* Linkwitz-Riley 4th order crossovers at 120 Hz and 2.4 kHz */
            for (int i = 0; i < 2; i++) {
                mbLp2[i].set(false, 2400.0f, sr); mbHp2[i].set(true, 2400.0f, sr);
                mbLp1[i].set(false, 120.0f, sr);  mbHp1[i].set(true, 120.0f, sr);
                mbApLp[i].set(false, 120.0f, sr); mbApHp[i].set(true, 120.0f, sr);   /* the low crossover's phase on the high band, so the bands sum flat */
            }
        }
        bool eqAny = false;
        for (float &x : eqModSeen) x = 1e9f;
        for (int b = 0; b < 4; b++) {
            eqOn[b] = s.band[b].type != EQ_OFF;
            if (eqOn[b]) {
                eq[b].c.set(s.band[b].type, s.band[b].hz, s.band[b].gainDb, s.band[b].q, sr);
                eqAny = true;
            } else {
                eq[b].clear();
            }
        }
        msAny = false;
        for (int k = 0; k < 2; k++)
            for (int b = 0; b < 4; b++) {
                msOn[k][b] = s.msBand[k][b].type != EQ_OFF;
                if (msOn[k][b]) {
                    msEq[k][b].c.set(s.msBand[k][b].type, s.msBand[k][b].hz, s.msBand[k][b].gainDb, s.msBand[k][b].q, sr);
                    msAny = true;
                } else {
                    msEq[k][b].clear();
                }
            }
        bandEq = (s.splitBand == 1 || s.splitBand == 2) && (s.splitStages & 16) && msAny;      /* the Mid-EQ bands are the Split band's own EQ (split_stages bit 16) */
        msMaster = msAny && !bandEq;
        eqAny = eqAny || msAny;
        /* A stage still fading out has to keep running, or it would cut off. */
        baseActive = eqAny || distTarget > 0.0f || distCur > 0.0f || compTarget > 0.0f || compCur > 0.0f || s.wideMix > 0.0f || s.delayMix > 0.0f || s.delay2Mix > 0.0f || s.revMix > 0.0f ||
                    wideCur > 0.0f || dly[0].wetCur > 0.0f || dly[1].wetCur > 0.0f || revWetCur > 0.0f || wideRan || dly[0].ran || dly[1].ran || revRan || mod.active() || hyper.active();
        anyActive = baseActive || xfOn;
    }

    /* Routes from an envelope or LFO to the master EQ's bands. Serum's Gain knob spans 48 dB and a route adds 48 dB x (depth x source) to it; the Freq knob is a log scale from
     * 21.5 Hz to 20 kHz, so a route adds that fraction of the span (ln 928.8) to the log frequency (calibration/dev/probe_fx_macro.py eq: +10 % = +0.99 octave). m holds a
     * frequency offset then a gain offset for each of the four bands (the knob fractions the voice's routes sum to). Coefficients are redone only for a band whose offsets moved. */
    void setEqMod(const float *m)
    {
        for (int b = 0; b < 4; b++) {
            if (!eqOn[b]) continue;
            const float f = m[b * 2], g = m[b * 2 + 1];
            if (f == eqModSeen[b * 2] && g == eqModSeen[b * 2 + 1]) continue;
            eqModSeen[b * 2] = f; eqModSeen[b * 2 + 1] = g;
            const auto &bd = st.band[b];
            const bool cut = eqIsCut(bd.type);
            const float hz = std::min(std::max(bd.hz * std::exp(f * 6.8342f), 10.0f), 20000.0f);
            const float gain = cut ? 0.0f : std::min(std::max(bd.gainDb + g * 48.0f, -24.0f), 24.0f);
            eq[b].c.set(bd.type, hz, gain, bd.q, sr);
        }
    }

    /* The Filter FX: a filter after the distortion, its cutoff set by the engine each block (it follows the filter envelope).
     * type is the voice filter's list (LP/HP/BP/notch, 12 and 24 dB). */
    bool filterOn() const { return xfOn; }

    /* The Filter FX in one of Vital's own models (1 Analog, 2 Ladder, 3 Dirty, 4 Digital, 5 Diode; 0 keeps the biquad): called after setFilter each block. */
    void setFilterModel(int model, float blend, bool steep, int cstyle = 0, float ctrans = 0.0f, int skStyle = 0)
    {
        xfVm = (xfOn && model >= 1 && model <= 6) ? model : 0;
        xfCstyle = cstyle; xfCtrans = ctrans; xfBlend = blend;
        if (!xfVm) return;
        const float g = xfDriveT;
        xfSk.on = xfLd.on = xfDt.on = xfDg.on = xfDo.on = false;
        switch (xfVm) {
        case 1: xfSk.setSampleRate(sr); xfSk.set(xfHz, xfRes, g, blend, skStyle >= 2 ? skStyle : (steep ? 1 : 0)); break;
        case 2: xfLd.setSampleRate(sr); xfLd.set(xfHz, xfRes, g, blend, steep); break;
        case 3: xfDt.setSampleRate(sr); xfDt.set(xfHz, xfRes, g, blend, steep); break;
        case 4: xfDg.setSampleRate(sr); xfDg.set(xfHz, xfRes, g, blend, steep); break;
        case 6: if (xfCombSr != sr) { xfComb.setSampleRate(sr); xfCombSr = sr; } break;
        default: xfDo.setSampleRate(sr); xfDo.set(xfHz, xfRes, g, blend, steep); break;
        }
    }
    void runFilterModel(float *L, float *R, int n)
    {
        switch (xfVm) {
        case 1: xfSk.process(L, R, n); break;
        case 2: xfLd.process(L, R, n); break;
        case 3: xfDt.process(L, R, n); break;
        case 4: xfDg.process(L, R, n); break;
        case 6: xfComb.processVital(L, R, n, xfCstyle % 3, xfRes, xfHz, xfBlend, xfCtrans, xfCstyle >= 3); break;
        default: xfDo.process(L, R, n); break;
        }
    }

    /* Routes from an LFO or envelope to the Vital distortion's Drive (a fraction of its 60 dB), and its filter's Cutoff (a fraction of the 128 notes), Res (of 0..1) and Blend (of 0..2).
     * m holds the four offsets the newest voice's routes sum to; the gain and the filter's coefficients are redone only when one moved. */
    void setVitalDistMod(const float *m)
    {
        if (!st.distVital) return;
        bool moved = false;
        for (int i = 0; i < 4; i++) if (m[i] != distVModSeen[i]) { distVModSeen[i] = m[i]; moved = true; }
        if (!moved) return;
        applyVitalDist(m);
    }
    /* The audio-rate LFO's frequency: the newest note's, set once per block. */
    void setVitalAudioNote(float note) { distAudioHz = 440.0f * std::pow(2.0f, (note + st.distAudioTrans - 69.0f) / 12.0f); }
    void applyVitalDist(const float *m, bool gain = true, bool filter = true)
    {
        if (gain) distGainNow = std::pow(10.0f, std::min(std::max(st.distDriveDb + m[0] * 60.0f, -30.0f), 30.0f) / 20.0f);
        if (filter && st.distFlt)
            distVf.set(std::min(std::max(st.distFltNote + m[1] * 128.0f, 8.0f), 136.0f), std::min(std::max(st.distFltRes + m[2], 0.0f), 1.0f), std::min(std::max(st.distFltBlend + m[3] * 2.0f, 0.0f), 2.0f), sr);
    }

    /* A route from an envelope or an LFO to the Distortion's Wet (Serum: the Wet knob, in its percent domain, moves by the route's amount x the source): the calibrated law's smoothstep
     * s is taken back to the Wet knob it came from, the offset added, and the smoothstep applied again. Called every block by the engine; 0 leaves the pot's mix as it is. */
    void setDistWetMod(float m)
    {
        if (!st.distCal && !st.distVital) return;
        if (m == distWetModSeen) return;
        distWetModSeen = m;
        float s = distMixBase;
        if (m != 0.0f && st.distVital) {
            s = std::min(std::max(distMixBase + m, 0.0f), 1.0f);        /* Vital's mix is a linear crossfade and a route adds to its stored value */
        } else if (m != 0.0f) {
            const float w0 = 0.5f - std::sin(std::asin(std::min(std::max(1.0f - 2.0f * std::min(std::max(distMixBase, 0.0f), 1.0f), -1.0f), 1.0f)) / 3.0f);
            const float w = std::min(std::max(w0 + m, 0.0f), 1.0f);
            s = w * w * (3.0f - 2.0f * w);
        }
        st.distMix = s;
        distTarget = st.distVital ? (s > 0.0f ? 1.0f : 0.0f) : (s > 0.0f || std::fabs(st.distLevel - 1.0f) > 1e-3f) ? 1.0f : 0.0f;
        baseActive = baseActive || distTarget > 0.0f;
        anyActive = baseActive || xfOn;
    }

    /* A route from an envelope or an LFO to the Comp's Wet (its parallel mix, linear in Serum's percent): added to the pot's mix. */
    void setCompWetMod(float m)
    {
        if (m == compWetModSeen) return;
        compWetModSeen = m;
        st.compMix = std::min(std::max(compMixBase + m, 0.0f), 1.0f);
        compTarget = (st.compRatio > 1.001f || st.compMulti) ? st.compMix : 0.0f;
        baseActive = baseActive || compTarget > 0.0f;
        anyActive = baseActive || xfOn;
    }

    /* wet: the Filter FX's Wet knob as a fraction of 0..100 %. Serum mixes it as a smoothstep s with the dry part kept, (1 - s^2) dry + s filtered (calibration/dev/probe_fxfilter_wet.py). */
    /* drive01: the Filter FX's Drive as a fraction of its 30 dB (Vital's law, saturate.h vitalFilterDriveIn; 0 is off); res01 is its Resonance knob. */
    void setFilter(bool on, int type, float hz, float q, float wet = 1.0f, float drive01 = 0.0f, float res01 = 0.0f, bool vitalLaw = false)
    {
        xfWetKnob = std::min(std::max(wet, 0.0f), 1.0f);
        xfHz = hz; xfRes = res01;
        xfDriveT = std::pow(10.0f, drive01 * 30.0f / 20.0f);
        /* Vital's filter saturates at every drive, 0 dB included, and its input gain 1 / (1 + 2 res) costs the passband about a ^ 0.8 (calibration probe: -5.2 dB at res 0.54, -2.1 at 0.2, -7.6 at 0.8) */
        xfDriveOn = vitalLaw || drive01 > 0.0f;
        xfDriveA = 1.0f / (1.0f + 2.0f * res01);
        xfPass = vitalLaw ? xfDriveA : 1.0f;                       /* Vital's passband loss of 1 / (1 + 2 res), exactly (measured with a sine through the plugin's Filter FX at every drive) */
        if (xfDriveOn) q = vitalFilterDriveQ(q, drive01);
        if (on) {
            if (!xfOn) xfS = xfWetKnob * xfWetKnob * (3.0f - 2.0f * xfWetKnob);
            static const Filter::Type kTypes[8] = {
                Filter::lowpass, Filter::lowpass, Filter::highpass, Filter::highpass,
                Filter::bandpass, Filter::bandpass, Filter::notch, Filter::notch };
            xf.setSampleRate(sr);
            xf.setType(kTypes[type & 7]);
            xf.setSlope((type & 1) ? Filter::db24 : Filter::db12);
            xf.setParams(hz, q);
        } else if (xfOn) {
            xf.reset();
            xfSk.reset(); xfLd.reset(); xfDt.reset(); xfDg.reset(); xfDo.reset(); xfComb.reset();
        }
        xfOn = on;
        anyActive = baseActive || xfOn;
    }

    /* The Filter FX at its Wet: the filtered signal at s and the dry at 1 - s^2 (s ramps towards the knob's smoothstep, so a moving Wet does not zip). */
    void processFilterFx(float *L, float *R, int n)
    {
        const float tgt = xfWetKnob * xfWetKnob * (3.0f - 2.0f * xfWetKnob);
        if (tgt >= 0.9999f && xfS >= 0.9999f) {
            xfS = 1.0f;
            if (xfVm) { runFilterModel(L, R, n); return; }
            if (xfDriveOn) { vitalFilterDriveIn(L, R, n, xfDriveG, xfDriveT, xfDriveA, xfPass); xfDriveG = xfDriveT; }
            xf.process(L, R, n);
            return;
        }
        float dL[128], dR[128];
        for (int o = 0; o < n; o += 128) {
            const int m = std::min(128, n - o);
            std::copy(L + o, L + o + m, dL);
            std::copy(R + o, R + o + m, dR);
            if (xfVm) runFilterModel(L + o, R + o, m);
            else {
                if (xfDriveOn) { const float g1 = xfDriveG + (xfDriveT - xfDriveG) * (float) m / (float) (n - o); vitalFilterDriveIn(L + o, R + o, m, xfDriveG, g1, xfDriveA, xfPass); xfDriveG = g1; }
                xf.process(L + o, R + o, m);
            }
            for (int i = 0; i < m; i++) {
                xfS += (tgt - xfS) * 0.002f;
                const float d = 1.0f - xfS * xfS;
                L[o + i] = dL[i] * d + L[o + i] * xfS;
                R[o + i] = dR[i] * d + R[o + i] * xfS;
            }
        }
        if (std::fabs(tgt - xfS) < 1e-4f) xfS = tgt;
    }

    void process(float *L, float *R, int n)
    {
        const int inBand = st.splitBand ? st.splitStages : 0;                    /* the stages that work on one band only */
        const bool convNow = (st.convMix > 0.0f && convP) || convWetCur > 0.0f, revNow = st.revMix > 0.0f || revWetCur > 0.0f;
        if (inBand && (((inBand & 64) && convNow) || ((inBand & 128) && revNow) || ((inBand & 1) && hyper.active()) || ((inBand & 2) && (distTarget > 0.0f || distCur > 0.0f)) || ((inBand & 4) && mod.active()) || ((inBand & 16) && bandEq) || ((inBand & 32) && (st.wideMix > 0.0f || wideCur > 0.0f)))) processBand(L, R, n, inBand);
        if (!(inBand & 1) && hyper.active()) hyper.process(L, R, n);
        /* The main chain's stages in the order fxSeq gives (FX Order; 0 is distortion, Filter FX, comp, chorus / flanger / phaser (and the width), delay, conv and reverb, EQ). */
        for (int k = 0; k < 7; k++) {
            switch (fxSeq[k]) {
            case 0:
                if (!(inBand & 2) && (distTarget > 0.0f || distCur > 0.0f)) processDist(L, R, n);
                break;
            case 1:
                if (xfOn) processFilterFx(L, R, n);
                break;
            case 2:
                if (compTarget > 0.0f || compCur > 0.0f) {
                    if (st.compVital) processCompVital(L, R, n); else if (st.compMulti) processCompMulti(L, R, n); else processComp(L, R, n);
                } else { compGrDb = 0.0f; compMs = 0.0f; for (int b = 0; b < 3; b++) { mbMs[b] = 0.0f; mbGain[b] = 0.0f; vcBand[b].hi[0] = vcBand[b].hi[1] = vcBand[b].lo[0] = vcBand[b].lo[1] = 0.0f; vcBand[b].mult = 0.0f; } vcSplitA.reset(); vcSplitB[0].reset(); vcSplitB[1].reset(); }
                break;
            case 3: {
                if (!(inBand & 4) && mod.active()) mod.process(L, R, n);

                const bool wideNow = st.wideMix > 0.0f || wideCur > 0.0f;
                if (wideNow && !(inBand & 32)) { processWide(L, R, n); wideRan = true; }               /* (with bit 32 the width works on the Split band, in processBandChunk) */
                else if (!wideNow && wideRan) { clearWide(); wideRan = false; }
                break;
            }
            case 4: {
                const DelayPrm q[2] = { { st.delayMix, st.delayMs, st.delayRMs, st.delayFb, st.delayLoHz, st.delayHiHz, st.delayPing },
                                        { st.delay2Mix, st.delay2Ms, st.delay2RMs, st.delay2Fb, st.delay2LoHz, st.delay2HiHz, st.delay2Ping } };
                for (int j = 0; j < 2; j++) {
                    if (j == 0 && st.delayVital) {
                        if (st.delayVMix > 0.0f || vdly.wet > 0.0f) { processDelayVital(L, R, n); vdly.ran = true; }
                        else if (vdly.ran) { vdly.clear(); vdly.ran = false; }
                        continue;
                    }
                    if (q[j].mix > 0.0f || dly[j].wetCur > 0.0f) { processDelay(dly[j], q[j], L, R, n); dly[j].ran = true; }
                    else if (dly[j].ran) { clearDelay(dly[j]); dly[j].ran = false; }
                }
                break;
            }
            case 5:
                if (!(inBand & 64) && convNow) { processConv(L, R, n); convRan = true; }                /* (with bit 64 the convolver works on the Split band, in processBandChunk) */
                else if (!convNow && convRan) { if (convP) convP->reset(); convRan = false; }

                if (!(inBand & 128) && revNow) { processReverb(L, R, n); revRan = true; }              /* (likewise bit 128: the reverb) */
                else if (!revNow && revRan) { clearReverb(); revRan = false; }
                break;
            default:
                processEq(L, R, n);
                break;
            }
        }
    }

    /* The EQ stage (the last of the main chain unless FX Order moves it). */
    void processEq(float *L, float *R, int n)
    {
        /* The bands run whether or not the EQ is switched on, so the filters hold the signal's state and
         * switching never starts one from silence; only their output is crossfaded (about 10 ms). With
         * the EQ on, a sample goes through the bands in the same order as before, bit for bit. */
        bool eqAny = false;
        for (int b = 0; b < 4; b++) eqAny = eqAny || eqOn[b];
        eqAny = eqAny || msAny;
        if (!eqAny) return;
        const float step = 1.0f / (0.010f * sr);
        for (int i = 0; i < n; i++) {
            if (eqCur < eqTarget) eqCur = std::min(eqCur + step, eqTarget);
            else if (eqCur > eqTarget) eqCur = std::max(eqCur - step, eqTarget);
            const float dl = L[i], dr = R[i];
            float l = dl, r = dr;
            for (int b = 0; b < 4; b++) {
                if (!eqOn[b]) continue;
                l = eq[b].tick(0, l);
                r = eq[b].tick(1, r);
            }
            if (msMaster) {                    /* the SplitMS branches: Mid and Side each through their own bands (a split on its own is transparent) */
                float m = 0.5f * (l + r), sd = 0.5f * (l - r);
                for (int b = 0; b < 4; b++) {
                    if (msOn[0][b]) m = msEq[0][b].tick(0, m);
                    if (msOn[1][b]) sd = msEq[1][b].tick(0, sd);
                }
                l = m + sd; r = m - sd;
            }
            if (eqCur >= 1.0f) { L[i] = l; R[i] = r; }
            else               { L[i] = dl + eqCur * (l - dl); R[i] = dr + eqCur * (r - dr); }
        }
    }

private:
    /* The multiband compressor: three bands split by Linkwitz-Riley crossovers; each band has its own rms detector, a downward curve above
     * its threshold (ratio, soft knee) and an upward one below a floor 11 dB under it (slope Below), smoothed in dB with attack and
     * release, then a band gain; the bands are summed, then makeup and the parallel mix. Measured on Serum's with a sine in the mid band. */
    struct MbBq {
        float b0 = 1, b1 = 0, b2 = 0, a1 = 0, a2 = 0, z1[2] = { 0, 0 }, z2[2] = { 0, 0 };
        void set(bool hp, float fc, float sr)
        {
            const float w = 6.28318530718f * fc / sr, cs = std::cos(w), alpha = std::sin(w) / (2.0f * 0.70710678f), a0 = 1.0f + alpha;
            if (!hp) { b0 = (1.0f - cs) * 0.5f / a0; b1 = (1.0f - cs) / a0; }
            else     { b0 = (1.0f + cs) * 0.5f / a0; b1 = -(1.0f + cs) / a0; }
            b2 = b0; a1 = -2.0f * cs / a0; a2 = (1.0f - alpha) / a0;
        }
        float tick(int c, float x)
        {
            const float y = b0 * x + z1[c];
            z1[c] = b1 * x - a1 * y + z2[c];
            z2[c] = b2 * x - a2 * y;
            return y;
        }
    };

    static float kneeCurve(float over, float slope, float knee)
    {
        if (over <= -0.5f * knee) return 0.0f;
        if (over >= 0.5f * knee) return slope * over;
        const float k = over + 0.5f * knee;
        return slope * k * k / (2.0f * knee);
    }

    void processCompMulti(float *L, float *R, int n)
    {
        const float dw = (compTarget - compCur) / (float) n;
        const float slope = 1.0f - 1.0f / std::max(st.compRatio, 1.0f), knee = std::max(st.compKneeDb, 0.1f), sb = std::min(std::max(st.compBelow, 0.0f), 1.0f);
        for (int i = 0; i < n; i++) {
            compCur += dw;
            float band[3][2];
            for (int c = 0; c < 2; c++) {
                const float x = c ? R[i] : L[i];
                const float lo2 = mbLp2[1].tick(c, mbLp2[0].tick(c, x)), hi = mbHp2[1].tick(c, mbHp2[0].tick(c, x));
                band[0][c] = mbLp1[1].tick(c, mbLp1[0].tick(c, lo2));
                band[1][c] = mbHp1[1].tick(c, mbHp1[0].tick(c, lo2));
                band[2][c] = mbApLp[1].tick(c, mbApLp[0].tick(c, hi)) + mbApHp[1].tick(c, mbApHp[0].tick(c, hi));
            }
            float out[2] = { 0, 0 };
            for (int b = 0; b < 3; b++) {
                mbMs[b] += (0.5f * (band[b][0] * band[b][0] + band[b][1] * band[b][1]) - mbMs[b]) * compDet;
                const float xdb = 3.0102999566f * fxdetail::fastLog2(mbMs[b] + 1e-18f), T = st.compThreshBandDb[b];
                /* The upward part has no end in a silent tail: a band at -180 dB asked for +135 dB, and the next note arrived into that gain
                 * (Serum's own stays level across repeated notes). It is capped, so the boost a quiet tail earns stays one a note can recover from. */
                const float up = std::min(sb * kneeCurve((T - 11.0f) - xdb, 1.0f, knee), st.compBoostDb);
                const float target = up - kneeCurve(xdb - T, slope, knee);   /* dB: + boosts, - reduces */
                mbGain[b] = target + (target < mbGain[b] ? compAtt : compRel) * (mbGain[b] - target);
                const float g = fxdetail::fastExp2((mbGain[b] + st.compBandGainDb[b]) * 0.16609640474f);          /* 10^(dB / 20) */
                out[0] += band[b][0] * g;
                out[1] += band[b][1] * g;
            }
            const float w = compCur;
            L[i] += w * (out[0] * compMk - L[i]);
            R[i] += w * (out[1] * compMk - R[i]);
        }
        compCur = compTarget;
    }

    /* Vital's compressor (effects/compressor.cpp, MultibandCompressor): Linkwitz-Riley splits at 120 Hz and 2.5 kHz, each band's own pair of envelope followers on the squared signal (attack and
     * release, a per-band base time), a downward gain over the upper threshold and an upward (or expanding) one under the lower, a band gain, and the bands summed. Left and right run apart. */
    struct VcBand {
        float upThr2 = 1, loThr2 = 1, upR = 0, loR = 0, attN = 1, relN = 1, attScale = 0.5f, relScale = 0.5f, mult = 0, multTarget = 1;
        float hi[2] = { 0, 0 }, lo[2] = { 0, 0 };
        void setup(const FxSettings &s, int b, float sr)
        {
            static const float kAtt[3] = { 2.8f, 1.4f, 0.7f }, kRel[3] = { 40.0f, 28.0f, 15.0f };
            const float spm = sr * 0.001f;
            attN = std::max(std::exp(std::min(std::max(s.vcAttack, 0.0f), 1.0f) * 8.0f - 4.0f) * kAtt[b] * spm, 5.0f);
            relN = std::max(std::exp(std::min(std::max(s.vcRelease, 0.0f), 1.0f) * 8.0f - 4.0f) * kRel[b] * spm, 5.0f);
            attScale = 1.0f / (attN + 1.0f); relScale = 1.0f / (relN + 1.0f);
            upThr2 = std::pow(10.0f, std::min(std::max(s.vcUpThrDb[b], -100.0f), 12.0f) * 0.1f);
            loThr2 = std::pow(10.0f, std::min(std::max(s.vcLoThrDb[b], -100.0f), 12.0f) * 0.1f);
            upR = std::min(std::max(s.vcUpRatio[b], 0.0f), 1.0f) * 0.5f;
            loR = std::min(std::max(s.vcLoRatio[b], -1.0f), 1.0f) * 0.5f;
            multTarget = std::pow(10.0f, std::min(std::max(s.vcGainDb[b], -30.0f), 30.0f) * 0.05f);
        }
        float tick(int c, float x)
        {
            const float sq = x * x;
            const bool hiAtt = sq > hi[c];
            hi[c] = (sq + hi[c] * (hiAtt ? attN : relN)) * (hiAtt ? attScale : relScale);
            hi[c] = std::max(hi[c], upThr2);
            const bool loAtt = sq > lo[c];
            lo[c] = (sq + lo[c] * (loAtt ? attN : relN)) * (loAtt ? attScale : relScale);
            lo[c] = std::min(lo[c], loThr2);
            float g = 1.0f;
            if (upR > 0.0f) g *= fxdetail::fastExp2(std::min(upR * fxdetail::fastLog2(upThr2 / hi[c]), 20.0f));
            if (loR != 0.0f) g *= fxdetail::fastExp2(std::min(std::max(loR * fxdetail::fastLog2(loThr2 / (lo[c] + 1e-30f)), -60.0f), 20.0f));
            return std::min(std::max(g, 0.0f), 32.0f) * x;
        }
    };
    struct VcSplit {                                         /* one Linkwitz-Riley split: low and high, each two Butterworth biquads */
        MbBq lp[2], hp[2];
        void set(float fc, float sr) { for (int i = 0; i < 2; i++) { lp[i].set(false, fc, sr); hp[i].set(true, fc, sr); } }
        void reset() { for (int i = 0; i < 2; i++) { lp[i] = MbBq(); hp[i] = MbBq(); } }
        void tick(int c, float x, float &lo, float &hi) { lo = lp[1].tick(c, lp[0].tick(c, x)); hi = hp[1].tick(c, hp[0].tick(c, x)); }
    };

    void processCompVital(float *L, float *R, int n)
    {
        const float dw = (compTarget - compCur) / (float) n;
        VcBand *bd = vcBand;
        float startMult[3] = { bd[0].mult, bd[1].mult, bd[2].mult };
        for (int i = 0; i < n; i++) {
            compCur += dw;
            const float u = (float) (i + 1) / (float) n;
            float m[3];
            for (int b = 0; b < 3; b++) m[b] = startMult[b] + (bd[b].multTarget - startMult[b]) * u;
            float out[2];
            for (int c = 0; c < 2; c++) {
                const float x = c ? R[i] : L[i];
                float lo1, hi1, y;
                switch (st.vcBands) {
                case 0: {
                    float lo1L, lo1H, hi1L, hi1H;
                    vcSplitA.tick(c, x, lo1, hi1);
                    vcSplitB[0].tick(c, lo1, lo1L, lo1H);
                    vcSplitB[1].tick(c, hi1, hi1L, hi1H);
                    y = bd[0].tick(c, lo1L + lo1H) * m[0] + bd[1].tick(c, hi1L) * m[1] + bd[2].tick(c, hi1H) * m[2];
                    break; }
                case 1:
                    vcSplitA.tick(c, x, lo1, hi1);
                    y = bd[0].tick(c, lo1) * m[0] + bd[1].tick(c, hi1) * m[1];
                    break;
                case 2:
                    vcSplitB[0].tick(c, x, lo1, hi1);
                    y = bd[1].tick(c, lo1) * m[1] + bd[2].tick(c, hi1) * m[2];
                    break;
                default:
                    y = bd[1].tick(c, x) * m[1];
                    break;
                }
                out[c] = y;
            }
            const float w = compCur;
            L[i] += w * (out[0] * compMk - L[i]);
            R[i] += w * (out[1] * compMk - R[i]);
        }
        for (int b = 0; b < 3; b++) bd[b].mult = bd[b].multTarget;
        compCur = compTarget;
    }

    /* Compressor: a stereo-linked rms detector (20 ms), the gain computer on its level in dB (threshold, ratio, a soft knee of the width set), and the gain
     * reduction smoothed in the dB domain with separate attack and release; makeup, then a parallel mix. */
    void processComp(float *L, float *R, int n)
    {
        const float dw = (compTarget - compCur) / (float) n;
        const float T = st.compThreshDb, slope = 1.0f - 1.0f / std::max(st.compRatio, 1.0f), knee = std::max(st.compKneeDb, 0.1f);
        for (int i = 0; i < n; i++) {
            compCur += dw;
            compMs += (0.5f * (L[i] * L[i] + R[i] * R[i]) - compMs) * compDet;
            const float xdb = 3.0102999566f * fxdetail::fastLog2(compMs + 1e-18f) + 4.77f;      /* the rms and a saw's crest: the level the curves were fitted at */
            const float over = xdb - T;
            float targetGr;                              /* gain reduction wanted, dB (>= 0) */
            if (over <= -0.5f * knee) targetGr = 0.0f;
            else if (over >= 0.5f * knee) targetGr = slope * over;
            else { const float k = over + 0.5f * knee; targetGr = slope * k * k / (2.0f * knee); }
            const float c = targetGr > compGrDb ? compAtt : compRel;
            compGrDb = targetGr + c * (compGrDb - targetGr);
            const float g = fxdetail::fastExp2(-compGrDb * 0.16609640474f) * compMk;
            const float w = compCur;
            L[i] += w * (L[i] * g - L[i]);
            R[i] += w * (R[i] * g - R[i]);
        }
        compCur = compTarget;
    }

    /* The stages of a Split / SplitMS branch: the band (or Mid / Side) goes through them and joins the rest again. A 4th-order Linkwitz-Riley split (two Butterworth biquads each way) sums
     * flat, so a branch that changes nothing leaves the signal as it was (calibration/dev/probe_split_band.py: Serum's Split is -6 dB at Freq and 24 dB/octave). */
    void processBand(float *L, float *R, int n, int stages)
    {
        for (int at = 0; at < n; at += 128) processBandChunk(L + at, R + at, std::min(128, n - at), stages);
    }

    void processBandChunk(float *L, float *R, int n, int stages)
    {
        float aL[128], aR[128], oL[128], oR[128];
        const int band = st.splitBand;
        for (int i = 0; i < n; i++) {
            if (band <= 2) {
                float sel[2], oth[2];
                for (int c = 0; c < 2; c++) {
                    const float x = c ? R[i] : L[i];
                    const float lo = spLp[1].tick(c, spLp[0].tick(c, x)), hi = spHp[1].tick(c, spHp[0].tick(c, x));
                    sel[c] = band == 1 ? lo : hi; oth[c] = band == 1 ? hi : lo;
                }
                aL[i] = sel[0]; aR[i] = sel[1]; oL[i] = oth[0]; oR[i] = oth[1];
            } else {
                const float m = 0.5f * (L[i] + R[i]), sd = 0.5f * (L[i] - R[i]);
                if (band == 3) { aL[i] = aR[i] = m; oL[i] = sd; oR[i] = -sd; }
                else           { aL[i] = aR[i] = sd; oL[i] = m; oR[i] = m; }
            }
        }
        const bool modFirst = (stages & 8) != 0;                                  /* the branch has its Chorus / Flanger / Phaser ahead of its Distortion */
        if ((stages & 1) && hyper.active()) hyper.process(aL, aR, n);
        if (modFirst && (stages & 4) && mod.active()) mod.process(aL, aR, n);
        if ((stages & 2) && (distTarget > 0.0f || distCur > 0.0f)) processDist(aL, aR, n);
        if (!modFirst && (stages & 4) && mod.active()) mod.process(aL, aR, n);
        if ((stages & 32) && (st.wideMix > 0.0f || wideCur > 0.0f)) { processWide(aL, aR, n); wideRan = true; }      /* a HyperD's Dimension in the branch */
        if ((stages & 16) && bandEq) {                                           /* the EQ modules of the branch (after its other stages) */
            for (int b = 0; b < 4; b++) {
                if (!msOn[0][b]) continue;
                for (int i = 0; i < n; i++) { aL[i] = msEq[0][b].tick(0, aL[i]); aR[i] = msEq[0][b].tick(1, aR[i]); }
            }
        }
        if ((stages & 64) && ((st.convMix > 0.0f && convP) || convWetCur > 0.0f)) { processConv(aL, aR, n); convRan = true; }      /* the band's own Conv and Reverb, after its other stages */
        if ((stages & 128) && (st.revMix > 0.0f || revWetCur > 0.0f)) { processReverb(aL, aR, n); revRan = true; }
        for (int i = 0; i < n; i++) {
            if (band == 4)      { L[i] = oL[i] + aL[i]; R[i] = oR[i] - aL[i]; }
            else if (band == 3) { L[i] = oL[i] + aL[i]; R[i] = oR[i] + aL[i]; }
            else                { L[i] = oL[i] + aL[i]; R[i] = oR[i] + aR[i]; }
        }
    }

    /* Distortion. Pre-gain g = 1 + 14 drive^2 (up to +23 dB) into the curve, scaled by 1/sqrt(g) so the level stays near
     * where it was while the peaks round off. The wet part ramps over the block, so switching it on or off, or moving
     * the mix, does not step. Asym biases the input and takes the bias's own output back out, which makes even harmonics
     * without a DC offset. */
    void processDist(float *L, float *R, int n)
    {
        if (st.distVital) { processDistVital(L, R, n); return; }
        if (st.distCal) { processDistCal(L, R, n); return; }
        const float a = std::min(std::max(st.distDrive, 0.0f), 1.0f);
        const float g = 1.0f + 14.0f * a * a, k = 1.0f / std::sqrt(g);
        const float bias = 0.25f, biasOut = std::tanh(g * bias);
        const float dw = (distTarget - distCur) / (float) n;
        for (int i = 0; i < n; i++) {
            distCur += dw;
            const float w = distCur;
            float x[2] = { L[i], R[i] }, y[2];
            for (int c = 0; c < 2; c++) {
                const float v = x[c];
                switch (st.distMode) {
                case 1:  y[c] = (std::tanh(g * (v + bias)) - biasOut) * k; break;
                case 2:  y[c] = std::min(std::max(g * v, -1.0f), 1.0f) * k; break;
                default: y[c] = std::tanh(g * v) * k; break;
                }
            }
            L[i] = x[0] + w * (y[0] - x[0]);
            R[i] = x[1] + w * (y[1] - x[1]);
        }
        distCur = distTarget;
    }

    /* Vital's law (calibration/vital_dist_probe.py, vital_distfilt_probe.py): the wet part is the curve of gain x signal, in units of Vital's own full scale (the engine's pre-master level is 6.7 dB
     * under it, so the signal is divided by kVitalFull going in and multiplied coming out), through the filter before or after it; the dry part is not filtered, and the mix is a plain crossfade. */
    void processDistVital(float *L, float *R, int n)
    {
        constexpr float kVitalFull = 0.46345f;                     /* 10^(-6.7 / 20) */
        constexpr int kAudioStep = 1;                              /* the audio-rate LFO is applied every this many samples (a power of 2) */
        /* Down Sample (a high sine through the plugin, calibration/vital_dist_probe.py): Vital holds each sample for a length that grows with Drive, 2 samples at the bottom of the knob, 4 at 0 dB, doubling every ~9 dB; the plugin's own
         * output past that is a ping-pong of two held values that this plain hold does not reproduce, so the length is capped at 12 */
        const float vdsLen = std::min(std::max(4.0f * std::pow(2.0f, st.distDriveDb / 8.9f), 2.0f), 12.0f);
        float g = distGainNow / kVitalFull;
        /* Bit Crush (distortion.h bitCrush / bitCrushScale): the signal in Vital's own full scale is rounded to a step of ((drive + 30) / 60)^2, from 1 at +30 dB down to nothing at -30 dB; Drive is not a gain here */
        auto crushStep = [&]() { const float db = 20.0f * std::log10(std::max(distGainNow, 1e-6f)); const float d = std::min(std::max((db + 30.0f) / 60.0f, 0.0f), 1.0f); return std::min(std::max(d * d, 1.49e-8f), 1.0f); };
        float cs = st.distMode == 6 ? crushStep() : 1.0f;
        const float m = std::min(std::max(st.distMix, 0.0f), 1.0f);
        const float dw = (distTarget - distCur) / (float) n;
        for (int i = 0; i < n; i++) {
            if (st.distAudio && (i & (kAudioStep - 1)) == 0) {
                /* the audio-rate LFO, stepped every sample (4 left a stepped-coefficient hash that the plugin's per-sample cutoff does not have): its value moves Drive and the filter, on top of the block-rate route offsets */
                distAudioPhase += (float) kAudioStep * distAudioHz / sr;
                distAudioPhase -= std::floor(distAudioPhase);
                const float px = distAudioPhase * 16.0f;
                const int i0 = std::min((int) px, 15), i1 = (i0 + 1) & 15;
                const float u = st.distAudioShape[i0] + (st.distAudioShape[i1] - st.distAudioShape[i0]) * (px - (float) i0);
                float mm[4];
                for (int k = 0; k < 4; k++) mm[k] = distVModSeen[k] + st.distAudioAmt[k] * u;
                applyVitalDist(mm, st.distAudioAmt[0] != 0.0f, st.distAudioAmt[1] != 0.0f || st.distAudioAmt[2] != 0.0f || st.distAudioAmt[3] != 0.0f);      /* only what the LFO moves is redone */
                g = distGainNow / kVitalFull;
                if (st.distMode == 6) cs = crushStep();
            }
            distCur += dw;
            const float w = distCur;
            float x[2] = { L[i], R[i] }, y[2];
            for (int c = 0; c < 2; c++) {
                const float v = x[c];
                const float vf = st.distFlt == 1 ? distVf.tick(c, v) : v;
                const float t = g * vf;
                float f;
                switch (st.distMode) {
                case 2:  f = std::min(std::max(t, -1.0f), 1.0f); break;
                case 3:  f = std::sin(1.57079633f * t); break;
                case 4:  if (c == 0) { vdsPhase += 1.0f; vdsNew = vdsPhase >= vdsLen; if (vdsNew) vdsPhase -= vdsLen; }
                         if (vdsNew) dsHold[c] = vf;
                         f = dsHold[c] / kVitalFull; break;
                case 5:  { float p = std::fmod(t + 1.0f, 4.0f); if (p < 0.0f) p += 4.0f; f = p < 2.0f ? p - 1.0f : 3.0f - p; break; }
                case 6:  f = std::round(vf / kVitalFull / cs) * cs; break;
                default: f = std::tanh(t); break;
                }
                float wet = f * kVitalFull;
                if (st.distFlt == 2) wet = distVf.tick(c, wet);
                y[c] = v + m * (wet - v);
            }
            L[i] = x[0] + w * (y[0] - x[0]);
            R[i] = x[1] + w * (y[1] - x[1]);
        }
        distCur = distTarget;
    }

    /* The calibrated law (calibration/dev/probe_dist_transfer.py, probe_dist_wet.py, calibrate_dist2.py): Serum's Wet is a smoothstep s with the dry part
     * (1 - s^2), and its LevelOut a gain on the sum. The curve is out * f(gain x): tanh, the biased tanh (a bias of 0.004 at the input, its DC taken
     * out), a hard clip, or a sine. distTarget is 1 whenever the stage runs, so the ramp is only the switching. */
    void processDistCal(float *L, float *R, int n)
    {
        const float s = std::min(std::max(st.distMix, 0.0f), 1.0f), dry = 1.0f - s * s;
        const float g = st.distGain, k = st.distOut, lv = st.distLevel, bias = 0.004f, biasOut = std::tanh(g * bias);
        const float dw = (distTarget - distCur) / (float) n;
        const float dcA = 1.0f - std::exp(-2.0f * 3.14159265f * 8.0f / sr);
        for (int i = 0; i < n; i++) {
            distCur += dw;
            const float w = distCur;
            float x[2] = { L[i], R[i] }, y[2];
            for (int c = 0; c < 2; c++) {
                const float v = x[c];
                const float vf = st.distPre ? distPreBq.tick(c, v) : v;          /* the wet part sees the filtered signal, the dry part does not */
                float f;
                switch (st.distMode) {
                case 1:  f = std::tanh(g * (vf + bias)) - biasOut; break;
                case 2:  f = std::min(std::max(g * vf, -1.0f), 1.0f); break;
                case 3:  f = std::sin(g * vf); break;
                case 4:  if (dsCount == 0) dsHold[c] = vf; f = dsHold[c]; break;      /* Serum's Downsample: the sample at the start of each hold, repeated */
                default: f = std::tanh(g * vf); break;
                }
                float o = lv * (dry * v + s * k * f);
                distDc[c] += dcA * (o - distDc[c]);              /* every curve can leave a DC term on a loud or lopsided input (the bias, a sine fold of an offset): taken out of the sum */
                y[c] = o - distDc[c];
            }
            if (st.distMode == 4) { if (++dsCount >= st.distHold) dsCount = 0; } else dsCount = 0;
            L[i] = x[0] + w * (y[0] - x[0]);
            R[i] = x[1] + w * (y[1] - x[1]);
        }
        distCur = distTarget;
    }

    /* Two modulated taps per side off the mono sum, and only their DIFFERENCE is added, as
     * +side on the left and -side on the right. So the mono sum of the output is exactly the
     * input, and width costs no phase cancellation when the result is folded down. */
    void processWide(float *L, float *R, int n)
    {
        const float target = st.wideMix;
        const float dg = (target - wideCur) / (float) n;
        const float rate = st.wideRate * fxdetail::kTwoPi / sr;
        const float baseD = 0.012f * sr, depthD = (0.002f + 0.006f * st.wideDepth) * sr;
        for (int i = 0; i < n; i++) {
            const float m = 0.5f * (L[i] + R[i]);
            chorusBuf[(size_t) chorusPos] = m;
            /* the four sweeps are sin(p), sin(p + pi/2) and their negatives (a half cycle apart): one sine and one cosine of the phase */
            const float cyc = chorusPhase * (1.0f / fxdetail::kTwoPi), sn = fxdetail::sinCyc(cyc), cn = fxdetail::sinCyc(cyc + 0.25f);
            const float sweep[2][2] = { { sn, cn }, { -sn, -cn } };
            float w[2];
            for (int side = 0; side < 2; side++) {
                float acc = 0.0f;
                for (int tap = 0; tap < 2; tap++) {
                    const float d = baseD + (tap ? 0.0007f * sr : 0.0f) + depthD * sweep[side][tap];
                    const float rp = (float) chorusPos - d + (float) kChorusMax;
                    const int i0 = (int) rp;
                    const float fr = rp - (float) i0;
                    const float a = chorusBuf[(size_t) (i0 & (kChorusMax - 1))];            /* i0 is positive and kChorusMax a power of two: a mask is the remainder */
                    const float b = chorusBuf[(size_t) ((i0 + 1) & (kChorusMax - 1))];
                    acc += a + (b - a) * fr;
                }
                w[side] = 0.5f * acc;
            }
            wideCur += dg;
            const float side = wideCur * fxdetail::kWideGain * 0.5f * (w[0] - w[1]);
            L[i] += side;
            R[i] -= side;
            chorusPhase += rate;
            if (chorusPhase > fxdetail::kTwoPi) chorusPhase -= fxdetail::kTwoPi;
            chorusPos = (chorusPos + 1) & (kChorusMax - 1);
        }
        wideCur = target;
    }

    /* Stereo mode: each side echoes itself. Ping-pong: the mono sum feeds the left line, the
     * left echo feeds the right line, and the right echo feeds back to the left, so the right
     * echo lands at the left time plus the right time (measured on Serum's Mode 1). */
    void processDelay(DelayUnit &u, const DelayPrm &q, float *L, float *R, int n)
    {
        const float tgtWet = fxdetail::kDelayOut * wetGain(q.mix);
        const float tgtDry = dryGain(q.mix);
        const float dw = (tgtWet - u.wetCur) / (float) n, dd = (tgtDry - u.dryCur) / (float) n;
        const float lim = (float) (kDelayMax - 4);
        const float tgtT[2] = { std::min(q.ms * 0.001f * sr, lim), std::min(q.rms * 0.001f * sr, lim) };
        if (!u.ran) { u.timeCur[0] = tgtT[0]; u.timeCur[1] = tgtT[1]; }   /* no glide from zero */
        const float slew = 1.0f - std::exp(-1.0f / (0.05f * sr));       /* a knob turn glides over 50 ms */
        const float aHp = 1.0f - std::exp(-fxdetail::kTwoPi * q.lo / sr);
        const float aLp = 1.0f - std::exp(-fxdetail::kTwoPi * std::min(q.hi, sr * 0.45f) / sr);
        const float fb = q.fb;
        /* Serum's delay filter has unity gain at its centre whatever its width (calibration/probe_delay_filter2.json: 0.0 dB at Freq for BW 0.75..9). The two one-poles lose
         * R / (1 + R) at their geometric centre, R being the ratio of the corners: a narrow filter lost 2.6 dB per pass, so its echoes died faster than Serum's. */
        const float cornerRatio = std::max(std::min(q.hi, sr * 0.45f) / std::max(q.lo, 1.0f), 1.0001f);
        const float makeup = (1.0f + cornerRatio) / cornerRatio;
        const int mask = kDelayMax - 1;
        float *bl = u.buf[0].data(), *br = u.buf[1].data();
        for (int i = 0; i < n; i++) {
            for (int c = 0; c < 2; c++) u.timeCur[c] += (tgtT[c] - u.timeCur[c]) * slew;
            float echo[2];
            for (int c = 0; c < 2; c++) {
                const float *buf = c ? br : bl;
                const float rp = (float) u.pos - u.timeCur[c] + (float) kDelayMax;
                const int i0 = (int) rp;
                const float fr = rp - (float) i0;
                const float a = buf[i0 & mask], b = buf[(i0 + 1) & mask];
                echo[c] = a + (b - a) * fr;
            }
            /* The delay's band-pass (its Freq and BW) is on the whole wet output, so the first echo is filtered, and what feeds back is the
             * filtered echo (Serum's filter at Freq 40 takes 14.7 dB off a noise echo; with the filter only in the feedback the first echo went out
             * untouched and a preset with a narrow filter had an echo 5-12 dB too loud). */
            float ef[2];
            for (int c = 0; c < 2; c++) {
                u.fbHp[c] = sanitize(u.fbHp[c] + aHp * (echo[c] - u.fbHp[c]));
                const float hp = echo[c] - u.fbHp[c];
                u.fbLp[c] = sanitize(u.fbLp[c] + aLp * (hp - u.fbLp[c]));
                ef[c] = u.fbLp[c] * makeup;
            }
            float f[2];
            if (q.ping) { f[0] = ef[1] * fb; f[1] = 0.0f; }
            else { f[0] = ef[0] * fb; f[1] = ef[1] * fb; }
            if (q.ping) {
                bl[u.pos] = sanitize(0.5f * (L[i] + R[i]) + f[0]);
                br[u.pos] = sanitize(ef[0]);
            } else {
                bl[u.pos] = sanitize(L[i] + f[0]);
                br[u.pos] = sanitize(R[i] + f[1]);
            }
            u.wetCur += dw; u.dryCur += dd;
            L[i] = L[i] * u.dryCur + ef[0] * u.wetCur;
            R[i] = R[i] * u.dryCur + ef[1] * u.wetCur;
            u.pos = (u.pos + 1) & mask;
        }
        u.wetCur = tgtWet; u.dryCur = tgtDry;
    }

    /* Vital's delay (effects/delay.cpp), ported: a stereo line with a period for each side (the left and right Frequency knobs, or the Aux one on the right for the stereo and ping-pong styles), the write
     * saturated and band-passed by a pair of one-poles (gain 1 + high / low corner), an equal-power dry / wet, and the periods and frequency gliding with a 20 ms half-life. Styles: 0 mono, 1 stereo, 2 ping
     * pong (the mono sum goes into the left line, the sides swap on every write and the right feedback is 1), 3 mid ping pong (the same without the mono sum, the left period the sum of both). */
    struct VitalDelayState {
        static constexpr int kMax = 1 << 18;
        std::vector<float> buf[2];
        int pos = 0;
        float freq[2] = { 0, 0 }, period[2] = { 0, 0 }, wet = 0, dry = 1, fb[2] = { 0, 0 }, gain = 1, lc = 0.1f, hc = 0.1f;
        float lpS[2] = { 0, 0 }, hpS[2] = { 0, 0 };
        bool ran = false, init = false;
        void clear() { for (auto &b : buf) std::fill(b.begin(), b.end(), 0.0f); pos = 0; lpS[0] = lpS[1] = hpS[0] = hpS[1] = 0.0f; init = false; wet = 0.0f; }
    } vdly;
    static float vdCoef(float hz, float sr) { const float x = std::min(std::max(hz, 1.0f), sr * 0.4535f) * 3.14159265f / sr; return std::tan(x / (x + 1.0f)); }
    void processDelayVital(float *L, float *R, int n)
    {
        auto &u = vdly;
        if (u.buf[0].empty()) { u.buf[0].assign((size_t) VitalDelayState::kMax, 0.0f); u.buf[1].assign((size_t) VitalDelayState::kMax, 0.0f); }
        const int style = std::min(std::max(st.delayStyle, 0), 3);
        float tgtF[2] = { 1000.0f / std::max(st.delayMs, 1.0f), 1000.0f / std::max(st.delayMs, 1.0f) };
        if (style >= 1) tgtF[1] = 1000.0f / std::max(st.delayRMs, 1.0f);
        if (!u.init) { for (int c = 0; c < 2; c++) u.freq[c] = tgtF[c]; }
        const float decay = std::pow(0.5f, (float) n / (0.02f * sr));
        for (int c = 0; c < 2; c++) u.freq[c] = tgtF[c] + (u.freq[c] - tgtF[c]) * decay;
        float tgtP[2] = { sr / u.freq[0], sr / u.freq[1] };
        if (style == 3) tgtP[0] += tgtP[1];
        const float maxP = (float) (VitalDelayState::kMax - 8);
        for (int c = 0; c < 2; c++) tgtP[c] = std::min(std::max(tgtP[c], 3.0f), maxP);
        if (!u.init) { for (int c = 0; c < 2; c++) u.period[c] = tgtP[c]; u.init = true; }
        const float wetT = std::sin(1.5707963f * st.delayVMix), dryT = std::cos(1.5707963f * st.delayVMix);
        const float fbT[2] = { std::min(std::max(st.delayFb, -1.0f), 1.0f), style == 2 ? 1.0f : std::min(std::max(st.delayFb, -1.0f), 1.0f) };
        if (!u.ran) { u.wet = wetT; u.dry = dryT; u.fb[0] = fbT[0]; u.fb[1] = fbT[1]; }
        const float lowHz = std::max(st.delayHiHz, 1.0f), highHz = std::max(st.delayLoHz, 1.0f);       /* the engine's Hi corner is Vital's low-pass, its Lo the high-pass */
        const float gainT = highHz / lowHz + 1.0f, lcT = vdCoef(lowHz, sr), hcT = vdCoef(highHz, sr);
        const float inv = 1.0f / (float) n;
        float curP[2] = { u.period[0], u.period[1] }, dP[2], wet = u.wet, dry = u.dry, fb[2] = { u.fb[0], u.fb[1] }, gain = u.gain, lc = u.lc, hc = u.hc;
        for (int c = 0; c < 2; c++) {
            const float newP = u.period[c] + (tgtP[c] - u.period[c]) * 0.5f;
            dP[c] = (newP - u.period[c]) * inv;
            u.period[c] = newP;
        }
        const float dW = (wetT - u.wet) * inv, dD = (dryT - u.dry) * inv, dF0 = (fbT[0] - u.fb[0]) * inv, dF1 = (fbT[1] - u.fb[1]) * inv, dG = (gainT - u.gain) * inv, dL = (lcT - u.lc) * inv, dH = (hcT - u.hc) * inv;
        const int mask = VitalDelayState::kMax - 1;
        auto sat8 = [](float v) { return std::min(std::max(v * 0.125f, -1.0f), 1.0f) * 8.0f; };
        for (int i = 0; i < n; i++) {
            wet += dW; dry += dD; fb[0] += dF0; fb[1] += dF1; gain += dG; lc += dL; hc += dH;
            const float in[2] = { L[i], R[i] };
            float rd[2];
            for (int c = 0; c < 2; c++) {
                const float rp = (float) u.pos - curP[c] + (float) VitalDelayState::kMax;
                const int i0 = (int) rp;
                const float fr = rp - (float) i0;
                const float a = u.buf[c][(size_t) (i0 & mask)], b = u.buf[c][(size_t) ((i0 + 1) & mask)];
                rd[c] = a + (b - a) * fr;
            }
            float w[2];
            if (style == 2) {
                const float mono = (in[0] + in[1]) * 0.70710678f;
                const float t0 = sat8(mono + rd[0] * fb[0]), t1 = sat8(0.0f + rd[1] * fb[1]);
                w[0] = t1; w[1] = t0;                                                          /* the sides swap */
            } else if (style == 3) {
                const float t0 = sat8(in[0] + rd[0] * fb[0]), t1 = sat8(in[1] + rd[1] * fb[1]);
                w[0] = t1; w[1] = t0;
            } else {
                w[0] = sat8(in[0] + rd[0] * fb[0]); w[1] = sat8(in[1] + rd[1] * fb[1]);
            }
            for (int c = 0; c < 2; c++) {
                const float x = w[c] * gain;
                float d = lc * (x - u.lpS[c]); u.lpS[c] += d; const float lp = u.lpS[c]; u.lpS[c] += d;
                d = hc * (lp - u.hpS[c]); u.hpS[c] += d; const float hp = u.hpS[c]; u.hpS[c] += d;
                u.buf[c][(size_t) u.pos] = sanitize(lp - hp);
                curP[c] += dP[c];
            }
            L[i] = dry * in[0] + wet * rd[0];
            R[i] = dry * in[1] + wet * rd[1];
            u.pos = (u.pos + 1) & mask;
        }
        u.wet = wetT; u.dry = dryT; u.fb[0] = fbT[0]; u.fb[1] = fbT[1]; u.gain = gainT; u.lc = lcT; u.hc = hcT;
        for (int c = 0; c < 2; c++) { u.lpS[c] = sanitize(u.lpS[c]); u.hpS[c] = sanitize(u.hpS[c]); }
    }

    /* Eight-line feedback delay network behind four series allpass diffusers. Decay time is
     * explicit: each line's gain is 10^(-3 len / (sr T60)), so T60 is the DC decay and the
     * damping filter shortens it further at high frequencies. */
    void processReverb(float *L, float *R, int n)
    {
        if (st.revVital) { processReverbVital(L, R, n); return; }
        const float tgtWet = fxdetail::kRevOut * revLevel(st.revT60) * wetGain(st.revMix);
        const float tgtDry = dryGain(st.revMix);
        const float dw = (tgtWet - revWetCur) / (float) n, dd = (tgtDry - revDryCur) / (float) n;
        const float aDamp = 1.0f - std::exp(-fxdetail::kTwoPi * std::min(st.revDampHz, sr * 0.45f) / sr);
        const float aHp = 1.0f - std::exp(-fxdetail::kTwoPi * st.revLoHz / sr);
        const float T = std::max(st.revT60, 0.05f);
        float g[kLines];
        float gSum = 0.0f;
        for (int i = 0; i < kLines; i++) {
            g[i] = std::pow(10.0f, -3.0f * (float) lines[i].len / (sr * T));
            gSum += g[i];
        }
        /* A long tail holds more energy for the same input, and Serum's reverb does not get
         * louder as its decay lengthens (steady noise at wet 100 stayed within +/-1.5 dB of
         * the dry level from size 0 to 75). So the injection is scaled by what the loop
         * would otherwise accumulate. */
        const float gAvg = gSum * (1.0f / (float) kLines);
        const float norm = std::sqrt(std::max(1.0f - gAvg * gAvg, 1e-4f));
        const float wd = std::min(std::max(st.revWidth, 0.0f), 1.0f);
        static const float sl[kLines] = { 1, 1, -1, -1, 1, 1, -1, -1 };
        static const float sr_[kLines] = { 1, -1, 1, -1, 1, -1, 1, -1 };
        const int preN = std::min((int) (st.revVDelayS * sr), 16000);                       /* the Pre-delay knob (0 = none: the input is used as it is) */
        if (preN > 0 && rpd.empty()) rpd.assign(16384, 0.0f);
        for (int i = 0; i < n; i++) {
            float x = 0.5f * (L[i] + R[i]);
            if (preN > 0) { rpd[(size_t) rpdw] = x; x = rpd[(size_t) ((rpdw - preN) & 16383)]; rpdw = (rpdw + 1) & 16383; }
            for (int k = 0; k < kAps; k++) {
                Allpass &a = aps[k];
                const int len = (int) a.buf.size();
                const float y = -fxdetail::kApGain * x + a.buf[(size_t) a.pos];
                a.buf[(size_t) a.pos] = sanitize(x + fxdetail::kApGain * y);
                if (++a.pos >= len) a.pos = 0;                    /* (a compare, not the integer division a modulo by a variable length costs per sample) */
                x = y;
            }
            float o[kLines];
            float sum = 0.0f;
            for (int k = 0; k < kLines; k++) {
                Line &l = lines[k];
                l.lp = sanitize(l.lp + aDamp * (l.buf[(size_t) l.pos] - l.lp));
                o[k] = l.lp * g[k];
                sum += o[k];
            }
            const float mix = sum * (2.0f / (float) kLines);       /* Householder: energy preserving */
            float wl = 0.0f, wr = 0.0f;
            for (int k = 0; k < kLines; k++) {
                Line &l = lines[k];
                l.buf[(size_t) l.pos] = sanitize(o[k] - mix + x * (0.35f * norm));
                if (++l.pos >= l.len) l.pos = 0;
                wl += sl[k] * o[k];
                wr += sr_[k] * o[k];
            }
            wl *= 0.5f; wr *= 0.5f;
            const float mid = 0.5f * (wl + wr), side = 0.5f * (wl - wr) * wd;
            wl = mid + side; wr = mid - side;
            revHp[0] = sanitize(revHp[0] + aHp * (wl - revHp[0])); wl -= revHp[0];
            revHp[1] = sanitize(revHp[1] + aHp * (wr - revHp[1])); wr -= revHp[1];
            revWetCur += dw; revDryCur += dd;
            L[i] = L[i] * revDryCur + wl * revWetCur;
            R[i] = R[i] * revDryCur + wr * revWetCur;
        }
        revWetCur = tgtWet; revDryCur = tgtDry;
    }

    /* Vital's reverb, ported from vital/src/synthesis/effects/reverb.cpp (GPL-3.0-or-later, Copyright 2013-2019 Matt Tytel; see THIRD-PARTY.md): a 4 x 4 grid of feedback lines
     * (lengths x 2^(size x 4 - 3), each read through a cubic interpolator with a slow chorus on the read position), every line behind an all-pass (delay about 570..1020 samples, gain 0.6,
     * each lane reading its neighbour's: the left / right swap), mixed by a Householder-like matrix over rows, columns and the whole grid, then a high shelf and a low shelf (one-poles) and the
     * decay gain 0.001^(line length / T60). The input is a band-pass (difference of two one-poles) at a quarter level into every line; the output adds the grid again through the same matrix. */
    struct VRev {
        std::vector<float> fb[16], ap[4], pd[2];
        int w = 0, pdw = 0;
        float pre[2][2] = {}, hiSh[4][4] = {}, loSh[4][4] = {}, phase = 0;
    };
    VRev vr;
    std::vector<float> rpd;                                     /* Serum reverb's pre-delay line */
    int rpdw = 0;
    void processReverbVital(float *L, float *R, int n)
    {
        static const int kAD[4][4] = { { 1001, 799, 933, 876 }, { 895, 807, 907, 853 }, { 957, 1019, 711, 567 }, { 833, 779, 663, 997 } };
        static const float kFD[4][4] = { { 6753.2f, 9278.4f, 7704.5f, 11328.5f }, { 9701.12f, 5512.5f, 8480.45f, 5638.65f }, { 3120.73f, 3429.5f, 3626.37f, 7713.52f }, { 4521.54f, 6518.97f, 5265.56f, 5630.25f } };
        constexpr int FB = 1 << 15, FM = FB - 1, AN = 1024, AM = AN - 1, PN = 1 << 14, PM = PN - 1;
        if (vr.fb[0].empty()) { for (auto &b : vr.fb) b.assign(FB, 0.0f); for (auto &b : vr.ap) b.assign(AN * 4, 0.0f); for (auto &b : vr.pd) b.assign(PN, 0.0f); }
        const float w01 = std::min(std::max(st.revMix, 0.0f), 1.0f);
        const float tgtWet = std::sin(w01 * 1.5707963f), tgtDry = std::cos(w01 * 1.5707963f);
        const float dw = (tgtWet - revWetCur) / (float) n, dd = (tgtDry - revDryCur) / (float) n;
        auto coef = [&](float note) { const float f = 440.0f * std::pow(2.0f, (std::min(std::max(note, 0.0f), 130.0f) - 69.0f) / 12.0f); const float x = f * 3.14159265f / sr; return std::tan(x / (x + 1.0f)); };
        const float cPreLo = coef(st.revVPreLo), cPreHi = coef(st.revVPreHi), cLo = coef(st.revVLoNote), cHi = coef(st.revVHiNote);
        const float loAmp = 1.0f - std::pow(10.0f, std::min(std::max(st.revVLoDb, -24.0f), 0.0f) / 20.0f), hiAmp = std::pow(10.0f, std::min(std::max(st.revVHiDb, -24.0f), 0.0f) / 20.0f);
        const float sizeMult = std::pow(2.0f, std::min(std::max(st.revVSize, 0.0f), 1.0f) * 4.0f - 3.0f);
        const float decaySamples = std::min(std::max(st.revVDecay, 0.1f), 100.0f) * 44100.0f, decayPeriod = sizeMult / decaySamples;
        const float ratio = sr / 44100.0f;
        float decay[4][4], delay[4][4];
        for (int c = 0; c < 4; c++) for (int l = 0; l < 4; l++) { decay[c][l] = std::pow(0.001f, kFD[c][l] * decayPeriod); delay[c][l] = sizeMult * kFD[c][l] * ratio; }
        const float chHz = std::min(std::max(st.revVChorusHz, 0.0f), 16.0f), phInc = chHz / sr;
        float amt[4], cr[4], ci[4];
        for (int l = 0; l < 4; l++) {
            float a = std::min(std::max(st.revVChorus, 0.0f), 1.0f) * 2500.0f * ratio;
            for (int c = 0; c < 4; c++) a = std::min(a, delay[c][l] - 32.0f);
            amt[l] = std::max(a, 0.0f) * sizeMult;
            const float ph = ((float) l * (1.0f / 16.0f) + vr.phase) * 6.2831853f;
            cr[l] = std::cos(ph); ci[l] = std::sin(ph);
        }
        const float rotR = std::cos(phInc * 6.2831853f), rotI = std::sin(phInc * 6.2831853f);
        vr.phase += (float) n * phInc; vr.phase -= std::floor(vr.phase);
        const float pdTarget = std::min(std::max(st.revVDelayS * sr, 3.0f), (float) (PN - 8));
        for (int i = 0; i < n; i++) {
            for (int l = 0; l < 4; l++) { const float r = cr[l] * rotR - ci[l] * rotI; ci[l] = ci[l] * rotR + cr[l] * rotI; cr[l] = r; }
            float fbr[4][4];
            for (int c = 0; c < 4; c++)
                for (int l = 0; l < 4; l++) {
                    const float m = (c < 2 ? cr[l] : ci[l]) * amt[l] * ((c & 1) ? -1.0f : 1.0f);
                    const float rp = (float) vr.w - (delay[c][l] + m) + (float) FB;
                    const int i1 = (int) rp; const float f = rp - (float) i1;
                    const float *b = vr.fb[c * 4 + l].data();
                    const float y0 = b[(i1 - 1) & FM], y1 = b[i1 & FM], y2 = b[(i1 + 1) & FM], y3 = b[(i1 + 2) & FM];
                    const float c1 = 0.5f * (y2 - y0), c2 = y0 - 2.5f * y1 + 2.0f * y2 - 0.5f * y3, c3 = 0.5f * (y3 - y0) + 1.5f * (y1 - y2);
                    fbr[c][l] = ((c3 * f + c2) * f + c1) * f + y1;
                }
            const float in[2] = { L[i], R[i] };
            float sc[2];
            for (int ch = 0; ch < 2; ch++) {
                float d = cPreHi * (in[ch] - vr.pre[1][ch]); vr.pre[1][ch] += d; const float hi = vr.pre[1][ch]; vr.pre[1][ch] += d;
                d = cPreLo * (in[ch] - vr.pre[0][ch]); vr.pre[0][ch] += d; const float lo = vr.pre[0][ch]; vr.pre[0][ch] += d;
                sc[ch] = sanitize(lo - hi) * 0.25f;
            }
            float ao[4][4], rows[4] = { 0, 0, 0, 0 }, cols[4] = { 0, 0, 0, 0 }, tot = 0.0f;
            for (int c = 0; c < 4; c++)
                for (int l = 0; l < 4; l++) {
                    const int src = l ^ 1;
                    const float apr = vr.ap[c][(size_t) (4 * ((vr.w - kAD[c][src] - 1) & AM) + src)];
                    const float adi = fbr[c][l] - apr * 0.6f;
                    vr.ap[c][(size_t) (4 * (vr.w & AM) + l)] = sanitize(sc[l & 1] + adi);
                    ao[c][l] = apr + adi * 0.6f;
                    rows[l] += ao[c][l]; cols[c] += ao[c][l]; tot += ao[c][l];
                }
            float wr[4][4], sto[4][4], rows2[4] = { 0, 0, 0, 0 }, cols2[4] = { 0, 0, 0, 0 }, tot2 = 0.0f;
            for (int c = 0; c < 4; c++)
                for (int l = 0; l < 4; l++) {
                    float v = ao[c][l] + 0.25f * tot - 0.5f * rows[l] - 0.5f * cols[c];
                    float &h = vr.hiSh[c][l]; float d = cHi * (v - h); h += d; const float hl = h; h += d;
                    v = hl + hiAmp * (v - hl);
                    float &g = vr.loSh[c][l]; d = cLo * (v - g); g += d; const float ll = g; g += d;
                    v -= ll * loAmp;
                    wr[c][l] = sanitize(v);
                    sto[c][l] = decay[c][l] * wr[c][l];
                    vr.fb[c * 4 + l][(size_t) vr.w] = sto[c][l];
                    rows2[l] += sto[c][l]; cols2[c] += sto[c][l]; tot2 += sto[c][l];
                }
            float t[4] = { 0, 0, 0, 0 };
            for (int c = 0; c < 4; c++)
                for (int l = 0; l < 4; l++) {
                    const float ff = sto[c][l] + 0.25f * tot2 - 0.5f * rows2[l] - 0.5f * cols2[c];
                    t[l] += wr[c][l] + 0.125f * ff * decay[c][l];
                }
            vr.w = (vr.w + 1) & FM;
            vr.pd[0][(size_t) vr.pdw] = t[0] + t[2]; vr.pd[1][(size_t) vr.pdw] = t[1] + t[3];
            const float rp = (float) vr.pdw - (pdTarget - 1.0f) + (float) PN;
            const int j = (int) rp; const float f = rp - (float) j;
            vr.pdw = (vr.pdw + 1) & PM;
            const float wl = vr.pd[0][(size_t) (j & PM)] * (1.0f - f) + vr.pd[0][(size_t) ((j + 1) & PM)] * f;
            const float wrr = vr.pd[1][(size_t) (j & PM)] * (1.0f - f) + vr.pd[1][(size_t) ((j + 1) & PM)] * f;
            revWetCur += dw; revDryCur += dd;
            L[i] = L[i] * revDryCur + wl * revWetCur;
            R[i] = R[i] * revDryCur + wrr * revWetCur;
        }
        revWetCur = tgtWet; revDryCur = tgtDry;
    }

    void clearWide() { std::fill(chorusBuf.begin(), chorusBuf.end(), 0.0f); wideCur = 0; }
    void clearDelay(DelayUnit &u)
    {
        std::fill(u.buf[0].begin(), u.buf[0].end(), 0.0f);
        std::fill(u.buf[1].begin(), u.buf[1].end(), 0.0f);
        u.fbLp[0] = u.fbLp[1] = u.fbHp[0] = u.fbHp[1] = 0;
        u.wetCur = 0; u.dryCur = 1;
    }
    /* The convolution reverb: out = cos(pi w / 2) dry + sin^2(pi w / 2) convolved, w = Wet (Serum's measured blend; the IR carries the Wet-100 level). */
    void processConv(float *L, float *R, int n)
    {
        if (!convP) { convWetCur = 0.0f; convDryCur = 1.0f; return; }
        const float w = std::min(std::max(st.convMix, 0.0f), 1.0f);
        const float s = std::sin(1.5707963f * w);
        const float tgtWet = s * s, tgtDry = std::cos(1.5707963f * w);
        const float dw = (tgtWet - convWetCur) / (float) n, dd = (tgtDry - convDryCur) / (float) n;
        for (int i = 0; i < n; i++) {
            float wl, wr;
            convP->step(L[i], R[i], wl, wr);
            convWetCur += dw; convDryCur += dd;
            L[i] = L[i] * convDryCur + wl * convWetCur;
            R[i] = R[i] * convDryCur + wr * convWetCur;
        }
        convWetCur = tgtWet; convDryCur = tgtDry;
    }

    void clearReverb()
    {
        for (auto &l : lines) { std::fill(l.buf.begin(), l.buf.end(), 0.0f); l.lp = 0; }
        for (auto &a : aps) std::fill(a.buf.begin(), a.buf.end(), 0.0f);
        revHp[0] = revHp[1] = 0;
        std::fill(rpd.begin(), rpd.end(), 0.0f);
        for (auto &b : vr.fb) std::fill(b.begin(), b.end(), 0.0f);
        for (auto &b : vr.ap) std::fill(b.begin(), b.end(), 0.0f);
        for (auto &b : vr.pd) std::fill(b.begin(), b.end(), 0.0f);
        for (int a = 0; a < 2; a++) vr.pre[0][a] = vr.pre[1][a] = 0.0f;
        for (int c = 0; c < 4; c++) for (int l = 0; l < 4; l++) vr.hiSh[c][l] = vr.loSh[c][l] = 0.0f;
        revWetCur = 0; revDryCur = 1;
    }

    float sr;
    FxSettings st;
    bool anyActive = false, baseActive = false, xfOn = false;
    bool xfDriveOn = false;                                   /* the Filter FX's Vital drive */
    int xfVm = 0;                                             /* the Filter FX in one of Vital's own models (setFilterModel) */
    float xfHz = 1000.0f, xfRes = 0.0f, xfBlend = 0.0f, xfCtrans = 0.0f;
    int xfCstyle = 0;
    CombFilter xfComb; double xfCombSr = 0.0;
    VitalSK xfSk; VitalLadder xfLd; VitalDirty xfDt; VitalDigital xfDg; VitalDiode xfDo;
    float xfDriveG = 1.0f, xfDriveT = 1.0f, xfDriveA = 1.0f, xfPass = 1.0f;  /* its gain as it was, as it is set to be, and its a = 1 / (1 + 2 resonance) */
    float xfWetKnob = 1.0f, xfS = 1.0f;                       /* the Filter FX Wet knob (0..1) and the smoothstep weight it has reached */
    Filter xf;
    ModFx mod;
    Hyper hyper;

    StereoBiquad eq[4];
    bool eqOn[4] = { false, false, false, false };
    float eqModSeen[8] = { 1e9f, 1e9f, 1e9f, 1e9f, 1e9f, 1e9f, 1e9f, 1e9f };         /* the offsets setEqMod last applied (1e9: the settings were just set, redo them) */
    StereoBiquad msEq[2][4];
    bool msOn[2][4] = {};
    bool msAny = false;
    bool bandEq = false, msMaster = false;                   /* a Split band's EQ (the Mid slots) / the Mid-Side EQ on the whole signal */

    std::vector<float> chorusBuf;
    int chorusPos = 0;
    float chorusPhase = 0.0f;
    float wideCur = 0.0f;
    bool wideRan = false;
    int fxSeq[7] = { 0, 1, 2, 3, 4, 5, 6 };

    DelayUnit dly[2];

    struct Line { std::vector<float> buf; int pos = 0; int len = 1; float lp = 0; };
    Line lines[kLines];
    struct Allpass { std::vector<float> buf; int pos = 0; };
    Allpass aps[kAps];
    std::shared_ptr<ConvPlan> convP;
    float convWetCur = 0.0f, convDryCur = 1.0f;
    bool convRan = false;
    float revWetCur = 0.0f, revDryCur = 1.0f;
    float revHp[2] = { 0, 0 };
    bool revRan = false;
    float eqCur = 1.0f, eqTarget = 1.0f;
    float compMixBase = 0.0f, compWetModSeen = 0.0f;           /* the pot's Comp mix, and the last Wet offset a route put on it */
    float distMixBase = 0.0f, distWetModSeen = 0.0f;          /* the pot's smoothstepped mix, and the last Wet offset a route put on it */
    float distCur = 0.0f, distTarget = 0.0f, distDc[2] = { 0.0f, 0.0f };
    int   dsCount = 0;
    float dsHold[2] = { 0.0f, 0.0f };
    StereoBiquad distPreBq;
    VitalDistFilter distVf;
    float vdsPhase = 0.0f; bool vdsNew = true;                  /* the Vital law's Down Sample: samples since the last hold, and whether this one starts a new hold */
    float distAudioHz = 261.63f, distAudioPhase = 0.0f;                           /* the audio-rate LFO's frequency (the newest note's) and phase */
    float distGainNow = 1.0f, distVModSeen[4] = { 0.0f, 0.0f, 0.0f, 0.0f };      /* the Vital law's gain as the routes leave it, and the offsets it was last set for */
    MbBq mbLp2[2], mbHp2[2], mbLp1[2], mbHp1[2], mbApLp[2], mbApHp[2];
    MbBq spLp[2], spHp[2];                  /* the Split crossover */
    float mbMs[3] = { 0, 0, 0 }, mbGain[3] = { 0, 0, 0 };
    VcBand vcBand[3];
    VcSplit vcSplitA, vcSplitB[2];            /* Vital's compressor: the 120 Hz split, and the 2.5 kHz split of the low and of the upper branch */
    float compCur = 0.0f, compTarget = 0.0f, compGrDb = 0.0f, compAtt = 0.0f, compRel = 0.0f, compMk = 1.0f, compDet = 0.0f, compMs = 0.0f;
};

} // namespace tb
