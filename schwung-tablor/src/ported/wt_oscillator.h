/* Wavetable oscillator — de-JUCE'd port of gin::WTOscillator and
 * gin::WTVoicedStereoOscillator (BSD-3, (c) Roland Rabien).
 *
 * Faithful to the original's behaviour:
 *  - three process paths (simple / phase-distorted / table-crossfade)
 *  - table switches only at phase wrap; one-cycle crossfade after a switch
 *  - bend (phase distortion), formant, asym, fold post-processing
 *  - per-channel DC blocker (10 Hz one-pole)
 *  - unison wrapper: evenly-spread detune, +/-spread pan, 1/sqrt(v) gain
 *
 * Differences (output-identical): the mip level and the formant exp() are
 * hoisted per block (note and formant are constant within a block); no SIMD.
 *
 * Differences (audible, on purpose):
 *  - the unison wrapper DC-blocks its stack once rather than per stream. See
 *    VoicedWTOscillator.
 *  - hard sync, which gin does not have. See processSync.
 *  - unison pans each stream hard to a side rather than evenly across the field.
 *    See renderStack.
 */
#pragma once

#include "wavetable.h"
#include "../dsp/warp_curves.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>

#if defined(__aarch64__)
#include <arm_neon.h>
#endif

namespace tb {

inline constexpr int kMaxUnison = 16;

/* Sync at full amount runs the slave this far above the note: three octaves,
 * so the sweep stays a formant moving through the sound instead of a whistle. */
inline constexpr float kSyncRangeSemis = 36.0f;

namespace math {
    inline float pow2(float x) { return x * x; }
    inline float pow4(float x) { return pow2(pow2(x)); }
    inline float pow8(float x) { return pow2(pow4(x)); }
    inline float lerp(float a, float b, float t) { return a + t * (b - a); }
}

class DCBlocker {
public:
    void setSampleRate(float sr) { sampleRate = sr; recalc(); }
    void setCutoff(float hz)     { cutoff = hz; recalc(); }
    inline float process(float x) { z = x * a + z * b; return z; }
    void reset() { z = 0.0f; }
private:
    void recalc()
    {
        b = std::exp(-2.0f * (float) M_PI * cutoff / sampleRate);
        a = 1.0f - b;
    }
    float sampleRate = 44100.0f, cutoff = 10.0f;
    float a = 0.0f, b = 1.0f, z = 0.0f;
};

class WTOscillator {
public:
    struct Params {
        float leftGain  = 1.0f;
        float rightGain = 1.0f;
        float position  = 0.0f;   /* 0..1 across the table's frames */
        float bend      = 0.0f;   /* -1..1 phase distortion */
        float formant   = 0.0f;   /*  0..1 spectral shift */
        float asym      = 0.0f;
        float fold      = 0.0f;
        float sync      = 0.0f;   /* 0..1 hard sync amount */
        /* Serum's Dist* oscillator warps: a static shaper out = shapeOut f(shapeGain x) of every stream's sample (calibration/dev/fit_warp_dist.py) */
        int   shape     = 0;      /* 0 off, 2 hard clip, 3 softsign, 4 cubic soft clip (1 is unused: std::tanh costs 7x the others per sample) */
        float shapeGain = 1.0f, shapeOut = 1.0f;
        int   curveMode = 0;      /* shape 5: one of Serum's static waveshapers as a measured transfer curve (warp_curves.h), curveMode its index, curveAmt Serum's 0..1 amount */
        float curveAmt  = 0.0f;
        /* Serum's PD / FM warps (the modulator is the Sub or another oscillator): pmod is added to the phase, in cycles; fmod to the phase step, in cycles per
         * sample (a frequency deviation). Both are already scaled by the warp's depth, one value per sample of the block. */
        const float *pmod = nullptr, *fmod = nullptr;
        float selfPd = 0.0f;      /* Serum's SelfPD: the phase pushed by the wave's own value, read at p + selfPd x(p + selfPd x(...)) (cycles per table peak; 0 off) */
    };

    void setSampleRate(double sr)
    {
        sampleRate = sr;
        blockerL.setSampleRate((float) sr);
        blockerR.setSampleRate((float) sr);
        blockerL.setCutoff(10.0f);
        blockerR.setCutoff(10.0f);
    }

    void setWavetable(const Wavetable *t) { wt = t; }
    void setBlockDC(bool b) { blockDC = b; }

    void noteOn(float p = -1.0f)
    {
        phase = (p >= 0.0f) ? p : (float) rand() / (float) RAND_MAX;
        tableIndex = -1;
        lastTableIndex = -1;
        steppedIdx = -1;
        syncEngaged = false;
        blockerL.reset();
        blockerR.reset();
    }

    void processAdding(float note, const Params &params, float *l, float *r, int n)
    {
        if (!wt || wt->size() == 0) return;
        if (params.shape == 5) prepareCurve(params.curveMode, params.curveAmt);

        /* The shaper (postProcess) is a function of each sample alone, so the paths below write the raw samples to a block buffer and finish() shapes them
         * and adds them in with the gains in one pass: the same arithmetic per sample, without a call and four branches per sample (output-identical).
         * Sync keeps its own: its polyBLEP works on shaped values. */
        if (params.pmod || params.fmod || params.selfPd > 0.0f || !(params.sync > 0.0f || syncEngaged)) {
            float raw[kRawBlock];
            for (int at = 0; at < n; at += kRawBlock) {
                const int k = std::min(kRawBlock, n - at);
                if (params.pmod || params.fmod || params.selfPd > 0.0f) {
                    Params q = params;
                    if (q.pmod) q.pmod += at;
                    if (q.fmod) q.fmod += at;
                    processModulated(note, q, raw, k);
                } else if (wt->stepped)
                    processStepped(note, params, raw, k);
                else if (tableIndex != lastTableIndex && lastTableIndex >= 0)
                    processCrossfade(note, params, raw, k);
                else if (params.bend == 0.0f && params.formant == 0.0f)
                    processSimple(note, params, raw, k);
                else
                    processComplex(note, params, raw, k);
                finish(params, raw, l + at, r + at, k);
            }
        } else
            processSync(note, params, l, r, n);

        if (blockDC) {
            for (int i = 0; i < n; i++) {
                l[i] -= blockerL.process(l[i]);
                r[i] -= blockerR.process(r[i]);
            }
        }
    }

private:
    inline int posToIndex(float position) const
    {
        int i = (int) ((float) wt->size() * position);
        return std::min(wt->size() - 1, i);
    }

