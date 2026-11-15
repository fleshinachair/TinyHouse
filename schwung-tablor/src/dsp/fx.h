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
#include "conv.h"
#include "../ported/filter.h"

namespace tb {

/* ------------------------------------------------------------------ pot maps */

enum EqType { EQ_OFF = 0, EQ_BELL, EQ_LOSHELF, EQ_HISHELF, EQ_HIPASS, EQ_LOPASS };

/* 20 Hz .. 20 kHz, exponential. */
inline float potEqFreq(float v) { return 20.0f * std::pow(1000.0f, pot01(v)); }
/* +/-24 dB, 64 is flat. */
inline float potEqGain(float v) { return potBipolar(v) * 24.0f; }

/* Q from the pot, which holds Serum's Reso (0..100) as 0..127. Serum's own mapping, measured
 * (calibration/fx_probe.py eqfit): a bell or a cut goes from very wide to narrow, and a
 * shelf's slope from gentle to resonant. */
inline float potEqQ(int type, float v)
{
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

    /* RBJ cookbook. fc is clamped below Nyquist so a 20 kHz band cannot go unstable. */
    void set(int type, float fc, float gainDb, float q, float sr)
    {
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
    float delay2Mix = 0, delay2Ms = 250, delay2RMs = 250, delay2Fb = 0.4f, delay2LoHz = 20, delay2HiHz = 16000;   /* a second delay after the first (Serum racks with two) */
    int   delay2Ping = 0;
    float revMix = 0, revT60 = 2.0f, revDampHz = 8000, revLoHz = 20, revWidth = 1.0f;
    float convMix = 0;        /* Serum's Conv Wet 0..1: the dry part cos(pi w / 2), the convolved part sin^2(pi w / 2) of the Wet-100 level (calibration/probe_conv_blend.py) */
    struct Band { int type = EQ_OFF; float hz = 1000, gainDb = 0, q = 1; } band[4];
    Band msBand[2][4];        /* the EQs inside a SplitMS node: [0] the Mid branch, [1] the Side branch (fxmap.ms_branches) */
    bool eqEnable = true;     /* the EQ's own switch: it fades in and out, the bands keep running */
    float distDrive = 0, distMix = 1;       /* distortion: drive 0..1 (0 is off), wet part 0..1 */
    int   distMode = 0;                     /* 0 soft (tanh), 1 asymmetric, 2 hard clip, 3 sine, 4 hold (the calibrated law only) */
    int   distHold = 1;                     /* mode 4: Serum's Downsample, a sample-and-hold of this many samples */
    bool  distCal = false;                  /* the calibrated law: out = level (1 - s^2) x + s out curve(gain x), s = distMix */
    float distGain = 1, distOut = 1, distLevel = 1;
    int   distPre = 0;                      /* a filter before the curve, on the distorted part: 0 off, 1 low-pass, 2 high-pass (the calibrated law only) */
    float distPreHz = 1000, distPreQ = 1;
    float compMix = 0;                      /* compressor: parallel part 0..1 (0 is off) */
    float compThreshDb = 0, compRatio = 1, compAttackMs = 10, compReleaseMs = 100, compMakeupDb = 0, compKneeDb = 12;
    bool  compMulti = false;                /* three bands, each with its own threshold and gain, and upward compression below */
    float compThreshBandDb[3] = { 0, 0, 0 }, compBandGainDb[3] = { 0, 0, 0 }, compBelow = 0;
    /* Serum's Chorus, Flanger and Phaser. Each mix is 0 when its switch is off (the stage then fades out and is skipped). */
    float chorMix = 0, chorRate = 0.5f, chorD1Ms = 5.0f, chorD2Ms = 5.2f, chorDepth = 0.5f, chorFb = 0, chorFiltHz = 20000;
    bool  chorHpf = false;
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
/* Reverb line lengths in samples at 44.1 kHz (mutually prime, 25..59 ms) and the input
 * diffusers. The output level kRevOut was measured against Serum's reverb. */
inline constexpr int kLineLen[8] = { 1087, 1283, 1481, 1699, 1907, 2131, 2347, 2593 };
inline constexpr int kApLen[4] = { 113, 197, 311, 431 };
inline constexpr float kApGain = 0.6f;
inline constexpr float kRevOut = 2.25f;          /* Serum's tail on a played note, held 1 s and released (calibration/reverb_engine_only.py, 54 presets with a master reverb): with the older 1.50
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
        if (st.chorMix > 0 || chorCur > 0) { processChorus(L, R, n); chorRan = true; }
        else if (chorRan) { clearChorus(); chorRan = false; }
        if (st.flanMix > 0 || flanCur > 0) { processFlanger(L, R, n); flanRan = true; }
        else if (flanRan) { clearFlanger(); flanRan = false; }
        if (st.phasMix > 0 || phasCur > 0) { processPhaser(L, R, n); phasRan = true; }
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

    void clearChorus() { for (int c = 0; c < 2; c++) { std::fill(chBuf[c].begin(), chBuf[c].end(), 0.0f); chFb[c] = chLp[c] = chLp2[c] = 0.0f; } chorCur = 0.0f; }
    void clearFlanger() { for (int c = 0; c < 2; c++) { std::fill(flBuf[c].begin(), flBuf[c].end(), 0.0f); flFb[c] = 0.0f; } flanCur = 0.0f; }
    void clearPhaser() { for (int c = 0; c < 2; c++) { for (int k = 0; k < kMaxPoles; k++) apX[c][k] = apY[c][k] = 0.0f; phFb[c] = 0.0f; } phasCur = 0.0f; }

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
                const float t0 = tap(chBuf[c], chWp, d1 + swing * std::sin(fxdetail::kTwoPi * (chPh + off[c][0])));
                const float t1 = tap(chBuf[c], chWp, d2 + swing * std::sin(fxdetail::kTwoPi * (chPh + off[c][1])));
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
                const float lfo = 0.5f + 0.5f * std::sin(fxdetail::kTwoPi * (flPh + (c ? st.flanPhase : 0.0f)));
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
                    const float lfo = std::sin(fxdetail::kTwoPi * (phPh + (c ? st.phasPhase : 0.0f)));
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
        for (int i = 0; i < n; i++) {
            cur += dg;
            float *io[2] = { &L[i], &R[i] };
            for (int c = 0; c < 2; c++) {
                const float x = *io[c];
                buf[c][(size_t) wp] = x;
                float acc = 0.0f;
                for (int v = 0; v < nv; v++) {
                    const float tri = 4.0f * std::fabs(ph[c][v] - 0.5f) - 1.0f;                       /* +1 at phase 0, -1 at 0.5 */
                    const float d = 2.0f + 0.5f * swing * (1.0f + tri);
                    const float rp = (float) wp - d + (float) kBuf;
                    const int i1 = (int) rp;
                    const float fr = rp - (float) i1;
                    const float y0 = buf[c][(size_t) ((i1 - 1) & (kBuf - 1))], y1 = buf[c][(size_t) (i1 & (kBuf - 1))];
                    const float y2 = buf[c][(size_t) ((i1 + 1) & (kBuf - 1))], y3 = buf[c][(size_t) ((i1 + 2) & (kBuf - 1))];
                    const float c1 = 0.5f * (y2 - y0), c2 = y0 - 2.5f * y1 + 2.0f * y2 - 0.5f * y3, c3 = 0.5f * (y3 - y0) + 1.5f * (y1 - y2);
                    acc += ((c3 * fr + c2) * fr + c1) * fr + y1;
                    ph[c][v] += rateHz * spd[c][v] / sr;
                    ph[c][v] -= std::floor(ph[c][v]);
                }
                /* Serum's wet path has no bass: a 12 dB/oct high-pass near 150 Hz (white noise through the cloud, third-octave gain re the dry: -2 dB at 159 Hz, -7 at 126, -14 at 63, -20 at 50) */
                const float hp = hpB0 * acc + hpS1[c];
                hpS1[c] = hpB1 * acc - hpA1 * hp + hpS2[c];
                hpS2[c] = hpB2 * acc - hpA2 * hp;
                *io[c] = x * dryGain(cur) + hp * voiceGain * wetGain(cur);
            }
            wp = (wp + 1) & (kBuf - 1);
        }
        cur = target;
        if (!on && cur <= 0.0f) { for (int c = 0; c < 2; c++) std::fill(buf[c].begin(), buf[c].end(), 0.0f); }
    }

