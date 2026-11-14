/* The granular oscillator: Serum's Granular oscillator as a mode of a sample slot (see docs/GRANULAR_PROBE_NOTES.md for every number below, measured on Serum 2, and
 * docs/GRANULAR_DESIGN.md for the plan).
 *
 *   - Grains start on a strict grid every T / N seconds, T = the grain length, N = the number of grains sounding at once (Serum's Density). Both are held as Serum KNOB positions
 *     0..1, because a modulation route adds to the knob: N = 30 * kd (plain Density = 1000 kd^4), T = 10 * kl^4 seconds (Free mode; BPM mode passes T in directly).
 *   - A grain plays the file at the key's rate (as the sample slot does) from a read position that moves at ScanRate % of real time, under a window drawn over its length.
 *   - Each grain is scaled by kGrainGain[M], M = floor(N) + 1, the measured fall in level as grains pile up.
 *   - The grid begins at note-on. Grains of the grid from before the note exist only when their birth read position lies inside the file (Scan 0, or a Start offset).
 *
 * Rendered grain-major: one tight loop per live grain over the block, like SamplePlayer::stream. Not here yet (probe plan P4, P5, P7-P11): loop modes, the random amounts,
 * unison, BPM-mode lengths, warps on the grains, stereo pan. A negative scan rate is a guess (it runs from the file's end) until P4. */
#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>

#include "sample.h"

namespace tb {

/* Level factor for M grains at once, M = floor(N) + 1, measured (index 0 unused; 31 is the step at Density exactly 1000). */
static const float kGrainGain[32] = {
    1.0f,   1.0000f, 0.5957f, 0.5730f, 0.5545f, 0.5388f, 0.5249f, 0.5125f, 0.5012f, 0.4908f, 0.4812f, 0.4722f, 0.4638f, 0.4558f, 0.4483f, 0.4412f,
    0.4344f, 0.4279f, 0.4217f, 0.4158f, 0.4100f, 0.4045f, 0.3993f, 0.3942f, 0.3892f, 0.3844f, 0.3798f, 0.3753f, 0.3710f, 0.3668f, 0.3627f, 0.3587f };

enum GrainWindow { kWinTukey = 0, kWinExpDec, kWinTriangle, kWinBlackmanHarris, kWinGaussian };

/* Density knob 0..1 -> N, the grains sounding at once. */
inline float grainDensityN(float kd) { return 30.0f * std::clamp(kd, 0.0f, 1.0f); }
/* Grain Length knob 0..1 (Free mode) -> seconds. */
inline float grainLengthSeconds(float kl) { const float k = std::clamp(kl, 0.0f, 1.0f); const float k2 = k * k; return 10.0f * k2 * k2; }

/* One window shape over x = age / T in 0..1 (measured: shape, Param 0..100, Skew -100..100). Peak 1. */
inline float grainWindow(int shape, float param, float skew, float x)
{
    const float a = std::clamp(param, 0.0f, 100.0f) * 0.01f;
    if (shape != kWinExpDec) {                                 /* Skew moves the window's centre to c = 0.5 + skew / 200 (a piecewise-linear time warp); ExpDec ignores it */
        const float c = 0.5f + std::clamp(skew, -100.0f, 100.0f) / 200.0f;
        if (c <= 0.0f) x = 0.5f + 0.5f * x;
        else if (c >= 1.0f) x = 0.5f * x;
        else x = x < c ? 0.5f * x / c : 0.5f + 0.5f * (x - c) / (1.0f - c);
    }
    const float pi = 3.14159265358979f;
    switch (shape) {
    case kWinExpDec: { const float k = param * 0.1f; return std::exp(-k * k * x); }
    case kWinTriangle: return (1.0f - a) + a * (1.0f - std::fabs(2.0f * x - 1.0f));
    case kWinBlackmanHarris: {
        const float bh = 0.35875f - 0.48829f * std::cos(2.0f * pi * x) + 0.14128f * std::cos(4.0f * pi * x) - 0.01168f * std::cos(6.0f * pi * x);
        return (1.0f - a) + a * bh;
    }
    case kWinGaussian: {
        if (param <= 0.0f) return 1.0f;
        const float d = x - 0.5f;
        return std::exp(-d * d / (2.0f * (0.05f / param)));      /* sigma^2 = 0.05 / Param */
    }
    default: {                                                  /* Tukey: raised-cosine tapers of a/2 at each end, flat between; a = 1 is Hann */
        if (a <= 0.0f) return 1.0f;
        const float h = a * 0.5f;
        if (x < h) return 0.5f * (1.0f - std::cos(pi * x / h));
        if (x > 1.0f - h) return 0.5f * (1.0f - std::cos(pi * (1.0f - x) / h));
        return 1.0f;
    }
    }
}

struct GrainParams {
    float kd = 0.25f;                 /* Density knob (Serum default 0.25: N = 7.5) */
    float lenSeconds = 0.625f;        /* grain length, seconds of output time (Serum default: knob 0.5). Free mode: grainLengthSeconds(kl) */
    float scanPct = 100.0f;           /* Scan Rate, percent of real time */
    float start = 0.0f;               /* Start, 0..1 of the file */
    int   winShape = kWinTukey;
    float winParam = 100.0f;          /* Tukey 100 = Hann */
    float winSkew = 0.0f;
};

struct GrainCloud {
    static constexpr int kPool = 64;
    static constexpr int kWinN = 1024;

