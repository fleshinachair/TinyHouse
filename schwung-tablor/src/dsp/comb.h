#pragma once
/* Serum's comb / flanger voice-filter models. Fitted to the per-harmonic response of a held saw (calibration/probe_comb_saw.json, to 0.01-0.12 dB rms over 60 harmonics):
 *
 *   w[n] = x[n] + g w[n - D]                   a feedback comb tuned to the cutoff: D = sample rate / cutoff, g = +-Reso (0..1, capped at 0.99)
 *   y[n] = a (w[n] + c w[n - D]),  a = 0.4965 / (1 + |g|)
 *
 * Comb +/-: g > 0 / g < 0 and c = sign g^2. Flange +/-: c = sign (so the valleys between the teeth are as deep as 35 dB). The cutoff is Serum's knob on the law
 * midiNoteToHz(12 log2(2697) x Freq) exactly (425.0 Hz at Freq 0.5), with no filter calibration offset.
 * Cubic (4-point Lagrange) interpolation reads the fractional delay; D is ramped across a block so a moving cutoff does not click. */
#include <algorithm>
#include <cmath>

namespace tb {

class CombFilter {
public:
    static constexpr int kLen = 16384;                       /* 2.7 Hz at 44.1 kHz */
    void setSampleRate(double sr) { sampleRate = (float) sr; reset(); }
    void reset() { if (used) { std::fill(bufL, bufL + kLen, 0.0f); std::fill(bufR, bufR + kLen, 0.0f); used = false; } pos = 0; prevD = -1.0f; }   /* (the 128 KB are cleared only if the filter ran) */

    /* kind 0 Comb, 1 Flange; sign +1 / -1; reso 0..1; hz the comb's frequency */
    void process(float *l, float *r, int n, int kind, int sign, float reso, float hz)
    {
        used = true;
        const float targetD = std::clamp(sampleRate / std::max(hz, 3.0f), 4.0f, (float) (kLen - 8));
        if (prevD < 0.0f) prevD = targetD;
        const float g = (float) sign * std::min(reso, 0.99f);
        const float c = kind ? (float) sign : (float) sign * g * g;
        const float a = 0.4965f / (1.0f + std::fabs(g));
        const float d0 = prevD, d1 = targetD;
        for (int i = 0; i < n; i++) {
            const float D = d0 + (d1 - d0) * (float) (i + 1) / (float) n;
            const int ip = (int) D;
            const float fr = D - (float) ip;
            /* Lagrange 4-point at the delay D: samples pos-ip+1 .. pos-ip-2 */
            const float c0 = -fr * (fr - 1.0f) * (fr - 2.0f) * (1.0f / 6.0f);
            const float c1 = (fr + 1.0f) * (fr - 1.0f) * (fr - 2.0f) * 0.5f;
            const float c2 = -(fr + 1.0f) * fr * (fr - 2.0f) * 0.5f;
            const float c3 = (fr + 1.0f) * fr * (fr - 1.0f) * (1.0f / 6.0f);
            const int m = kLen - 1;
            const int b = pos - ip;
            auto tap = [&](const float *buf) {
                return c0 * buf[(b + 1) & m] + c1 * buf[b & m] + c2 * buf[(b - 1) & m] + c3 * buf[(b - 2) & m];
            };
            const float dl = tap(bufL), dr = tap(bufR);
            const float wl = l[i] + g * dl, wr = r[i] + g * dr;
            bufL[pos] = wl; bufR[pos] = wr;
            l[i] = a * (wl + c * dl);
            r[i] = a * (wr + c * dr);
            pos = (pos + 1) & m;
        }
        prevD = targetD;
        /* keep denormals out of a ringing tail */
        if (std::fabs(bufL[(pos - 1) & (kLen - 1)]) < 1e-20f) bufL[(pos - 1) & (kLen - 1)] = 0.0f;
        if (std::fabs(bufR[(pos - 1) & (kLen - 1)]) < 1e-20f) bufR[(pos - 1) & (kLen - 1)] = 0.0f;
    }

private:
    float sampleRate = 44100.0f;
    float bufL[kLen] = {}, bufR[kLen] = {};
    int pos = 0;
    bool used = false;
    float prevD = -1.0f;
};

/* Serum's Phase 12 / 24 / 36 / 48 "P" voice-filter models: the flange law with the delay replaced by a chain of N = 2, 4, 6, 8 first-order all-passes whose corner is 0.511 x the
 * cutoff (calibration/probe_phase_saw.json: the chain alone, Reso 0, fits 60 harmonics to 0.0-0.3 dB; with feedback g = Reso, 0.2-1.5 dB up to 60 %, 3-5 dB at 90 %):
 *   w = x + g A(w),  y = a (w + A(w)),  a = 0.4965 / (1 + g),  A = the chain.
 * The loop has no delay (an all-pass passes its input straight through by a1), so w is solved: w = (x + g r) / (1 - g a1^N) where r is the chain's output for a zero input,
 * which is what the stages' states alone give. */
class PhaserFilter {
public:
    static constexpr int kMaxStages = 8;
    void setSampleRate(double sr) { sampleRate = (float) sr; reset(); }
    void reset() { for (int c = 0; c < 2; c++) for (int k = 0; k < kMaxStages; k++) s[c][k] = 0.0f; prevA = 2.0f; }

    void process(float *l, float *r, int n, int stages, float reso, float hz)
    {
        const float t = std::tan(3.14159265f * std::min(0.511f * hz, 0.45f * sampleRate) / sampleRate);
        const float a1T = (t - 1.0f) / (t + 1.0f);
        if (prevA > 1.0f) prevA = a1T;
        const float g = std::min(reso, 0.99f), gain = 0.4965f / (1.0f + g);
        const float aStart = prevA;
        for (int i = 0; i < n; i++) {
            const float a1 = aStart + (a1T - aStart) * (float) (i + 1) / (float) n;
            float a1n = 1.0f;
            for (int k = 0; k < stages; k++) a1n *= a1;
            const float den = 1.0f / (1.0f - g * a1n);
            float *ch[2] = { l + i, r + i };
            for (int c = 0; c < 2; c++) {
                float *st = s[c];
                float u = 0.0f;                                   /* the chain's response to a zero input: the states alone */
                for (int k = 0; k < stages; k++) u = a1 * u + st[k];
                const float w = (*ch[c] + g * u) * den;
                float v = w;
                for (int k = 0; k < stages; k++) { const float y = a1 * v + st[k]; st[k] = v - a1 * y; v = y; }
                *ch[c] = gain * (w + v);
            }
        }
        prevA = a1T;
    }

private:
    float sampleRate = 44100.0f;
    float s[2][kMaxStages] = {};
    float prevA = 2.0f;
};

}  // namespace tb
