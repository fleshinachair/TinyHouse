#pragma once
/* processVital() below is derived from Vital's comb filter (synthesis/filters/comb_filter.cpp, https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later:
 * a build that includes it is GPL-3.0-or-later (see THIRD-PARTY.md and LICENSE-GPL-3.0). */
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
    void reset() { for (auto &a : vs) a[0] = a[1] = 0.0f; vPrev[0] = -1.0f; if (used) { std::fill(bufL, bufL + kLen, 0.0f); std::fill(bufR, bufR + kLen, 0.0f); used = false; } pos = 0; prevD = -1.0f; }   /* (the 128 KB are cleared only if the filter ran) */

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

    /* Vital's comb filter (comb_filter.cpp), run on the same delay lines. style 0 comb (feedback through the filter pair), 1 positive flange, 2 negative flange; reso 0..1;
     * hz the comb's frequency (the voice's cutoff, tuned); blend 0..2 and trans (semitones) the Blend and Blend Transpose knobs; band: the "band spread" filter styles, where Blend is the
     * width (up to 8 octaves each way around the transposed cutoff) and the loop filter is a band-pass. Ramps every coefficient across the block like Vital does. */
    void processVital(float *l, float *r, int n, int style, float reso, float hz, float blend, float trans, bool band)
    {
        used = true;
        const float kNyq = 0.45351473923f * sampleRate;
        float fb, scale;
        if (style == 0) {
            fb = -1.0f + 2.0f * std::clamp(reso, 0.0f, 1.0f);
            fb /= std::sqrt(std::fabs(fb) + 0.00001f);
            scale = -fb * fb * 0.5f + 1.0f;
        } else {
            fb = std::clamp(reso, 0.0f, 1.0f);
            scale = 1.0f / (fb + 1.0f);
        }
        const float note = 69.0f + 12.0f * std::log2(std::max(hz, 1.0f) / 440.0f);
        auto toHz = [](float m) { return 8.1757989156f * std::exp2(m * (1.0f / 12.0f)); };
        auto coef = [&](float f) { const float d = f * 3.14159265f / sampleRate; return std::tan(d / (d + 1.0f)); };
        float f1, f2, lowG, highG;
        const float minNote = note - 48.0f;
        if (band) {
            const float centre = note + trans, range = blend * 0.5f * 8.0f * 12.0f;
            f1 = std::clamp(toHz(centre + range), 1.0f, sampleRate / 2.1f);
            f2 = std::clamp(toHz(std::max(minNote, centre - range)), 1.0f, sampleRate / 2.1f);
            lowG = f2 / f1 + 1.0f; highG = 0.0f;
        } else {
            lowG = std::clamp(2.0f - blend, 0.0f, 1.0f); highG = std::clamp(blend, 0.0f, 1.0f);
            f1 = std::clamp(toHz(note + trans), 1.0f, kNyq);
            f2 = std::clamp(toHz(minNote), 1.0f, kNyq);
        }
        const float c1 = coef(f1), c2 = coef(f2);
        const float minPeriod = style == 2 ? 4.0f : 2.0f, maxPeriod = (float) (kLen - 8);
        const float targetP = std::clamp(sampleRate / std::clamp(hz, 1.0f, kNyq), minPeriod, maxPeriod);
        if (vPrev[0] < 0.0f) { vPrev[0] = targetP; vPrev[1] = fb; vPrev[2] = scale; vPrev[3] = c1; vPrev[4] = c2; vPrev[5] = lowG; vPrev[6] = highG; }
        const float tgt[7] = { targetP, fb, scale, c1, c2, lowG, highG };
        const int m = kLen - 1;
        float *buf[2] = { bufL, bufR };
        float *io[2] = { l, r };
        for (int i = 0; i < n; i++) {
            const float u = (float) (i + 1) / (float) n;
            float cur[7];
            for (int k = 0; k < 7; k++) cur[k] = vPrev[k] + (tgt[k] - vPrev[k]) * u;
            const float period = style == 2 ? cur[0] * 0.5f : cur[0];
            const int pi = (int) period;
            const float t = 1.0f - (period - (float) pi);
            const int b = pos - 1 - pi;                                       /* the newest pushed sample is pos - 1; t = 1 reads buf[b] */
            for (int c = 0; c < 2; c++) {
                float *bf = buf[c];
                const float p0 = bf[(b - 2) & m], p1 = bf[(b - 1) & m], p2 = bf[b & m], p3 = bf[(b + 1) & m];
                const float rd = 0.5f * ((2.0f * p1) + (p2 - p0) * t + (2.0f * p0 - 5.0f * p1 + 4.0f * p2 - p3) * t * t + (3.0f * p1 - p0 - 3.0f * p2 + p3) * t * t * t);
                float *st = vs[c];
                const float x = io[c][i];
                auto onePole = [](float &s, float in, float cf) { const float d = cf * (in - s); s += d; const float o = s; s += d; return o; };
                auto hardTanh = [](float v) { const float k = 0.66f; const float cl = std::clamp(v, -k, k); return cl + std::tanh((v - cl) * (1.0f / (1.0f - k))) * (1.0f - k); };
                float out;
                if (style == 0) {
                    const float comb = cur[2] * x + cur[1] * rd;
                    const float low = onePole(st[0], comb, cur[3]), high = comb - low;
                    const float s1 = cur[5] * low + cur[6] * high;
                    out = s1 - onePole(st[1], s1, cur[4]);
                    bf[pos & m] = hardTanh(out);
                } else {
                    const float low = onePole(st[0], rd, cur[3]), high = rd - low;
                    const float s1 = cur[5] * low + cur[6] * high;
                    const float fo = s1 - onePole(st[1], s1, cur[4]);
                    const float xin = x * 0.70710678119f;
                    if (style == 1) { bf[pos & m] = xin + hardTanh(fo * cur[1]); out = xin * cur[2] + fo; }
                    else            { bf[pos & m] = xin - hardTanh(fo * cur[1]); out = xin * cur[2] - fo; }
                }
                io[c][i] = out;
            }
            pos = (pos + 1) & m;
        }
        for (int k = 0; k < 7; k++) vPrev[k] = tgt[k];
    }

