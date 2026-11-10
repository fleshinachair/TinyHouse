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

#include <algorithm>
#include <cmath>
#include <cstdlib>

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
        /* Serum's Dist* oscillator warps: a static shaper out = shapeOut f(shapeGain x) of every stream's sample (calibration/fit_warp_dist.py) */
        int   shape     = 0;      /* 0 off, 2 hard clip, 3 softsign, 4 cubic soft clip (1 is unused: std::tanh costs 7x the others per sample) */
        float shapeGain = 1.0f, shapeOut = 1.0f;
        /* Serum's PD / FM warps from the Sub: `mod` is the Sub's waveform at unit level, one value per sample of the block. modKind 1 adds
         * modDepth x mod to the phase (cycles); 2 adds it to the phase step (cycles per sample, a frequency deviation). */
        int   modKind = 0;
        float modDepth = 0.0f;
        const float *mod = nullptr;
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
        syncEngaged = false;
        blockerL.reset();
        blockerR.reset();
    }

    void processAdding(float note, const Params &params, float *l, float *r, int n)
    {
        if (!wt || wt->size() == 0) return;

        if (params.modKind != 0 && params.mod)
            processModulated(note, params, l, r, n);
        else if (params.sync > 0.0f || syncEngaged)
            processSync(note, params, l, r, n);
        else if (tableIndex != lastTableIndex && lastTableIndex >= 0)
            processCrossfade(note, params, l, r, n);
        else if (params.bend == 0.0f && params.formant == 0.0f)
            processSimple(note, params, l, r, n);
        else
            processComplex(note, params, l, r, n);

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

    inline void postProcess(const Params &p, float &v) const
    {
        if (p.shape >= 2) {
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

    void processSimple(float note, const Params &p, float *l, float *r, int n)
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
            for (; todo > 0; todo--) {
                const float ph = std::min(kAlmostOne, phase);
                float s = FrameTable::readLinear(la, ph);
                if (frac > 0.0f)
                    s += (FrameTable::readLinear(lb, ph) - s) * frac;
                postProcess(p, s);
                *l++ += s * p.leftGain;
                *r++ += s * p.rightGain;
                phase += delta;
            }
            while (phase >= 1.0f) phase -= 1.0f;
        }
        lastTableIndex = tableIndex = posToIndex(p.position);
    }

    /* The Sub as a modulator (PD: the phase is pushed by it; FM: the phase step is). The phase changes by a different amount every sample, so
     * this walks one sample at a time and wraps the phase either way (an FM deviation can pull it back). */
    void processModulated(float note, const Params &p, float *l, float *r, int n)
    {
        const float delta = freqDelta(note);
        const float formantMul = (p.formant != 0.0f)
            ? std::exp(p.formant * 1.60943791243f) : 1.0f;
        const FrameTable *fa = nullptr, *fb = nullptr;
        float frac = 0.0f;
        posToFrames(p.position, fa, fb, frac);
        const MipLevel &la = fa->levelRef(note), &lb = fb->levelRef(note);
        const bool pm = p.modKind == 1;
        for (int i = 0; i < n; i++) {
            const float m = p.modDepth * p.mod[i];
            float at = phase;
            if (pm) { at += m; at -= std::floor(at); }
            const float ph = distort(std::min(kAlmostOne, at), p.bend, formantMul);
            float s = FrameTable::readLinear(la, ph);
            if (frac > 0.0f)
                s += (FrameTable::readLinear(lb, ph) - s) * frac;
            postProcess(p, s);
            l[i] += s * p.leftGain;
            r[i] += s * p.rightGain;
            phase += pm ? delta : delta + m;
            phase -= std::floor(phase);
        }
        lastTableIndex = tableIndex = posToIndex(p.position);
    }

    void processComplex(float note, const Params &p, float *l, float *r, int n)
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
            for (; todo > 0; todo--) {
                float ph = distort(std::min(kAlmostOne, phase), p.bend, formantMul);
                float s = FrameTable::readLinear(la, ph);
                if (frac > 0.0f)
                    s += (FrameTable::readLinear(lb, ph) - s) * frac;
                postProcess(p, s);
                *l++ += s * p.leftGain;
                *r++ += s * p.rightGain;
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

    void processCrossfade(float note, const Params &p, float *l, float *r, int n)
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
                postProcess(p, s);
                *l++ += s * p.leftGain;
                *r++ += s * p.rightGain;
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

    const Wavetable *wt = nullptr;
    double sampleRate = 44100.0;
    float phase = 0.0f;
    int tableIndex = -1, lastTableIndex = -1;
    bool blockDC = true;
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
    /* One more of Serum's warps on the oscillator: 0 none, 1 Filter LP, 2 Filter HP, 3 FM from Sub, 4 PD from Sub, 5 RM from Sub; xamount is
     * Serum's 0..1 amount. The laws are measured (calibration/probe_warp_fm.py, probe_filterwarp.py). */
    int   xwarp = 0;
    float xamount = 0.0f;
};

/* What each warp does at an amount, from Serum:
 *  - Filter LP / HP: a first-order filter whose corner is an ABSOLUTE frequency (it does not follow the note): log2(Hz) by amount below. LP is open at 1, HP is shut.
 *  - PD from Sub: the phase pushed by the Sub, index 33.6 a^4 rad (the same at every pitch).
 *  - FM from Sub: the frequency pushed by the Sub, a deviation of 58 200 a^4 Hz, so the index is that over the Sub's frequency.
 *  - RM from Sub: (1 - a) x + a 1.314 x S, the carrier falling to nothing at 1.
 * The Sub is taken at unit level whatever its own level. */
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
        fwL = fwR = 0.0f;
    }
    void noteOn(float phase = -1.0f)
    {
        for (auto &o : oscs) o.noteOn(phase);
        blockerL.reset();
        blockerR.reset();
        fwL = fwR = 0.0f;
    }

    /* `sub` is the Sub's waveform at unit level for the block, needed only by the FM / PD / RM warps (null: those warps are off). */
    void processAdding(float note, const VoicedWTParams &vp, float *l, float *r, int n, const float *sub = nullptr)
    {
        const int xw = vp.xwarp;
        const bool needSub = xw >= 3 && xw <= 5;
        const bool active = xw >= 1 && xw <= 5 && vp.xamount > 0.0f && (!needSub || sub);
        const float a = std::min(std::max(vp.xamount, 0.0f), 1.0f), a4 = (a * a) * (a * a);
        /* Filter warps: a one-pole (trapezoidal) at the corner, once per stack, since the filter is linear and the stack is the sum of its streams */
        float fg = 0.0f;
        if (active && xw <= 2) {
            const float k = std::tan(std::min((float) M_PI * filterWarpCornerHz(a) / (float) sampleRate, 1.5533f));
            fg = k / (1.0f + k);
        }
        while (n > 0) {
            const int todo = std::min(n, kStackBlock);
            float sl[kStackBlock] = {}, sr[kStackBlock] = {};
            renderStack(note, vp, sl, sr, todo, active && (xw == 3 || xw == 4) ? sub : nullptr, xw == 3 ? 2 : 1,
                        xw == 3 ? kXwarpFmHz / (float) sampleRate * a4 : kXwarpPdCycles * a4);
            if (active && xw == 5) {
                const float dry = 1.0f - a, wet = a * kXwarpRmGain;
                for (int i = 0; i < todo; i++) {
                    const float g = dry + wet * sub[i];
                    sl[i] *= g; sr[i] *= g;
                }
            } else if (active && xw <= 2) {
                const bool hp = xw == 2;
                for (int i = 0; i < todo; i++) {
                    float v = (sl[i] - fwL) * fg, y = v + fwL; fwL = y + v; sl[i] = hp ? sl[i] - y : y;
                    v = (sr[i] - fwR) * fg; y = v + fwR; fwR = y + v; sr[i] = hp ? sr[i] - y : y;
                }
            }
            for (int i = 0; i < todo; i++) {
                l[i] += sl[i] - blockerL.process(sl[i]);
                r[i] += sr[i] - blockerR.process(sr[i]);
            }
            l += todo; r += todo; n -= todo;
            if (sub) sub += todo;
        }
    }

private:
    static constexpr int kStackBlock = 128;

    void renderStack(float note, const VoicedWTParams &vp, float *l, float *r, int n, const float *mod, int modKind, float modDepth)
    {
        WTOscillator::Params p;
        p.position = vp.position; p.bend = vp.bend; p.formant = vp.formant;
        p.asym = vp.asym; p.fold = vp.fold; p.sync = vp.sync;
        p.shape = vp.shape; p.shapeGain = vp.shapeGain; p.shapeOut = vp.shapeOut;
        if (mod) { p.mod = mod; p.modKind = modKind; p.modDepth = modDepth; }

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
    float fwL = 0.0f, fwR = 0.0f;        /* the filter warp's state */
};

} // namespace tb