    std::shared_ptr<const SampleSet> own;
    const SampleRegion *r = nullptr;
    bool on = false;

    void start(const std::shared_ptr<const SampleSet> &set, int note, int vel = 100)
    {
        own = set;
        r = set ? set->pick(note, vel) : nullptr;
        on = r != nullptr;
        for (auto &g : grains) g.alive = false;
        scanSec = 0.0; phase = 1.0; prerolled = false;       /* phase 1: a birth exactly at note-on. The window table is kept: it depends only on the shape settings */
    }

    void release() { on = false; r = nullptr; own.reset(); for (auto &g : grains) g.alive = false; }

    int liveGrains() const { int n = 0; for (const auto &g : grains) n += g.alive ? 1 : 0; return n; }

    /* Adds n samples to l / rt. semis: pitch offset from the region's root (note - root, tuning, bend). gain: the slot's level (knob squared), as the sample slot has it.
     * What a grain latches at its birth: pitch, length, read position and the pile-up factor kGrainGain[M] (that each grain keeps its birth M and length under modulation is
     * an assumption until probe P2b). The slot level is NOT latched: it is applied live, so a level route works as on any oscillator. */
    void processAdding(const GrainParams &gp, float semis, float gain, float sr, float *l, float *rt, int n)
    {
        if (!on || !r || n <= 0) return;
        buildWindow(gp);
        const float N = grainDensityN(gp.kd);
        const double T = std::max(0.0, (double) gp.lenSeconds);
        const double lenSamp = T * sr;
        const double fileSec = (double) r->frames / (double) r->rate;
        const double step = std::pow(2.0, (double) (semis + r->tuneCents / 100.0f) / 12.0) * (double) r->rate / (double) sr;
        const int M = std::clamp((int) std::floor(N) + 1, 1, 31);
        const float amp = r->gain * (1.0f / 32768.0f) * kGrainGain[M];
        const bool live = N > 1e-6f && lenSamp >= 2.0;
        const double spacing = live ? lenSamp / (double) N : 1e30;      /* samples between births */
        const double scan = gp.scanPct * 0.01;

        /* sinceSec: the birth's time relative to the start of this block (negative for the pre-roll). The scan position is integrated (scanSec), so a modulated Scan Rate
         * changes the speed, not the place. */
        auto spawn = [&](double sinceSec, int offset, double ageSamples) {
            const double sp = scanSec + scan * sinceSec;            /* seconds the scan has moved by at the birth */
            double ps = (double) gp.start * fileSec + sp;
            if (scan < 0.0) ps = fileSec - (double) gp.start * fileSec + sp;     /* guess until P4: a negative scan runs back from the end */
            if (ps < 0.0 || ps >= fileSec) return;                  /* outside the file there is no grain (this is the measured note-on rule) */
            for (auto &g : grains) if (!g.alive) {
                g.alive = true; g.pos = ps * (double) r->rate + ageSamples * step; g.step = step; g.age = (int) ageSamples; g.invLen = (float) (1.0 / lenSamp);
                g.lenSamp = (int) lenSamp; g.amp = amp; g.off = offset;
                return;
            }
        };

        if (!prerolled) {                                       /* the grid's births from before the note that would still be sounding */
            prerolled = true;
            if (live) for (double age = spacing; age < lenSamp; age += spacing) spawn(-age / (double) sr, 0, age);
        }
        if (live) {
            /* The grid as a phase (births when it reaches 1, rate N / T per sample), not a countdown: identical for steady settings, and a Density or Length that is
             * modulated up from almost nothing gets its next grain at the new rate instead of waiting out the old, long gap. */
            double t = (1.0 - phase) * spacing;
            while (t < (double) n) {                            /* births on the grid inside this block */
                spawn(t / (double) sr, (int) t, 0.0);
                t += spacing;
            }
            phase = 1.0 - (t - (double) n) / spacing;
        }

        const int ch = r->channels, fr = r->frames;
        const int16_t *pcm = r->pcm.data();
        for (auto &g : grains) {
            if (!g.alive) continue;
            double pos = g.pos; int age = g.age;
            const float ga = g.amp * gain;                          /* the birth factor times the slot level of this block */
            int i = g.off;
            for (; i < n; i++) {
                if (age >= g.lenSamp || pos >= (double) fr) { g.alive = false; break; }
                const float fx = (float) age * g.invLen * (float) kWinN;
                const int wi = std::min((int) fx, kWinN - 1);
                const float w = win[wi] + (win[wi + 1] - win[wi]) * (fx - (float) wi);
                const int i0 = (int) pos;
                const float f1 = (float) (pos - i0);
                const int i1 = std::min(i0 + 1, fr - 1);
                float a, b;
                if (ch == 1) { const float x0 = pcm[i0], x1 = pcm[i1]; a = b = x0 + (x1 - x0) * f1; }
                else {
                    const float l0 = pcm[(size_t) i0 * 2], l1 = pcm[(size_t) i1 * 2], r0 = pcm[(size_t) i0 * 2 + 1], r1 = pcm[(size_t) i1 * 2 + 1];
                    a = l0 + (l1 - l0) * f1; b = r0 + (r1 - r0) * f1;
                }
                const float k = ga * w;
                l[i] += a * k; rt[i] += b * k;
                pos += g.step; age++;
            }
            g.pos = pos; g.age = age; g.off = 0;
        }
        scanSec += scan * (double) n / (double) sr;
    }

private:
    struct Grain { double pos = 0.0, step = 0.0; int age = 0, lenSamp = 0, off = 0; float invLen = 0.0f, amp = 0.0f; bool alive = false; };
    Grain grains[kPool];
    float win[kWinN + 1] = {};
    int   winShape = -1;
    float winParam = 0.0f, winSkew = 0.0f;
    double scanSec = 0.0;             /* how far the scan has moved since note-on, seconds of file (the integral of the scan rate) */
    double phase = 1.0;               /* the grid's phase at the start of the next block: a birth when it reaches 1 */
    bool prerolled = false;

    /* Rebuilt only when the shape settings change, and to a 0.25 step, so a modulated Window Param or Skew (one factory route) costs a rebuild per quarter step, not per block. */
    void buildWindow(const GrainParams &gp)
    {
        const float p = std::round(gp.winParam * 4.0f) * 0.25f, s = std::round(gp.winSkew * 4.0f) * 0.25f;
        if (gp.winShape == winShape && p == winParam && s == winSkew) return;
        winShape = gp.winShape; winParam = p; winSkew = s;
        for (int i = 0; i <= kWinN; i++) win[i] = grainWindow(winShape, winParam, winSkew, (float) i / (float) kWinN);
    }
};

} /* namespace tb */