private:
    /* fitted to Serum's HyperD alone on a sine and a saw (calibration/hyperd_fit.py) */
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

    void set(const FxSettings &s)
    {
        st = s;
        distMixBase = s.distMix; distWetModSeen = 0.0f;
        compMixBase = s.compMix; compWetModSeen = 0.0f;
        if (s.distPre) distPreBq.c.set(s.distPre == 1 ? EQ_LOPASS : EQ_HIPASS, s.distPreHz, 0.0f, s.distPreQ, sr);
        mod.set(s);
        hyper.set(s);
        if (s.splitBand == 1 || s.splitBand == 2) {
            for (int i = 0; i < 2; i++) { spLp[i].set(false, s.splitHz, sr); spHp[i].set(true, s.splitHz, sr); }
        }
        eqTarget = s.eqEnable ? 1.0f : 0.0f;
        distTarget = s.distCal ? (s.distMix > 0.0f || std::fabs(s.distLevel - 1.0f) > 1e-3f ? 1.0f : 0.0f) : (s.distDrive > 0.0f ? s.distMix : 0.0f);
        compTarget = (s.compRatio > 1.001f || s.compMulti) ? s.compMix : 0.0f;
        compDet = 1.0f - std::exp(-1.0f / (0.020f * sr));                       /* 20 ms rms window */
        compAtt = std::exp(-1.0f / (std::max(s.compAttackMs, 0.05f) * 0.001f * sr));
        compRel = std::exp(-1.0f / (std::max(s.compReleaseMs, 1.0f) * 0.001f * sr));
        compMk = std::pow(10.0f, s.compMakeupDb / 20.0f);
        if (s.compMulti) {                                   /* Linkwitz-Riley 4th order crossovers at 120 Hz and 2.4 kHz */
            for (int i = 0; i < 2; i++) {
                mbLp2[i].set(false, 2400.0f, sr); mbHp2[i].set(true, 2400.0f, sr);
                mbLp1[i].set(false, 120.0f, sr);  mbHp1[i].set(true, 120.0f, sr);
                mbApLp[i].set(false, 120.0f, sr); mbApHp[i].set(true, 120.0f, sr);   /* the low crossover's phase on the high band, so the bands sum flat */
            }
        }
        bool eqAny = false;
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

    /* The Filter FX: a filter after the distortion, its cutoff set by the engine each block (it follows the filter envelope).
     * type is the voice filter's list (LP/HP/BP/notch, 12 and 24 dB). */
    bool filterOn() const { return xfOn; }

    /* A route from an envelope or an LFO to the Distortion's Wet (Serum: the Wet knob, in its percent domain, moves by the route's amount x the source): the calibrated law's smoothstep
     * s is taken back to the Wet knob it came from, the offset added, and the smoothstep applied again. Called every block by the engine; 0 leaves the pot's mix as it is. */
    void setDistWetMod(float m)
    {
        if (!st.distCal) return;
        if (m == distWetModSeen) return;
        distWetModSeen = m;
        float s = distMixBase;
        if (m != 0.0f) {
            const float w0 = 0.5f - std::sin(std::asin(std::min(std::max(1.0f - 2.0f * std::min(std::max(distMixBase, 0.0f), 1.0f), -1.0f), 1.0f)) / 3.0f);
            const float w = std::min(std::max(w0 + m, 0.0f), 1.0f);
            s = w * w * (3.0f - 2.0f * w);
        }
        st.distMix = s;
        distTarget = (s > 0.0f || std::fabs(st.distLevel - 1.0f) > 1e-3f) ? 1.0f : 0.0f;
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

    /* wet: the Filter FX's Wet knob as a fraction of 0..100 %. Serum mixes it as a smoothstep s with the dry part kept, (1 - s^2) dry + s filtered (calibration/probe_fxfilter_wet.py). */
    void setFilter(bool on, int type, float hz, float q, float wet = 1.0f)
    {
        xfWetKnob = std::min(std::max(wet, 0.0f), 1.0f);
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
        }
        xfOn = on;
        anyActive = baseActive || xfOn;
    }

    /* The Filter FX at its Wet: the filtered signal at s and the dry at 1 - s^2 (s ramps towards the knob's smoothstep, so a moving Wet does not zip). */
    void processFilterFx(float *L, float *R, int n)
    {
        const float tgt = xfWetKnob * xfWetKnob * (3.0f - 2.0f * xfWetKnob);
        if (tgt >= 0.9999f && xfS >= 0.9999f) { xfS = 1.0f; xf.process(L, R, n); return; }
        float dL[128], dR[128];
        for (int o = 0; o < n; o += 128) {
            const int m = std::min(128, n - o);
            std::copy(L + o, L + o + m, dL);
            std::copy(R + o, R + o + m, dR);
            xf.process(L + o, R + o, m);
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
        if (!(inBand & 2) && (distTarget > 0.0f || distCur > 0.0f)) processDist(L, R, n);
        if (xfOn) processFilterFx(L, R, n);
        if (compTarget > 0.0f || compCur > 0.0f) {
            if (st.compMulti) processCompMulti(L, R, n); else processComp(L, R, n);
        } else { compGrDb = 0.0f; compMs = 0.0f; for (int b = 0; b < 3; b++) { mbMs[b] = 0.0f; mbGain[b] = 0.0f; } }

        if (!(inBand & 4) && mod.active()) mod.process(L, R, n);

        const bool wideNow = st.wideMix > 0.0f || wideCur > 0.0f;
        if (wideNow && !(inBand & 32)) { processWide(L, R, n); wideRan = true; }               /* (with bit 32 the width works on the Split band, in processBandChunk) */
        else if (!wideNow && wideRan) { clearWide(); wideRan = false; }

        {
            const DelayPrm q[2] = { { st.delayMix, st.delayMs, st.delayRMs, st.delayFb, st.delayLoHz, st.delayHiHz, st.delayPing },
                                    { st.delay2Mix, st.delay2Ms, st.delay2RMs, st.delay2Fb, st.delay2LoHz, st.delay2HiHz, st.delay2Ping } };
            for (int k = 0; k < 2; k++) {
                if (q[k].mix > 0.0f || dly[k].wetCur > 0.0f) { processDelay(dly[k], q[k], L, R, n); dly[k].ran = true; }
                else if (dly[k].ran) { clearDelay(dly[k]); dly[k].ran = false; }
            }
        }

        if (!(inBand & 64) && convNow) { processConv(L, R, n); convRan = true; }                /* (with bit 64 the convolver works on the Split band, in processBandChunk) */
        else if (!convNow && convRan) { if (convP) convP->reset(); convRan = false; }

        if (!(inBand & 128) && revNow) { processReverb(L, R, n); revRan = true; }              /* (likewise bit 128: the reverb) */
        else if (!revNow && revRan) { clearReverb(); revRan = false; }

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

    static constexpr float kMaxBoostDb = 12.0f;

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
                const float xdb = 10.0f * std::log10(mbMs[b] + 1e-18f), T = st.compThreshBandDb[b];
                /* The upward part has no end in a silent tail: a band at -180 dB asked for +135 dB, and the next note arrived into that gain
                 * (Serum's own stays level across repeated notes). It is capped, so the boost a quiet tail earns stays one a note can recover from. */
                const float up = std::min(sb * kneeCurve((T - 11.0f) - xdb, 1.0f, knee), kMaxBoostDb);
                const float target = up - kneeCurve(xdb - T, slope, knee);   /* dB: + boosts, - reduces */
                mbGain[b] = target + (target < mbGain[b] ? compAtt : compRel) * (mbGain[b] - target);
                const float g = std::pow(10.0f, (mbGain[b] + st.compBandGainDb[b]) / 20.0f);
                out[0] += band[b][0] * g;
                out[1] += band[b][1] * g;
            }
            const float w = compCur;
            L[i] += w * (out[0] * compMk - L[i]);
            R[i] += w * (out[1] * compMk - R[i]);
        }
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
            const float xdb = 10.0f * std::log10(compMs + 1e-18f) + 4.77f;      /* the rms and a saw's crest: the level the curves were fitted at */
            const float over = xdb - T;
            float targetGr;                              /* gain reduction wanted, dB (>= 0) */
            if (over <= -0.5f * knee) targetGr = 0.0f;
            else if (over >= 0.5f * knee) targetGr = slope * over;
            else { const float k = over + 0.5f * knee; targetGr = slope * k * k / (2.0f * knee); }
            const float c = targetGr > compGrDb ? compAtt : compRel;
            compGrDb = targetGr + c * (compGrDb - targetGr);
            const float g = std::pow(10.0f, -compGrDb / 20.0f) * compMk;
            const float w = compCur;
            L[i] += w * (L[i] * g - L[i]);
            R[i] += w * (R[i] * g - R[i]);
        }
        compCur = compTarget;
    }

    /* The stages of a Split / SplitMS branch: the band (or Mid / Side) goes through them and joins the rest again. A 4th-order Linkwitz-Riley split (two Butterworth biquads each way) sums
     * flat, so a branch that changes nothing leaves the signal as it was (calibration/probe_split_band.py: Serum's Split is -6 dB at Freq and 24 dB/octave). */
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

    /* The calibrated law (calibration/probe_dist_transfer.py, probe_dist_wet.py, calibrate_dist2.py): Serum's Wet is a smoothstep s with the dry part
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
            float w[2];
            for (int side = 0; side < 2; side++) {
                float acc = 0.0f;
                for (int tap = 0; tap < 2; tap++) {
                    const float ph = chorusPhase + side * 3.14159265f + tap * 1.5707963f;
                    const float d = baseD + (tap ? 0.0007f * sr : 0.0f) + depthD * std::sin(ph);
                    const float rp = (float) chorusPos - d + (float) kChorusMax;
                    const int i0 = (int) rp;
                    const float fr = rp - (float) i0;
                    const float a = chorusBuf[(size_t) (i0 % kChorusMax)];
                    const float b = chorusBuf[(size_t) ((i0 + 1) % kChorusMax)];
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
            chorusPos = (chorusPos + 1) % kChorusMax;
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

    /* Eight-line feedback delay network behind four series allpass diffusers. Decay time is
     * explicit: each line's gain is 10^(-3 len / (sr T60)), so T60 is the DC decay and the
     * damping filter shortens it further at high frequencies. */
    void processReverb(float *L, float *R, int n)
    {
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
        for (int i = 0; i < n; i++) {
            float x = 0.5f * (L[i] + R[i]);
            for (int k = 0; k < kAps; k++) {
                Allpass &a = aps[k];
                const int len = (int) a.buf.size();
                const float y = -fxdetail::kApGain * x + a.buf[(size_t) a.pos];
                a.buf[(size_t) a.pos] = sanitize(x + fxdetail::kApGain * y);
                a.pos = (a.pos + 1) % len;
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
                l.pos = (l.pos + 1) % l.len;
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
        revWetCur = 0; revDryCur = 1;
    }

    float sr;
    FxSettings st;
    bool anyActive = false, baseActive = false, xfOn = false;
    float xfWetKnob = 1.0f, xfS = 1.0f;                       /* the Filter FX Wet knob (0..1) and the smoothstep weight it has reached */
    Filter xf;
    ModFx mod;
    Hyper hyper;

    StereoBiquad eq[4];
    bool eqOn[4] = { false, false, false, false };
    StereoBiquad msEq[2][4];
    bool msOn[2][4] = {};
    bool msAny = false;
    bool bandEq = false, msMaster = false;                   /* a Split band's EQ (the Mid slots) / the Mid-Side EQ on the whole signal */

    std::vector<float> chorusBuf;
    int chorusPos = 0;
    float chorusPhase = 0.0f;
    float wideCur = 0.0f;
    bool wideRan = false;

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
    MbBq mbLp2[2], mbHp2[2], mbLp1[2], mbHp1[2], mbApLp[2], mbApHp[2];
    MbBq spLp[2], spHp[2];                  /* the Split crossover */
    float mbMs[3] = { 0, 0, 0 }, mbGain[3] = { 0, 0, 0 };
    float compCur = 0.0f, compTarget = 0.0f, compGrDb = 0.0f, compAtt = 0.0f, compRel = 0.0f, compMk = 1.0f, compDet = 0.0f, compMs = 0.0f;
};

} // namespace tb