    /* Position as a CONTINUOUS place in the table: the two frames either side
     * and how far between them.
     *
     * The oscillator used to snap to one frame and change it at a phase wrap.
     * That is fine when position is a hand-turned knob, but under modulation
     * it is not: an envelope sweeping the whole table across a 45 ms attack
     * crosses ~25 frames per cycle, so every wrap jumped a long way through
     * the table at once and left a step. Reported as the sound being "a
     * little crunchy around the edges" -- the edges being exactly where an
     * envelope moves. Blending the two neighbours removes the steps entirely
     * and is what Serum and Vital do. */
    inline void posToFrames(float position, const FrameTable *&a,
                            const FrameTable *&b, float &frac) const
    {
        const int last = wt->size() - 1;
        if (wt->stepped) {
            a = b = wt->frame(posToIndex(std::min(std::max(position, 0.0f), 1.0f)));
            frac = 0.0f;
            return;
        }
        float fp = position * (float) last;
        if (fp < 0.0f) fp = 0.0f;
        if (fp > (float) last) fp = (float) last;
        int i0 = (int) fp;
        if (i0 > last) i0 = last;
        int i1 = i0 < last ? i0 + 1 : last;
        frac = fp - (float) i0;
        a = wt->frame(i0);
        b = wt->frame(i1);
    }

    inline float freqDelta(float note) const
    {
        float freq = (float) std::min(sampleRate / 2.0,
                                      440.0 * std::pow(2.0, (note - 69.0) / 12.0));
        return freq / (float) sampleRate;
    }

    /* The measured curve at an amount: the two nearest measured amounts mixed, rebuilt only when mode or amount changes. */
    void prepareCurve(int mode, float amount)
    {
        mode = std::clamp(mode, 0, kWarpCurveModes - 1);
        amount = std::clamp(amount, 0.0f, 1.0f);
        if (mode == curveModeC && amount == curveAmtC) return;
        curveModeC = mode; curveAmtC = amount;
        const float pos = amount * (kWarpCurveAmts - 1);
        const int i0 = std::min((int) pos, kWarpCurveAmts - 2);
        const float f = pos - (float) i0;
        for (int j = 0; j < kWarpCurvePts; j++)
            curve[j] = kWarpCurveData[mode][i0][j] * (1.0f - f) + kWarpCurveData[mode][i0 + 1][j] * f;
    }

    inline void postProcess(const Params &p, float &v) const
    {
        if (p.shape == 5) {
            /* the loader normalises every table to a 0.9 peak; Serum's curves are in file units (a table peaks at 1) */
            const float u = (std::min(std::max(v * (1.0f / 0.9f), -1.0f), 1.0f) + 1.0f) * (0.5f * (kWarpCurvePts - 1));
            const int i = std::min((int) u, kWarpCurvePts - 2);
            v = 0.9f * (curve[i] + (curve[i + 1] - curve[i]) * (u - (float) i));
        } else if (p.shape >= 2) {
            const float u = p.shapeGain * v;
            float f;
            switch (p.shape) {
            case 2:  f = std::min(std::max(u, -1.0f), 1.0f); break;
            case 3:  f = u / (1.0f + std::fabs(u)); break;
            default: { const float c = std::min(std::max(u, -1.0f), 1.0f); f = 1.5f * (c - c * c * c * (1.0f / 3.0f)); break; }
            }
            v = p.shapeOut * f;
        }
        if (p.asym > 0.0f)
            v = math::lerp(v, math::pow4(v - 1.0f) * -1.0f + 1.0f, math::pow2(p.asym));
        if (p.fold > 0.0f) {
            const float fold = math::pow2(math::pow2(1.0f - p.fold)) * 1.5f;
            v = v - (std::max(v, fold) - fold) * 2.0f - (std::min(v, -fold) + fold) * 2.0f;
        }
    }

    /* postProcess over a block, then the stream added in at its gains. The branches are taken once per block; each sample sees the same operations, in the
     * same order, as postProcess gives it. */
    void finish(const Params &p, float *o, float *l, float *r, int n) const
    {
        if (p.shape == 5) {
            for (int i = 0; i < n; i++) {
                const float v = o[i];
                const float u = (std::min(std::max(v * (1.0f / 0.9f), -1.0f), 1.0f) + 1.0f) * (0.5f * (kWarpCurvePts - 1));
                const int j = std::min((int) u, kWarpCurvePts - 2);
                o[i] = 0.9f * (curve[j] + (curve[j + 1] - curve[j]) * (u - (float) j));
            }
        } else if (p.shape == 2) {
            const float sg = p.shapeGain, so = p.shapeOut;
            for (int i = 0; i < n; i++) {
                const float u = sg * o[i];
                const float f = std::min(std::max(u, -1.0f), 1.0f);
                o[i] = so * f;
            }
        } else if (p.shape == 3) {
            const float sg = p.shapeGain, so = p.shapeOut;
            for (int i = 0; i < n; i++) {
                const float u = sg * o[i];
                const float f = u / (1.0f + std::fabs(u));
                o[i] = so * f;
            }
        } else if (p.shape >= 4) {
            const float sg = p.shapeGain, so = p.shapeOut;
            for (int i = 0; i < n; i++) {
                const float u = sg * o[i];
                const float c = std::min(std::max(u, -1.0f), 1.0f);
                const float f = 1.5f * (c - c * c * c * (1.0f / 3.0f));
                o[i] = so * f;
            }
        }
        if (p.asym > 0.0f) {
            const float t = math::pow2(p.asym);
            for (int i = 0; i < n; i++)
                o[i] = math::lerp(o[i], math::pow4(o[i] - 1.0f) * -1.0f + 1.0f, t);
        }
        if (p.fold > 0.0f) {
            const float fold = math::pow2(math::pow2(1.0f - p.fold)) * 1.5f;
            for (int i = 0; i < n; i++) {
                const float v = o[i];
                o[i] = v - (std::max(v, fold) - fold) * 2.0f - (std::min(v, -fold) + fold) * 2.0f;
            }
        }
        const float gl = p.leftGain, gr = p.rightGain;
        for (int i = 0; i < n; i++) {
            l[i] += o[i] * gl;
            r[i] += o[i] * gr;
        }
    }

