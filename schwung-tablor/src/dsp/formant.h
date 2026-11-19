#pragma once
/* The vowel tables and the band-pass sum below are derived from Vital's formant filter (synthesis/filters/formant_filter.cpp, formant_manager.cpp, digital_svf.cpp, https://github.com/mtytel/vital),
 * Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later: a build that includes it is GPL-3.0-or-later (see THIRD-PARTY.md and LICENSE-GPL-3.0). */
/* A voice filter made of up to five parallel band-pass resonators, the shape of a vowel: Vital's Formant model (four of them, set by an X / Y position between four vowels) and Serum's three
 * Formant models (ONE five, TWO three, TWB four, read from measured tables: formant_tables.h). Each resonator is the Cytomic state-variable filter Vital uses, its band output clipped at +-1 and scaled by the formant's gain.
 *
 *   resonator i:  r = clamp(Resonance x q_i), Q = 4 + 26 r^3, drive = 1 / (1 + 2 r^3), output = clip(band(drive x input)) x 10^(gain_i / 20)
 *   formant i:    midi_i = vowel midi_i + (80 - vowel midi_i) x Spread + Transpose   (the vowels are bilinear in X / Y between the corners of the chosen style)
 *
 * The settings (one FormantSet per block) are ramped across the block, so a moving X / Y or a pitch-following Transpose does not click. */
#include <algorithm>
#include <cmath>

#include "formant_tables.h"

namespace tb {

struct FormantSet {
    static constexpr int kMax = 5;
    int n = 0;                                   /* resonators in use */
    float midi[kMax] = {}, k[kMax] = {}, drive[kMax] = {}, post[kMax] = {};     /* k = 1 / Q */
    float clip = 1.0f;                           /* Vital clips each resonator's band output at +-1; Serum's are linear (a large number) */
};

class FormantBank {
public:
    void setSampleRate(double sr) { sampleRate = (float) sr; reset(); }
    void reset() { for (auto &c : ic) for (auto &f : c) f[0] = f[1] = 0.0f; have = false; used = false; }
    bool wasUsed() const { return used; }

