/* Multimode filter — de-JUCE'd port of gin::Filter (BSD-3, Roland Rabien).
 *
 * The DSP itself is the vendored AudioFilter library (SVF biquad with MZTi
 * coefficient matching) — the exact same code the original synth runs, so
 * there is no coefficient-fidelity risk. This wrapper reproduces gin::Filter:
 * type/slope selection, one or two cascaded stages (12/24 dB), stereo.
 *
 * WavetableVoice usage notes (preserved semantics):
 *   q = gin::Q / (1 - 0.99*res)  with gin::Q = 0.70710678
 *   the second stage always runs at fixed Q (gin passes gin::Q, not q).
 */
#pragma once

#include "audiofilter/AudioFilterTypes.h"
#include "audiofilter/FilterInstance.h"
#include "audiofilter/ParametricCreator.h"

#include <cmath>
#include <memory>

namespace tb {

inline constexpr float kFilterQ = 0.70710678118655f;   /* gin::Q */

inline float gainToDecibels(float gain)
{
    /* juce::Decibels::gainToDecibels, -100 dB floor */
    return gain > 0.0f ? std::fmax(-100.0f, 20.0f * std::log10(gain)) : -100.0f;
}

class Filter {
public:
    enum Type  { none = 0, lowpass, highpass, bandpass, notch,
                 lowshelf, highshelf, peak, allpass };
    enum Slope { db12 = 0, db24 };

    Filter()
        : biquad1(1), biquad2(1),
          inst1(2 /*channels*/), inst2(2)
    {
        biquad1.resize(1);
        biquad2.resize(1);
    }

    void setSampleRate(double sr) { if (sr != sampleRate) { sampleRate = sr; ok1 = ok2 = false; } }
    void setType(Type t)          { if (t != type) { type = t; ok1 = ok2 = false; } }
    void setSlope(Slope s)        { slope = s; }
    float getFrequency() const    { return freq; }

    void reset()
    {
        inst1.clear();
        inst2.clear();
    }

    /* The coefficients are a pure function of (type, freq, gain, Q, sample rate), so a call that repeats the last one (a steady cutoff across the block's pieces, the next
     * block of a held note) changes nothing and is skipped; the second stage is only designed while the slope uses it. Designing the pair was a large part of a voice's
     * per-block cost, paid again for every piece a note-boundary split cut the block into. */
    void setParams(float freq_, float q_, float g_ = 0.0f)
    {
        freq = freq_; q = q_; g = g_;
        if (type == none) return;

        if (!(ok1 && freq_ == d1f && q_ == d1q && g_ == d1g)) {
            AudioFilter::ParametricCreator::createMZTiStage(
                biquad1[0], freq, gainToDecibels(g), q, conv(type), sampleRate);
            inst1.setParams(biquad1);
            ok1 = true; d1f = freq_; d1q = q_; d1g = g_;
        }
        if (slope == db24) designStage2();
        else ok2 = false;
    }

    /* In-place stereo processing. */
    void process(float *l, float *r, int n)
    {
        if (type == none) return;
        float *chans[2] = { l, r };
        const float *inChans[2] = { l, r };
        switch (slope) {
        case db12:
            inst1.processBlock(chans, inChans, n);
            break;
        case db24:
            if (!ok2) designStage2();                         /* the slope was switched since the last setParams */
            inst1.processBlock(chans, inChans, n);
            inst2.processBlock(chans, inChans, n);
            break;
        }
    }

private:
    void designStage2()
    {
        if (ok2 && freq == d2f && g == d2g) return;
        AudioFilter::ParametricCreator::createMZTiStage(
            biquad2[0], freq, gainToDecibels(g), kFilterQ, conv(type), sampleRate);
        inst2.setParams(biquad2);
        ok2 = true; d2f = freq; d2g = g;
    }

    static AudioFilter::FilterType conv(Type t)
    {
        switch (t) {
        case lowpass:   return AudioFilter::afLoPass;
        case highpass:  return AudioFilter::afHiPass;
        case bandpass:  return AudioFilter::afBandPass;
        case notch:     return AudioFilter::afNotch;
        case lowshelf:  return AudioFilter::afLoShelf;
        case highshelf: return AudioFilter::afHiShelf;
        case peak:      return AudioFilter::afPeak;
        case allpass:
        case none:
        default:        return AudioFilter::afAllPass;
        }
    }

    Type type = none;
    Slope slope = db12;
    double sampleRate = 44100.0;
    float freq = 2000.0f, q = kFilterQ, g = 0.0f;
    bool ok1 = false, ok2 = false;                            /* the stages hold the design for d1* / d2* */
    float d1f = 0, d1q = 0, d1g = 0, d2f = 0, d2g = 0;

    AudioFilter::BiquadParamCascade biquad1, biquad2;
    AudioFilter::FilterInstance<float> inst1, inst2;
};

} // namespace tb