    /* gin's bendDistortion + formantDistortion, formant exp hoisted. */
    inline float distort(float ph, float bend, float formantMul) const
    {
        if (bend != 0.0f) {
            const float addDist = std::clamp(bend, 0.0f, 1.0f);
            const float subDist = std::clamp(bend, -1.0f, 0.0f);
            const float sub = math::pow8(ph);
            const float add = math::pow8(1.0f - ph);
            const float addMix = math::lerp(ph, 1.0f - add, addDist);
            const float subMix = math::lerp(ph, sub, -subDist);
            ph = std::min(kAlmostOne, addMix + subMix - ph);
        }
        if (formantMul != 1.0f)
            ph = std::min(kAlmostOne, ph * formantMul);
        return ph;
    }

#if defined(__aarch64__)
    /* Four table reads at once: ph (four phases below 1) in a table of vsz samples; each read's two samples come in one 64-bit load. */
    static inline float32x4_t look4(const float32x4_t ph, const float32x4_t vsz, const float *d)
    {
        const float32x4_t fidx = vmulq_f32(ph, vsz);
        const int32x4_t idx = vcvtq_s32_f32(fidx);
        const float32x4_t f = vsubq_f32(fidx, vcvtq_f32_s32(idx));
        int32_t ix[4]; vst1q_s32(ix, idx);
        const float32x4_t p01 = vcombine_f32(vld1_f32(d + ix[0]), vld1_f32(d + ix[1])), p23 = vcombine_f32(vld1_f32(d + ix[2]), vld1_f32(d + ix[3]));
        const float32x4_t a = vuzp1q_f32(p01, p23), b = vuzp2q_f32(p01, p23);
        return vfmaq_f32(a, f, vsubq_f32(b, a));
    }

    /* distort() on four phases. */
    static inline float32x4_t distort4(float32x4_t ph, float bend, float formantMul)
    {
        const float32x4_t vone = vdupq_n_f32(1.0f), vmax = vdupq_n_f32(kAlmostOne);
        if (bend != 0.0f) {
            const float addDist = std::clamp(bend, 0.0f, 1.0f), subDist = std::clamp(bend, -1.0f, 0.0f);
            const float32x4_t s2 = vmulq_f32(ph, ph), s4 = vmulq_f32(s2, s2), sub = vmulq_f32(s4, s4);
            const float32x4_t om = vsubq_f32(vone, ph), a2 = vmulq_f32(om, om), a4 = vmulq_f32(a2, a2), add = vmulq_f32(a4, a4);
            const float32x4_t addMix = vfmaq_f32(ph, vdupq_n_f32(addDist), vsubq_f32(vsubq_f32(vone, add), ph));
            const float32x4_t subMix = vfmaq_f32(ph, vdupq_n_f32(-subDist), vsubq_f32(sub, ph));
            ph = vminq_f32(vmax, vsubq_f32(vaddq_f32(addMix, subMix), ph));
        }
        if (formantMul != 1.0f) ph = vminq_f32(vmax, vmulq_f32(ph, vdupq_n_f32(formantMul)));
        return ph;
    }
#endif

    void processSimple(float note, const Params &p, float *o, int n)
    {
        if (tableIndex == -1 || tableIndex >= wt->size())
            tableIndex = posToIndex(p.position);

        const float delta = freqDelta(note);
        const FrameTable *fa = nullptr, *fb = nullptr;
        float frac = 0.0f;
        posToFrames(p.position, fa, fb, frac);
        const MipLevel &la = fa->levelRef(note), &lb = fb->levelRef(note);

        while (n > 0) {
            int todo = std::min(n, (int) ((1.0f - phase) / delta) + 1);
            n -= todo;
#if defined(__aarch64__)
            /* Four samples at a time: the phases are phase + k delta, the two samples of each lookup come in one 64-bit load, and the interpolation is a vector. The
             * rounding differs from adding delta one sample at a time by about 1e-7 of a cycle. */
            if (todo >= 4) {
                const float32x4_t vfa = vdupq_n_f32((float) la.size), vfb = vdupq_n_f32((float) lb.size), vdel4 = vdupq_n_f32(4.0f * delta), vmax = vdupq_n_f32(kAlmostOne), vfrac = vdupq_n_f32(frac);
                const float step4[4] = { 0.0f, delta, 2.0f * delta, 3.0f * delta };
                float32x4_t vph = vaddq_f32(vdupq_n_f32(phase), vld1q_f32(step4));
                const float *da = la.data.data(), *db = lb.data.data();
                for (; todo >= 4; todo -= 4) {
                    const float32x4_t ph = vminq_f32(vph, vmax);
                    float32x4_t sv = look4(ph, vfa, da);
                    if (frac > 0.0f) sv = vfmaq_f32(sv, vfrac, vsubq_f32(look4(ph, vfb, db), sv));
                    vst1q_f32(o, sv);
                    o += 4; vph = vaddq_f32(vph, vdel4);
                    phase += 4.0f * delta;
                }
            }
#endif
            for (; todo > 0; todo--) {
                const float ph = std::min(kAlmostOne, phase);
                float s = FrameTable::readLinear(la, ph);
                if (frac > 0.0f)
                    s += (FrameTable::readLinear(lb, ph) - s) * frac;
                *o++ = s;
                phase += delta;
            }
            while (phase >= 1.0f) phase -= 1.0f;
        }
        lastTableIndex = tableIndex = posToIndex(p.position);
    }