    void process(float *l, float *r, int n, const FormantSet &s)
    {
        if (!have) { prev = s; have = true; }
        used = true;
        float *io[2] = { l, r };
        float outL[256], outR[256];                          /* (a block is at most 128 samples: kBlock) */
        float *out[2] = { outL, outR };
        for (int c = 0; c < 2; c++) std::fill(out[c], out[c] + n, 0.0f);
        const float nyq = 0.49f * sampleRate;
        for (int f = 0; f < s.n; f++) {
            const float g0 = coef(prev.midi[f], nyq), g1 = coef(s.midi[f], nyq);
            const float k0 = prev.k[f], k1 = s.k[f], d0 = prev.drive[f], d1 = s.drive[f], p0 = prev.post[f], p1 = s.post[f];
            float ic1[2] = { ic[0][f][0], ic[1][f][0] }, ic2[2] = { ic[0][f][1], ic[1][f][1] };
            for (int i = 0; i < n; i++) {
                const float t = (float) (i + 1) / (float) n;
                const float g = g0 + (g1 - g0) * t, k = k0 + (k1 - k0) * t, d = d0 + (d1 - d0) * t, p = p0 + (p1 - p0) * t;
                const float a0 = 1.0f / (g * g + g * k + 1.0f), a1 = a0 * g, a2 = a1 * g;       /* one set of coefficients for both channels */
                for (int c = 0; c < 2; c++) {
                    const float v3 = d * io[c][i] - ic2[c];
                    const float v1 = a0 * ic1[c] + a1 * v3;
                    const float v2 = ic2[c] + a1 * ic1[c] + a2 * v3;
                    ic1[c] = 2.0f * v1 - ic1[c];
                    ic2[c] = 2.0f * v2 - ic2[c];
                    out[c][i] += std::clamp(v1, -s.clip, s.clip) * p;
                }
            }
            for (int c = 0; c < 2; c++) { ic[c][f][0] = ic1[c]; ic[c][f][1] = ic2[c]; }
        }
        for (int c = 0; c < 2; c++) std::copy(out[c], out[c] + n, io[c]);
        prev = s;
    }

private:
    float coef(float midi, float nyq) const
    {
        const float hz = std::clamp(440.0f * std::exp2((midi - 69.0f) * (1.0f / 12.0f)), 1.0f, nyq);
        return std::tan(hz * 3.14159265358979f / sampleRate);
    }
    float sampleRate = 44100.0f;
    float ic[2][FormantSet::kMax][2] = {};
    FormantSet prev;
    bool have = false, used = false;
};

/* Vital's vowels (formant_filter.cpp): per formant a gain (dB), a resonance factor and a midi note. */
struct VowelFormant { float gain, q, midi; };
inline const VowelFormant *vitalVowel(int v)
{
    static const VowelFormant a[4] = { { -2, 0.66f, 75.7552343327f }, { -8, 0.75f, 84.5454706023f }, { -9, 1.0f, 100.08500317f }, { -10, 1.0f, 101.645729657f } };
    static const VowelFormant e[4] = { { 0, 0.66f, 67.349957715f }, { -14, 0.75f, 92.39951181f }, { -4, 1.0f, 99.7552343327f }, { -14, 1.0f, 103.349957715f } };
    static const VowelFormant i[4] = { { 0, 0.8f, 61.7825925179f }, { -15, 0.75f, 94.049554095f }, { -17, 1.0f, 101.03821678f }, { -20, 1.0f, 103.618371471f } };
    static const VowelFormant o[4] = { { -2, 0.7f, 67.349957715f }, { -6, 0.75f, 79.349957715f }, { -14, 1.0f, 99.7552343327f }, { -14, 1.0f, 101.03821678f } };
    static const VowelFormant u[4] = { { 0, 0.7f, 65.0382167797f }, { -20, 0.75f, 74.3695077237f }, { -17, 1.0f, 100.408607741f }, { -14, 1.0f, 101.645729657f } };
    switch (v) { case 0: return a; case 1: return e; case 2: return i; case 3: return o; default: return u; }
}

/* The four resonators of Vital's Formant model. style 0: the corners (bottom left, bottom right, top left, top right) are a, i, u, o; style 1: a, o, i, e. x, y 0..1, spread -1..1, transpose in
 * semitones, resonance the knob 0..1. */
inline FormantSet vitalFormantSet(int style, float x, float y, float spread, float transpose, float resonance)
{
    static const int kCorners[2][4] = { { 0, 2, 4, 3 }, { 0, 3, 2, 1 } };           /* vowel indexes (a e i o u = 0..4) at bottom left, bottom right, top left, top right */
    const int *cn = kCorners[style != 0 ? 1 : 0];
    x = std::clamp(x, 0.0f, 1.0f); y = std::clamp(y, 0.0f, 1.0f);
    FormantSet s;
    s.n = 4;
    for (int i = 0; i < 4; i++) {
        const VowelFormant &bl = vitalVowel(cn[0])[i], &br = vitalVowel(cn[1])[i], &tl = vitalVowel(cn[2])[i], &tr = vitalVowel(cn[3])[i];
        auto mix = [&](float VowelFormant::*m) {
            const float bot = bl.*m + (br.*m - bl.*m) * x, top = tl.*m + (tr.*m - tl.*m) * x;
            return bot + (top - bot) * y;
        };
        const float gain = std::clamp(mix(&VowelFormant::gain), -15.0f, 15.0f);
        const float mid = mix(&VowelFormant::midi);
        const float r = std::clamp(resonance * mix(&VowelFormant::q), 0.0f, 1.0f), r3 = r * r * r;
        s.midi[i] = mid + (80.0f - mid) * spread + transpose;
        s.k[i] = 1.0f / (4.0f + 26.0f * r3);
        s.drive[i] = 1.0f / (2.0f * r3 + 1.0f);
        s.post[i] = std::pow(10.0f, gain * 0.05f);
    }
    return s;
}

inline constexpr float kSerumFormantPerNote = 1.0f / 136.7657f;      /* the knob 0..1 is the cutoff note over 12 log2(2697) semitones (the comb's law too) */
/* Serum's Formant models (0 ONE, 1 TWO, 2 TWB) at the filter's Freq knob x (0..1, the cutoff note over 136.7), Var 0..1 and Reso 0..1 (every bandwidth is divided by 1 + 2 Reso: measured, 0.09 dB rms). Each is the sum of unit-ish-gain band-pass resonators (centre, bandwidth and peak gain
 * from the measured grid, bilinear in the log of the centre and linear in the others); the unit-peak band-pass is the state-variable band output times k = bandwidth / centre. */
inline FormantSet serumFormantSet(int model, float x, float var, float reso)
{
    const int n = model == 0 ? kSerumFormantOneN : model == 1 ? kSerumFormantTwoN : kSerumFormantTwbN;
    const float *tab = model == 0 ? kSerumFormantOne : model == 1 ? kSerumFormantTwo : kSerumFormantTwb;
    const float fx = std::clamp(x, 0.0f, 1.0f) * (float) (kSerumFormantX - 1), fv = std::clamp(var, 0.0f, 1.0f) * (float) (kSerumFormantVar - 1);
    const int ix = std::min((int) fx, kSerumFormantX - 2), iv = std::min((int) fv, kSerumFormantVar - 2);
    const float tx = fx - (float) ix, tv = fv - (float) iv;
    FormantSet s;
    s.n = n;
    s.clip = 1.0e6f;
    for (int r = 0; r < n; r++) {
        float lf = 0.0f, bw = 0.0f, g = 0.0f;
        for (int c = 0; c < 4; c++) {
            const int dx = c & 1, dv = c >> 1;
            const float w = (dx ? tx : 1.0f - tx) * (dv ? tv : 1.0f - tv);
            const float *t = tab + (((ix + dx) * kSerumFormantVar + iv + dv) * n + r) * 3;
            lf += w * std::log(t[0]); bw += w * t[1]; g += w * t[2];
        }
        const float hz = std::exp(lf);
        s.midi[r] = 69.0f + 12.0f * std::log2(hz * (1.0f / 440.0f));
        /* The fits are analog band-pass shapes (x = f / centre); the state-variable stage sees the frequency through tan(pi f / fs). Near the centre that stretches the skirts by 2 theta / sin 2 theta
         * (theta = pi f / fs: 1.09 at 5 kHz, 1.44 at 10 kHz at 44.1 kHz), so the resonator is widened by the same factor to keep the analog width (TWB's top resonators sit near 10 kHz). */
        const float theta = 3.14159265f * hz * (1.0f / 44100.0f), c = 2.0f * theta / std::sin(2.0f * theta);
        s.k[r] = bw / (hz * (1.0f + 2.0f * std::clamp(reso, 0.0f, 1.0f))) * c;
        s.drive[r] = 1.0f;
        s.post[r] = s.k[r] * g;
    }
    return s;
}

}  // namespace tb
