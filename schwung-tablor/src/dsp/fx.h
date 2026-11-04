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
    float revMix = 0, revT60 = 2.0f, revDampHz = 8000, revLoHz = 20, revWidth = 1.0f;
    struct Band { int type = EQ_OFF; float hz = 1000, gainDb = 0, q = 1; } band[4];
    bool eqEnable = true;     /* the EQ's own switch: it fades in and out, the bands keep running */
    float distDrive = 0, distMix = 1;       /* distortion: drive 0..1 (0 is off), wet part 0..1 */
    int   distMode = 0;                     /* 0 soft (tanh), 1 asymmetric, 2 hard clip, 3 sine (the calibrated law only) */
    bool  distCal = false;                  /* the calibrated law: out = level (1 - s^2) x + s out curve(gain x), s = distMix */
    float distGain = 1, distOut = 1, distLevel = 1;
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

class Fx {
public:
    static constexpr int kLines = 8;
    static constexpr int kDelayMax = 1 << 17;       /* 2.97 s at 44.1 kHz */
    static constexpr int kChorusMax = 2048;         /* 46 ms */
    static constexpr int kAps = 4;

    explicit Fx(float sampleRate = 44100.0f) : sr(sampleRate)
    {
        chorusBuf.assign(kChorusMax, 0.0f);
        delayBuf[0].assign(kDelayMax, 0.0f);
        delayBuf[1].assign(kDelayMax, 0.0f);
        const float scale = sr / 44100.0f;
        for (int i = 0; i < kLines; i++) {
            lines[i].len = std::max(16, (int) (fxdetail::kLineLen[i] * scale));
            lines[i].buf.assign((size_t) lines[i].len, 0.0f);
        }
        for (int i = 0; i < kAps; i++)
            aps[i].buf.assign((size_t) std::max(8, (int) (fxdetail::kApLen[i] * scale)), 0.0f);
    }

    /* True if any stage would change the signal: the engine skips process() otherwise. */
    bool active() const { return anyActive; }

    void set(const FxSettings &s)
    {
        st = s;
        mod.set(s);
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
        /* A stage still fading out has to keep running, or it would cut off. */
        baseActive = eqAny || distTarget > 0.0f || distCur > 0.0f || compTarget > 0.0f || compCur > 0.0f || s.wideMix > 0.0f || s.delayMix > 0.0f || s.revMix > 0.0f ||
                    wideCur > 0.0f || delayWetCur > 0.0f || revWetCur > 0.0f || wideRan || delayRan || revRan || mod.active();
        anyActive = baseActive || xfOn;
    }

    /* The Filter FX: a filter after the distortion, its cutoff set by the engine each block (it follows the filter envelope).
     * type is the voice filter's list (LP/HP/BP/notch, 12 and 24 dB). */
    bool filterOn() const { return xfOn; }