    /* The Sub as a modulator (PD: the phase is pushed by it; FM: the phase step is). The phase changes by a different amount every sample, so
     * this walks one sample at a time and wraps the phase either way (an FM deviation can pull it back). */
    void processModulated(float note, const Params &p, float *o, int n)
    {
        const float delta = freqDelta(note);
        const float formantMul = (p.formant != 0.0f)
            ? std::exp(p.formant * 1.60943791243f) : 1.0f;
        const FrameTable *fa = nullptr, *fb = nullptr;
        float frac = 0.0f;
        posToFrames(p.position, fa, fb, frac);
        const MipLevel &la = fa->levelRef(note), &lb = fb->levelRef(note);
        if (!(p.selfPd > 0.0f)) {                      /* the common case, as a loop specialised on what is on (output-identical: an absent term is + 0) */
            const bool pm = p.pmod != nullptr, fm = p.fmod != nullptr, fr = frac > 0.0f, ds = p.bend != 0.0f || formantMul != 1.0f;
            const int sel = (pm ? 8 : 0) | (fm ? 4 : 0) | (fr ? 2 : 0) | (ds ? 1 : 0);
            switch (sel) {
#define TB_MODCASE(k) case k: modLoop<(k & 8) != 0, (k & 4) != 0, (k & 2) != 0, (k & 1) != 0>(p, la, lb, frac, delta, formantMul, o, n); break;
            TB_MODCASE(0) TB_MODCASE(1) TB_MODCASE(2) TB_MODCASE(3) TB_MODCASE(4) TB_MODCASE(5) TB_MODCASE(6) TB_MODCASE(7)
            TB_MODCASE(8) TB_MODCASE(9) TB_MODCASE(10) TB_MODCASE(11) TB_MODCASE(12) TB_MODCASE(13) TB_MODCASE(14) TB_MODCASE(15)
#undef TB_MODCASE
            }
            lastTableIndex = tableIndex = posToIndex(p.position);
            return;
        }
        for (int i = 0; i < n; i++) {
            float at = phase + (p.pmod ? p.pmod[i] : 0.0f);
            at -= std::floor(at);
            const float ph = distort(std::min(kAlmostOne, at), p.bend, formantMul);
            float s = FrameTable::readLinear(la, ph);
            if (frac > 0.0f)
                s += (FrameTable::readLinear(lb, ph) - s) * frac;
            if (p.selfPd > 0.0f) {                         /* the implicit y = x(p + z y), three fixed-point steps (the table is at a 0.9 peak: y is taken in units of the peak) */
                const float base = at;
                for (int it = 0; it < 3; it++) {
                    float at2 = base + p.selfPd * (s * (1.0f / 0.9f));
                    at2 -= std::floor(at2);
                    const float ph2 = distort(std::min(kAlmostOne, at2), p.bend, formantMul);
                    s = FrameTable::readLinear(la, ph2);
                    if (frac > 0.0f)
                        s += (FrameTable::readLinear(lb, ph2) - s) * frac;
                }
            }
            o[i] = s;
            phase += delta + (p.fmod ? p.fmod[i] : 0.0f);
            phase -= std::floor(phase);
        }
        lastTableIndex = tableIndex = posToIndex(p.position);
    }

    template <bool PM, bool FM, bool FR, bool DS>
    void modLoop(const Params &p, const MipLevel &la, const MipLevel &lb, float frac, float delta, float formantMul, float *o, int n)
    {
        const float *pmod = p.pmod, *fmod = p.fmod;
        const float bend = p.bend;
        float ph0 = phase;
        int i = 0;
#if defined(__aarch64__)
        /* Four samples at a time. The phases are an exclusive running sum of the steps (delta + the FM input), added to the block's phase and wrapped; the reads are as
         * in processSimple. Rounding differs from stepping one sample at a time by about 1e-7 of a cycle. */
        {
            const float32x4_t vzero = vdupq_n_f32(0.0f), vdelta = vdupq_n_f32(delta), vmax = vdupq_n_f32(kAlmostOne), vfrac = vdupq_n_f32(frac);
            const float32x4_t vfa = vdupq_n_f32((float) la.size), vfb = vdupq_n_f32((float) lb.size);
            const float *da = la.data.data(), *db = lb.data.data();
            for (; i + 4 <= n; i += 4) {
                const float32x4_t inc = FM ? vaddq_f32(vdelta, vld1q_f32(fmod + i)) : vdelta;
                float32x4_t x = vextq_f32(vzero, inc, 3);                                  /* [0, i0, i1, i2] */
                x = vaddq_f32(x, vextq_f32(vzero, x, 3));                                  /* [0, i0, i0+i1, i1+i2] */
                x = vaddq_f32(x, vextq_f32(vzero, x, 2));                                  /* [0, i0, i0+i1, i0+i1+i2] */
                float32x4_t at = vaddq_f32(vdupq_n_f32(ph0), x);
                if (PM) at = vaddq_f32(at, vld1q_f32(pmod + i));
                at = vsubq_f32(at, vrndmq_f32(at));
                const float32x4_t ph = DS ? distort4(vminq_f32(at, vmax), bend, formantMul) : vminq_f32(at, vmax);
                float32x4_t sv = look4(ph, vfa, da);
                if (FR) sv = vfmaq_f32(sv, vfrac, vsubq_f32(look4(ph, vfb, db), sv));
                vst1q_f32(o + i, sv);
                ph0 += vaddvq_f32(inc);
                ph0 -= std::floor(ph0);
            }
        }
#endif
        for (; i < n; i++) {
            float at = PM ? ph0 + pmod[i] : ph0;
            at -= std::floor(at);                          /* kept without PM too: a phase handed in at exactly 1 wraps to 0, as it does above */
            const float ph = DS ? distort(std::min(kAlmostOne, at), bend, formantMul) : std::min(kAlmostOne, at);
            float s = FrameTable::readLinear(la, ph);
            if (FR)
                s += (FrameTable::readLinear(lb, ph) - s) * frac;
            o[i] = s;
            ph0 += FM ? delta + fmod[i] : delta;
            ph0 -= std::floor(ph0);
        }
        phase = ph0;
    }