private:
    float sampleRate = 44100.0f;
    float bufL[kLen] = {}, bufR[kLen] = {};
    int pos = 0;
    bool used = false;
    float prevD = -1.0f;
    float vs[2][2] = {}, vPrev[7] = { -1.0f, 0, 0, 0, 0, 0, 0 };            /* Vital comb: the two one-pole states per channel, the previous block's coefficients */
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

/* Vital's Phaser voice filter (model 7), derived from Vital's synthesis/filters/phaser_filter.cpp (same notice as the Vital comb above): twelve first-order all-pass stages in three groups of four, Blend 0 / 1 / 2 taking the output
 * after 4 / 8 / 12 of them (crossfaded between); the input (drive 1 + Reso / 2) plus +-tanh(Reso x the band left after removing the lows and highs of the last all-pass output, corners 20 x and 1/20 of the cutoff)
 * feeds the chain; the output is half the sum of the input and +-the all-pass output (the sign is the Style). The one-pole coefficient is Vital's tan(x / (x + 1)), x = pi f / sr. */
class VitalPhaserFilter {
public:
    bool on = false;
    void setSampleRate(double sr) { sampleRate = (float) sr; reset(); }
    void reset() { for (int c = 0; c < 2; c++) { for (int k = 0; k < 12; k++) st[c][k] = 0.0f; lows[c] = highs[c] = out[c] = 0.0f; } prevC = -1.0f; }

    void process(float *l, float *r, int n, float hz, float reso, float blend, bool inverted, bool clean)
    {
        const float x = 3.14159265f * std::min(hz / sampleRate, 1.0f), cT = std::tan(x / (x + 1.0f));
        if (prevC < 0.0f) prevC = cT;
        const float res = std::min(std::max(reso, 0.0f), 1.0f), drive = res * 0.5f + 1.0f, inv = inverted ? -1.0f : 1.0f;
        blend = std::min(std::max(blend, 0.0f), 2.0f);
        const float p1 = std::min(std::max(1.0f - blend, 0.0f), 1.0f), p5 = std::min(std::max(blend - 1.0f, 0.0f), 1.0f), p3 = 1.0f - p1 - p5;
        auto one = [](float &s, float in, float cc) { const float d = cc * (in - s); s += d; const float o = s; s += d; return o; };
        for (int i = 0; i < n; i++) {
            const float cf = prevC + (cT - prevC) * (float) (i + 1) / (float) n;
            float *io[2] = { l + i, r + i };
            for (int c = 0; c < 2; c++) {
                const float in = *io[c];
                float *S = st[c];
                const float lo = one(lows[c], out[c], std::min(cf * 20.0f, 0.9f));
                const float hi = one(highs[c], lo, cf * (1.0f / 20.0f));
                const float band = lo - hi;
                float a = clean ? drive * in + inv * std::tanh(res * band) : std::min(std::max(drive * in + inv * res * band, -1.0f), 1.0f);
                for (int k = 0; k < 4; k++) { const float so = one(S[k], a, cf); a -= 2.0f * so; }
                const float o1 = a;
                for (int k = 4; k < 8; k++) { const float so = one(S[k], a, cf); a -= 2.0f * so; }
                const float o3 = a;
                for (int k = 8; k < 12; k++) { const float so = one(S[k], a, cf); a -= 2.0f * so; }
                const float o5 = a;
                float ap = p1 * o1 + p3 * o3 + p5 * o5;
                if (!(ap > -1e6f && ap < 1e6f)) ap = 0.0f;
                out[c] = ap;
                *io[c] = (in + inv * ap) * 0.5f;
            }
        }
        prevC = cT;
    }

private:
    float sampleRate = 44100.0f, prevC = -1.0f;
    float st[2][12] = {}, lows[2] = {}, highs[2] = {}, out[2] = {};
};

}  // namespace tb