    void setFilter(bool on, int type, float hz, float q)
    {
        if (on) {
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

    void process(float *L, float *R, int n)
    {
        if (distTarget > 0.0f || distCur > 0.0f) processDist(L, R, n);
        if (xfOn) xf.process(L, R, n);
        if (compTarget > 0.0f || compCur > 0.0f) {
            if (st.compMulti) processCompMulti(L, R, n); else processComp(L, R, n);
        } else { compGrDb = 0.0f; compMs = 0.0f; for (int b = 0; b < 3; b++) { mbMs[b] = 0.0f; mbGain[b] = 0.0f; } }

        if (mod.active()) mod.process(L, R, n);

        if (st.wideMix > 0.0f || wideCur > 0.0f) { processWide(L, R, n); wideRan = true; }
        else if (wideRan) { clearWide(); wideRan = false; }

        if (st.delayMix > 0.0f || delayWetCur > 0.0f) { processDelay(L, R, n); delayRan = true; }
        else if (delayRan) { clearDelay(); delayRan = false; }

        if (st.revMix > 0.0f || revWetCur > 0.0f) { processReverb(L, R, n); revRan = true; }
        else if (revRan) { clearReverb(); revRan = false; }

        /* The bands run whether or not the EQ is switched on, so the filters hold the signal's state and
         * switching never starts one from silence; only their output is crossfaded (about 10 ms). With
         * the EQ on, a sample goes through the bands in the same order as before, bit for bit. */
        bool eqAny = false;
        for (int b = 0; b < 4; b++) eqAny = eqAny || eqOn[b];
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
                float f;
                switch (st.distMode) {
                case 1:  f = std::tanh(g * (v + bias)) - biasOut; break;
                case 2:  f = std::min(std::max(g * v, -1.0f), 1.0f); break;
                case 3:  f = std::sin(g * v); break;
                default: f = std::tanh(g * v); break;
                }
                float o = lv * (dry * v + s * k * f);
                distDc[c] += dcA * (o - distDc[c]);              /* every curve can leave a DC term on a loud or lopsided input (the bias, a sine fold of an offset): taken out of the sum */
                y[c] = o - distDc[c];
            }
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
    void processDelay(float *L, float *R, int n)
    {
        const float tgtWet = fxdetail::kDelayOut * wetGain(st.delayMix);
        const float tgtDry = dryGain(st.delayMix);
        const float dw = (tgtWet - delayWetCur) / (float) n, dd = (tgtDry - delayDryCur) / (float) n;
        const float lim = (float) (kDelayMax - 4);
        const float tgtT[2] = { std::min(st.delayMs * 0.001f * sr, lim), std::min(st.delayRMs * 0.001f * sr, lim) };
        if (!delayRan) { delayTimeCur[0] = tgtT[0]; delayTimeCur[1] = tgtT[1]; }   /* no glide from zero */
        const float slew = 1.0f - std::exp(-1.0f / (0.05f * sr));       /* a knob turn glides over 50 ms */
        const float aHp = 1.0f - std::exp(-fxdetail::kTwoPi * st.delayLoHz / sr);
        const float aLp = 1.0f - std::exp(-fxdetail::kTwoPi * std::min(st.delayHiHz, sr * 0.45f) / sr);
        const float fb = st.delayFb;
        /* Serum's delay filter has unity gain at its centre whatever its width (calibration/probe_delay_filter2.json: 0.0 dB at Freq for BW 0.75..9). The two one-poles lose
         * R / (1 + R) at their geometric centre, R being the ratio of the corners: a narrow filter lost 2.6 dB per pass, so its echoes died faster than Serum's. */
        const float cornerRatio = std::max(std::min(st.delayHiHz, sr * 0.45f) / std::max(st.delayLoHz, 1.0f), 1.0001f);
        const float makeup = (1.0f + cornerRatio) / cornerRatio;
        const int mask = kDelayMax - 1;
        float *bl = delayBuf[0].data(), *br = delayBuf[1].data();
        for (int i = 0; i < n; i++) {
            for (int c = 0; c < 2; c++) delayTimeCur[c] += (tgtT[c] - delayTimeCur[c]) * slew;
            float echo[2];
            for (int c = 0; c < 2; c++) {
                const float *buf = c ? br : bl;
                const float rp = (float) delayPos - delayTimeCur[c] + (float) kDelayMax;
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
                fbHp[c] = sanitize(fbHp[c] + aHp * (echo[c] - fbHp[c]));
                const float hp = echo[c] - fbHp[c];
                fbLp[c] = sanitize(fbLp[c] + aLp * (hp - fbLp[c]));
                ef[c] = fbLp[c] * makeup;
            }
            float f[2];
            if (st.delayPing) { f[0] = ef[1] * fb; f[1] = 0.0f; }
            else { f[0] = ef[0] * fb; f[1] = ef[1] * fb; }
            if (st.delayPing) {
                bl[delayPos] = sanitize(0.5f * (L[i] + R[i]) + f[0]);
                br[delayPos] = sanitize(ef[0]);
            } else {
                bl[delayPos] = sanitize(L[i] + f[0]);
                br[delayPos] = sanitize(R[i] + f[1]);
            }
            delayWetCur += dw; delayDryCur += dd;
            L[i] = L[i] * delayDryCur + ef[0] * delayWetCur;
            R[i] = R[i] * delayDryCur + ef[1] * delayWetCur;
            delayPos = (delayPos + 1) & mask;
        }
        delayWetCur = tgtWet; delayDryCur = tgtDry;
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
    void clearDelay()
    {
        std::fill(delayBuf[0].begin(), delayBuf[0].end(), 0.0f);
        std::fill(delayBuf[1].begin(), delayBuf[1].end(), 0.0f);
        fbLp[0] = fbLp[1] = fbHp[0] = fbHp[1] = 0;
        delayWetCur = 0; delayDryCur = 1;
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
    Filter xf;
    ModFx mod;

    StereoBiquad eq[4];
    bool eqOn[4] = { false, false, false, false };

    std::vector<float> chorusBuf;
    int chorusPos = 0;
    float chorusPhase = 0.0f;
    float wideCur = 0.0f;
    bool wideRan = false;

    std::vector<float> delayBuf[2];
    int delayPos = 0;
    float delayWetCur = 0.0f, delayDryCur = 1.0f;
    float delayTimeCur[2] = { 0, 0 };
    float fbLp[2] = { 0, 0 }, fbHp[2] = { 0, 0 };
    bool delayRan = false;

    struct Line { std::vector<float> buf; int pos = 0; int len = 1; float lp = 0; };
    Line lines[kLines];
    struct Allpass { std::vector<float> buf; int pos = 0; };
    Allpass aps[kAps];
    float revWetCur = 0.0f, revDryCur = 1.0f;
    float revHp[2] = { 0, 0 };
    bool revRan = false;
    float eqCur = 1.0f, eqTarget = 1.0f;
    float distCur = 0.0f, distTarget = 0.0f, distDc[2] = { 0.0f, 0.0f };
    MbBq mbLp2[2], mbHp2[2], mbLp1[2], mbHp1[2], mbApLp[2], mbApHp[2];
    float mbMs[3] = { 0, 0, 0 }, mbGain[3] = { 0, 0, 0 };
    float compCur = 0.0f, compTarget = 0.0f, compGrDb = 0.0f, compAtt = 0.0f, compRel = 0.0f, compMk = 1.0f, compDet = 0.0f, compMs = 0.0f;
};

} // namespace tb