    void processComplex(float note, const Params &p, float *o, int n)
    {
        if (tableIndex == -1 || tableIndex >= wt->size())
            tableIndex = posToIndex(p.position);

        const float delta = freqDelta(note);
        const float formantMul = (p.formant != 0.0f)
            ? std::exp(p.formant * 1.60943791243f) : 1.0f;
        const FrameTable *fa = nullptr, *fb = nullptr;
        float frac = 0.0f;
        posToFrames(p.position, fa, fb, frac);
        const MipLevel &la = fa->levelRef(note), &lb = fb->levelRef(note);

        while (n > 0) {
            int todo = std::min(n, (int) ((1.0f - phase) / delta) + 1);
            n -= todo;
#if defined(__aarch64__)
            if (todo >= 4) {                                                     /* four samples at a time, as in processSimple */
                const float32x4_t vfa = vdupq_n_f32((float) la.size), vfb = vdupq_n_f32((float) lb.size), vdel4 = vdupq_n_f32(4.0f * delta), vmax = vdupq_n_f32(kAlmostOne), vfrac = vdupq_n_f32(frac);
                const float step4[4] = { 0.0f, delta, 2.0f * delta, 3.0f * delta };
                float32x4_t vph = vaddq_f32(vdupq_n_f32(phase), vld1q_f32(step4));
                const float *da = la.data.data(), *db = lb.data.data();
                for (; todo >= 4; todo -= 4) {
                    const float32x4_t ph = distort4(vminq_f32(vph, vmax), p.bend, formantMul);
                    float32x4_t sv = look4(ph, vfa, da);
                    if (frac > 0.0f) sv = vfmaq_f32(sv, vfrac, vsubq_f32(look4(ph, vfb, db), sv));
                    vst1q_f32(o, sv);
                    o += 4; vph = vaddq_f32(vph, vdel4);
                    phase += 4.0f * delta;
                }
            }
#endif
            for (; todo > 0; todo--) {
                float ph = distort(std::min(kAlmostOne, phase), p.bend, formantMul);
                float s = FrameTable::readLinear(la, ph);
                if (frac > 0.0f)
                    s += (FrameTable::readLinear(lb, ph) - s) * frac;
                *o++ = s;
                phase += delta;
            }
            while (phase >= 1.0f) phase -= 1.0f;
        }
        lastTableIndex = tableIndex = posToIndex(p.position);
    }

    /* Hard sync: a hidden master at the note's pitch restarts this oscillator's
     * phase, which runs above it. The note you hear stays the master's; the
     * amount moves a formant through it.
     *
     * The restart is a step in the waveform, and an unfiltered step aliases
     * badly at exactly the high notes sync is reached for. Each one is smoothed
     * with a two-sample polyBLEP sized to the actual jump, which reaches back
     * one sample -- so this path runs one sample late. It stays engaged until
     * the next note: at amount 0 the slave rides the master with no step at
     * all, so an envelope falling to zero and rising again cannot click. */
    void processSync(float note, const Params &p, float *l, float *r, int n)
    {
        const float delta  = freqDelta(note);
        const float sNote  = note + p.sync * kSyncRangeSemis;
        const float sDelta = freqDelta(sNote);
        const float formantMul = (p.formant != 0.0f)
            ? std::exp(p.formant * 1.60943791243f) : 1.0f;
        const FrameTable *fa = nullptr, *fb = nullptr;
        float frac = 0.0f;
        posToFrames(p.position, fa, fb, frac);
        /* the slave's pitch picks the mip level, or it aliases on its own */
        const MipLevel &la = fa->levelRef(sNote), &lb = fb->levelRef(sNote);

        const auto read = [&](float at) {
            const float ph = distort(std::min(kAlmostOne, at), p.bend, formantMul);
            float s = FrameTable::readLinear(la, ph);
            if (frac > 0.0f)
                s += (FrameTable::readLinear(lb, ph) - s) * frac;
            postProcess(p, s);
            return s;
        };

        if (!syncEngaged) {
            syncEngaged = true;
            masterPhase = phase;
            syncHold = read(phase);
        }

        for (int i = 0; i < n; i++) {
            masterPhase += delta;
            phase += sDelta;

            float s;
            if (masterPhase >= 1.0f) {
                masterPhase -= 1.0f;
                const float t = masterPhase / delta;    /* samples since the restart */
                float was = phase - t * sDelta;
                if (was >= 1.0f) was -= 1.0f;
                const float step = read(0.0f) - read(was);
                const float u = 1.0f - t;
                syncHold += step * t * t * 0.5f;
                phase = t * sDelta;
                s = read(phase) - step * u * u * 0.5f;
            } else {
                if (phase >= 1.0f) phase -= 1.0f;
                s = read(phase);
            }

            *l++ += syncHold * p.leftGain;
            *r++ += syncHold * p.rightGain;
            syncHold = s;
        }
    }

    /* A no-interpolation table (Serum's PWM tables, say) plays one frame at a time. The frame is the one the position names, but it is taken up only where a cycle
     * begins: picked per block it changed mid-cycle, and a position LFO sweeping across frame boundaries put a step in the waveform at every one (a tick on the PWM
     * pads, heard with one voice and no unison, gone once the filter is below about 700 Hz). */
    void processStepped(float note, const Params &p, float *o, int n)
    {
        if (steppedIdx < 0 || steppedIdx >= wt->size())
            steppedIdx = posToIndex(p.position);

        const float delta = freqDelta(note);
        const float formantMul = (p.formant != 0.0f) ? std::exp(p.formant * 1.60943791243f) : 1.0f;
        const FrameTable *ft = wt->frame(steppedIdx);

        while (n > 0) {
            int todo = std::min(n, (int) ((1.0f - phase) / delta) + 1);
            n -= todo;
            for (; todo > 0; todo--) {
                const float ph = distort(std::min(kAlmostOne, phase), p.bend, formantMul);
                *o++ = ft->processLinear(note, ph);
                phase += delta;
            }
            while (phase >= 1.0f) {
                phase -= 1.0f;
                steppedIdx = posToIndex(p.position);
                ft = wt->frame(steppedIdx);
            }
        }
        lastTableIndex = tableIndex = steppedIdx;
    }

    void processCrossfade(float note, const Params &p, float *o, int n)
    {
        if (tableIndex == -1 || tableIndex >= wt->size())
            tableIndex = posToIndex(p.position);
        if (lastTableIndex == -1 || lastTableIndex >= wt->size())
            lastTableIndex = posToIndex(p.position);

        const float delta = freqDelta(note);
        const float formantMul = (p.formant != 0.0f)
            ? std::exp(p.formant * 1.60943791243f) : 1.0f;
        const FrameTable *ft1 = wt->frame(tableIndex);
        const FrameTable *ft2 = wt->frame(lastTableIndex);

        while (n > 0) {
            int todo = std::min(n, (int) ((1.0f - phase) / delta) + 1);
            n -= todo;
            for (; todo > 0; todo--) {
                float ph = distort(std::min(kAlmostOne, phase), p.bend, formantMul);
                float s1 = ft1->processLinear(note, ph);
                float s2 = ft2->processLinear(note, ph);
                float s = s1 * phase + s2 * (1.0f - phase);  /* old->new over one cycle */
                *o++ = s;
                phase += delta;
            }
            while (phase >= 1.0f) {
                phase -= 1.0f;
                lastTableIndex = tableIndex;
                tableIndex = posToIndex(p.position);
                ft1 = ft2 = wt->frame(tableIndex);
            }
        }
    }

    static constexpr float kAlmostOne = 0.99999994f; /* < 1.0f in float */
    static constexpr int kRawBlock = 128;

    const Wavetable *wt = nullptr;
    double sampleRate = 44100.0;
    float phase = 0.0f;
    int tableIndex = -1, lastTableIndex = -1, steppedIdx = -1;
    bool blockDC = true;
    float curve[kWarpCurvePts] = {};
    int   curveModeC = -1;
    float curveAmtC = -1.0f;
    DCBlocker blockerL, blockerR;
    float masterPhase = 0.0f, syncHold = 0.0f;
    bool syncEngaged = false;
};

/* ------------------------------------------------------------------ */
/* Unison wrapper — gin::VoicedStereoOscillator, WT flavour            */
/* ------------------------------------------------------------------ */

struct VoicedWTParams {
    int   voices = 1;
    float pan = 0.0f;       /* -1..1 */
    float spread = 0.0f;    /*  0..1 */
    float detune = 0.0f;    /* semitones across the stack */
    float gain = 1.0f;
    /* WT params forwarded to every voice */
    float position = 0.0f, bend = 0.0f, formant = 0.0f, asym = 0.0f, fold = 0.0f;
    float sync = 0.0f;
    int   shape = 0;
    float shapeGain = 1.0f, shapeOut = 1.0f;
    int   curveMode = 0;
    float curveAmt = 0.0f;
    /* One more of Serum's warps on the oscillator: 0 none, 1 Filter LP, 2 Filter HP, 3 FM from Sub, 4 PD from Sub, 5 RM from Sub; xamount is
     * Serum's 0..1 amount. The laws are measured (calibration/dev/probe_warp_fm.py, probe_filterwarp.py). */
    int   xwarp = 0;
    float xamount = 0.0f;
    int   xsrc = 0;                       /* the modulator of an FM / PD / RM / AM warp: 0 the Sub, 1..3 oscillator A..C */
    int   xwarp2 = 0;                     /* the oscillator's second warp (Serum has two), applied after the first */
    float xamount2 = 0.0f;
    int   xsrc2 = 0;
};

/* What each warp does at an amount, from Serum:
 *  - Filter LP / HP: a first-order filter whose corner is an ABSOLUTE frequency (it does not follow the note): log2(Hz) by amount below. LP is open at 1, HP is shut.
 *  - PD from Sub: the phase pushed by the Sub, index 33.6 a^4 rad (the same at every pitch).
 *  - FM from Sub: the frequency pushed by the Sub, a deviation of 58 200 a^4 Hz, so the index is that over the Sub's frequency.
 *  - RM from Sub: (1 - a) x + a 1.314 x S, the carrier falling to nothing at 1.
 * The Sub, or the oscillator, is taken at unit level whatever its own level. */
inline float filterWarpCornerHz(float a)
{
    static const float A[12] = { 0.0f, 0.05f, 0.1f, 0.2f, 0.3f, 0.4f, 0.5f, 0.6f, 0.7f, 0.8f, 0.9f, 1.0f };
    static const float L[12] = { 5.43f, 5.91f, 6.41f, 7.39f, 8.37f, 9.37f, 10.35f, 11.35f, 12.32f, 13.37f, 14.57f, 17.56f };
    a = std::min(std::max(a, 0.0f), 1.0f);
    int j = 1;
    while (j < 11 && A[j] < a) j++;
    const float t = (a - A[j - 1]) / (A[j] - A[j - 1]);
    return std::exp2(L[j - 1] + t * (L[j] - L[j - 1]));
}
inline constexpr float kXwarpPdCycles = 33.6f / 6.28318530718f;   /* PD index in cycles at amount 1 (x a^4) */
inline constexpr float kXwarpFmHz     = 58200.0f;                  /* FM deviation at amount 1, Hz (x a^4) */
inline constexpr float kXwarpRmGain   = 1.314f;
inline constexpr float kXwarpOscScale = 1.52f;
/* The Noise as the modulator (calibration/dev/probe_noise_warp_law.py, averaged over renders, calibration/dev/validate_noise_warps.py).
 *  - FM from the Noise is a real FM by white noise: the carrier becomes a Lorentzian line (the power stays) whose half width H grows exponentially with the amount (about x 3.3 per 0.1), read from the
 *    share of the power left in the carrier's +-1.4 Hz (H = 1.4 / tan(pi share / 2)). A white frequency noise of standard deviation d cycles per sample has H = pi d^2 fs, so d = sqrt(H / (pi fs)).
 *    Nothing audible below 0.45.
 *  - PD from the Noise is a phase modulation by white noise (carrier keeps exp(-s^2), s the rms phase in radians), on from about 0.55; the tables are s^2 against the amount.
 *  - RM from the Noise is (1 - a) x + 0.409 a^2 x n, n white at unit rms (the noise part's power 0.168 a^4 of the note's). */
inline float noiseFmSdCycles(float a, float sampleRate)
{
    static const float A[10] = { 0.45f, 0.5f, 0.55f, 0.6f, 0.65f, 0.7f, 0.75f, 0.8f, 0.9f, 1.0f };
    static const float H[10] = { 0.054f, 0.42f, 2.05f, 11.7f, 21.0f, 52.0f, 150.0f, 230.0f, 4000.0f, 30000.0f };
    if (a < A[0]) return 0.0f;
    int j = 1;
    while (j < 9 && A[j] < a) j++;
    const float t = std::min((a - A[j - 1]) / (A[j] - A[j - 1]), 1.0f);
    const float h = std::exp(std::log(H[j - 1]) + t * (std::log(H[j]) - std::log(H[j - 1])));
    return std::sqrt(h / (3.14159265f * sampleRate));
}
inline float noisePdSigmaCycles(float a)
{
    static const float PA[10] = { 0.55f, 0.6f, 0.65f, 0.7f, 0.75f, 0.8f, 0.85f, 0.9f, 0.95f, 1.0f };
    static const float PV[10] = { 0.0f, 0.06f, 0.22f, 0.75f, 2.9f, 3.6f, 4.6f, 5.0f, 6.0f, 9.8f };
    if (a <= PA[0]) return 0.0f;
    int j = 1;
    while (j < 9 && PA[j] < a) j++;
    const float t = std::min((a - PA[j - 1]) / (PA[j] - PA[j - 1]), 1.0f);
    return std::sqrt(PV[j - 1] + t * (PV[j] - PV[j - 1])) / 6.28318530718f;
}
inline constexpr float kXwarpNoiseRmGain = 0.409f;
/* SelfPD: the phase is pushed by the wave's own value, y = x(p + z y), z = 0.62 a^2 cycles (measured: the implicit form fits a sine to 0.003-0.01 rms up to amount 0.4, where z is 0.095;
 * z / a^2 reads 0.63, 0.61, 0.59, 0.56 at 0.2 .. 0.5). */
inline float selfPdCycles(float a) { return a <= 0.5f ? 0.62f * a * a : 0.155f + (a - 0.5f) * 0.4f; }
/* An oscillator as the modulator is 1.52 times as strong as the Sub at the same amount (calibration/dev/probe_warp_osc.py: FM deviation 90 000 a^4 Hz against the Sub's
 * 58 200, PD index 51 a^4 against 33.6, RM / AM sidebands a against 0.657 a). Mode 6, AM, is x (1 + a 1.314 k S). */

/* The stack is DC-blocked ONCE, on its own buffer, not by each stream.
 *
 * A stream blocking the output it adds to also filters everything already in
 * that buffer: the rest of its stack and any oscillator rendered before it. N
 * streams were N cascaded 10 Hz high-passes over the whole bus -- measured at
 * -7.8 dB at 41 Hz for a 16-voice stack, with a further -4.1 dB to that stack
 * from a SILENT second oscillator after it. Upstream's cap of 4 mostly hid it.
 * Unison bass is the first thing this cap was raised for, so it had to go. It
 * also costs two filters per stack rather than two per stream. */
class VoicedWTOscillator {
public:
    explicit VoicedWTOscillator(int maxVoices = kMaxUnison) : oscs((size_t) maxVoices)
    {
        for (auto &o : oscs) o.setBlockDC(false);
    }

    void setSampleRate(double sr)
    {
        for (auto &o : oscs) o.setSampleRate(sr);
        blockerL.setSampleRate((float) sr);
        blockerR.setSampleRate((float) sr);
        blockerL.setCutoff(10.0f);
        blockerR.setCutoff(10.0f);
        sampleRate = sr;
    }
    void setWavetable(const Wavetable *t) { for (auto &o : oscs) o.setWavetable(t); }

    void noteOn(const float *phases, int n)
    {
        for (int i = 0; i < (int) oscs.size(); i++)
            oscs[(size_t) i].noteOn(i < n ? phases[i] : -1.0f);
        blockerL.reset();
        blockerR.reset();
        fwL[0] = fwL[1] = fwR[0] = fwR[1] = 0.0f;
    }
    void noteOn(float phase = -1.0f)
    {
        for (auto &o : oscs) o.noteOn(phase);
        blockerL.reset();
        blockerR.reset();
        fwL[0] = fwL[1] = fwR[0] = fwR[1] = 0.0f;
    }

    /* `src[0..3]` are the modulators' waveforms at unit level for the block (the Sub, then oscillators A..C), null where not rendered; the FM / PD / RM / AM warps read them. */
    void processAdding(float note, const VoicedWTParams &vp, float *l, float *r, int n, const float *const *src = nullptr)
    {
        struct Slot { int kind = 0; float a = 0.0f, gain = 1.0f, g = 0.0f; const float *sig = nullptr; bool noise = false; };
        Slot s[2];
        float selfPd = 0.0f;
        for (int k = 0; k < 2; k++) {
            const int w = k ? vp.xwarp2 : vp.xwarp;
            const float a = std::min(std::max(k ? vp.xamount2 : vp.xamount, 0.0f), 1.0f);
            if (w < 1 || w > 7 || a <= 0.0f) continue;
            if (w == 7) {                                      /* SelfPD: 0.62 a^2 cycles (calibration/probe_selfpd.py: the implicit form fits a sine to 0.01 up to amount 0.4) */
                selfPd = selfPdCycles(a);
                continue;
            }
            if (w >= 3) {
                const int si = std::min(std::max(k ? vp.xsrc2 : vp.xsrc, 0), 4);
                if (!src || !src[si]) continue;
                s[k].sig = src[si]; s[k].gain = si == 0 ? 1.0f : kXwarpOscScale;
                if (si == 4) {
                    if (w == 6) continue;                      /* AM from the Noise is not measured */
                    s[k].noise = true; s[k].gain = 1.0f;
                }
            } else {
                /* Filter warps: a one-pole (trapezoidal) at the corner, once per stack, since the filter is linear and the stack is the sum of its streams */
                const float kk = std::tan(std::min((float) M_PI * filterWarpCornerHz(a) / (float) sampleRate, 1.5533f));
                s[k].g = kk / (1.0f + kk);
            }
            s[k].kind = w; s[k].a = a;
        }
        while (n > 0) {
            const int todo = std::min(n, kStackBlock);
            float sl[kStackBlock], sr[kStackBlock];
            std::memset(sl, 0, sizeof(float) * (size_t) todo); std::memset(sr, 0, sizeof(float) * (size_t) todo);
            float pm[kStackBlock], fm[kStackBlock];
            bool hasPm = false, hasFm = false;
            for (int k = 0; k < 2; k++) {
                if (s[k].kind != 3 && s[k].kind != 4) continue;
                const float a4 = (s[k].a * s[k].a) * (s[k].a * s[k].a);
                const bool fmk = s[k].kind == 3;
                float *dst = fmk ? fm : pm;
                bool &has = fmk ? hasFm : hasPm;
                if (!has) { for (int i = 0; i < todo; i++) dst[i] = 0.0f; has = true; }
                const float depth = s[k].noise ? (fmk ? noiseFmSdCycles(s[k].a, (float) sampleRate) : noisePdSigmaCycles(s[k].a))
                                                : (fmk ? kXwarpFmHz / (float) sampleRate : kXwarpPdCycles) * a4 * s[k].gain;
                for (int i = 0; i < todo; i++) dst[i] += depth * s[k].sig[i];
            }
            renderStack(note, vp, sl, sr, todo, hasPm ? pm : nullptr, hasFm ? fm : nullptr, selfPd);
            for (int k = 0; k < 2; k++) {
                const int kind = s[k].kind;
                if (kind == 5 || kind == 6) {
                    const float dry = kind == 5 ? 1.0f - s[k].a : 1.0f,
                                wet = s[k].noise ? kXwarpNoiseRmGain * s[k].a * s[k].a : s[k].a * kXwarpRmGain * s[k].gain;
                    for (int i = 0; i < todo; i++) {
                        const float g = dry + wet * s[k].sig[i];
                        sl[i] *= g; sr[i] *= g;
                    }
                } else if (kind == 1 || kind == 2) {
                    const bool hp = kind == 2;
                    const float fg = s[k].g;
                    for (int i = 0; i < todo; i++) {
                        float v = (sl[i] - fwL[k]) * fg, y = v + fwL[k]; fwL[k] = y + v; sl[i] = hp ? sl[i] - y : y;
                        v = (sr[i] - fwR[k]) * fg; y = v + fwR[k]; fwR[k] = y + v; sr[i] = hp ? sr[i] - y : y;
                    }
                }
            }
            for (int i = 0; i < todo; i++) {
                l[i] += sl[i] - blockerL.process(sl[i]);
                r[i] += sr[i] - blockerR.process(sr[i]);
            }
            l += todo; r += todo; n -= todo;
            for (int k = 0; k < 2; k++) if (s[k].sig) s[k].sig += todo;
        }
    }

private:
    static constexpr int kStackBlock = 128;

    void renderStack(float note, const VoicedWTParams &vp, float *l, float *r, int n, const float *pmod, const float *fmod, float selfPd = 0.0f)
    {
        WTOscillator::Params p;
        p.position = vp.position; p.bend = vp.bend; p.formant = vp.formant;
        p.asym = vp.asym; p.fold = vp.fold; p.sync = vp.sync;
        p.shape = vp.shape; p.shapeGain = vp.shapeGain; p.shapeOut = vp.shapeOut; p.curveMode = vp.curveMode; p.curveAmt = vp.curveAmt;
        p.pmod = pmod; p.fmod = fmod; p.selfPd = selfPd;

        const int voices = std::clamp(vp.voices, 1, (int) oscs.size());
        if (voices == 1) {
            p.leftGain  = vp.gain * (1.0f - vp.pan);
            p.rightGain = vp.gain * (1.0f + vp.pan);
            oscs[0].processAdding(note, p, l, r, n);
            return;
        }

        const float baseNote  = note - vp.detune / 2.0f;
        const float noteDelta = vp.detune / (float) (voices - 1);
        const float comp      = 1.0f / std::sqrt((float) voices);

        /* Streams alternate between the two sides, with the middle one of an odd stack
         * in the centre. Evenly spaced pans could not get wider than side/mid -4.8 dB
         * however far they spread, since most streams sit nearer the centre than the
         * edge. Serum 2 measures -0.1 dB at full width, which takes every stream out at
         * the edge. Stacks of 2 and 3 land where they always did. */
        for (int i = 0; i < voices; i++) {
            const bool middle = (voices & 1) && i == voices / 2;
            const float side = middle ? 0.0f : (i & 1) ? 1.0f : -1.0f;
            float pan = std::clamp(vp.pan + vp.spread * side, -1.0f, 1.0f);
            p.leftGain  = vp.gain * (1.0f - pan) * comp;
            p.rightGain = vp.gain * (1.0f + pan) * comp;
            oscs[(size_t) i].processAdding(baseNote + noteDelta * (float) i, p, l, r, n);
        }
    }

    std::vector<WTOscillator> oscs;
    DCBlocker blockerL, blockerR;
    double sampleRate = 44100.0;
    float fwL[2] = {0.0f, 0.0f}, fwR[2] = {0.0f, 0.0f};        /* the filter warps' state, one per warp slot */
};

} // namespace tb
