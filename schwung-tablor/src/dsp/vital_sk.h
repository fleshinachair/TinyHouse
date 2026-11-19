/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (c) 2026 Mathew Taylor. This file is part of TinyHouse's Vital import and contains code derived from Vital
 * (https://github.com/mtytel/vital), Copyright 2013-2019 Matt Tytel, GNU GPL version 3 or later. See THIRD-PARTY.md and LICENSE-GPL-3.0.
 *
 * Vital's Analog filter model: the Sallen-Key filter of src/synthesis/filters/sallen_key_filter.cpp, 12 or 24 dB, with the Blend knob between low-pass, band-pass and high-pass.
 * Two one-pole stages in a loop with the resonance feedback, a tanh in front of the first stage, and the drive and resonance laws setupFilter gives. Measured through the plugin on a saw
 * with the filter turned up and down: the response matches this to a fraction of a dB at any resonance (the level-dependent part is the tanh, so the signal must arrive at Vital's own
 * level: the engine's is kVitalFull times it).
 *
 * Only for presets from the Vital import (the engine's biquad filters keep every other preset exactly as it was).
 */
#pragma once

#include <algorithm>
#include <cmath>

#include "saturate.h"

namespace tb {

/* Vital's own tanh (futils::tanh), a rational fit that is close to the real one but not the same in the shoulder. */
inline float vitalTanh(float v)
{
    const float a = std::fabs(v), sq = v * v;
    const float num = v * (2.45550750702956f + 2.45550750702956f * a + sq * (0.893229853513558f + 0.821226666969744f * a));
    const float den = 2.44506634652299f + (2.44506634652299f + sq) * std::fabs(v + 0.814642734961073f * v * a);
    return num / den;
}

struct VitalSK {
    static constexpr float kVitalFull = 0.33650f;                    /* the engine's level against Vital's (10^(-9.45 / 20)), as in vitalFilterDriveIn */
    bool on = false;
    bool steep = false;                                               /* 24 dB */
    double sr = 44100.0;

    /* what the block aims at (set()) and where the previous one ended */
    struct Coefs { float coef = 0, reso = 0, drive = 1, post = 1, lo = 1, bd = 0, hi = 0; };
    Coefs cur, tgt;
    bool fresh = true;
    float p1[2] = { 0, 0 }, p2[2] = { 0, 0 }, q1[2] = { 0, 0 }, q2[2] = { 0, 0 };      /* the pre stage pair (24 dB) and the main pair: filter_state_ of each one-pole */

    void setSampleRate(double s) { sr = s; }
    void reset() { fresh = true; for (int c = 0; c < 2; c++) p1[c] = p2[c] = q1[c] = q2[c] = 0.0f; }

    /* cutoff in Hz, res the Resonance knob 0..1, driveGain the Drive as a linear gain (1 = 0 dB, up to 31.6 = 30 dB), blend the Blend knob 0..2 (0 low-pass, 1 band-pass, 2 high-pass) */
    void set(float hz, float res, float driveGain, float blend, bool steep24)
    {
        steep = steep24;
        const float f = std::min(std::max(hz, 1.0f), (float) sr * 0.49f);
        const float sc = f / (float) sr * 3.14159265f;
        tgt.coef = std::tan(std::min(0.499f * 3.14159265f, sc / (sc + 1.0f)));
        const float rp = std::min(std::max(res, 0.0f), 1.0f), rs = std::sqrt(rp);
        const float driveDb = 20.0f * std::log10(std::max(driveGain, 1e-6f));
        tgt.reso = 2.15f * rs + (driveDb / 36.0f) * rp * 1.1f;
        const float scale = rs * rs * 2.0f + 1.0f;
        tgt.drive = driveGain / scale;
        tgt.post = 1.0f / std::sqrt(scale * tgt.drive);
        const float b = std::min(std::max(blend - 1.0f, -1.0f), 1.0f);
        tgt.bd = std::sqrt(1.0f - b * b);
        tgt.lo = b < 0.0f ? -b : 0.0f;
        tgt.hi = b >= 0.0f ? b : 0.0f;
        if (fresh) cur = tgt;
    }

    /* in and out are in the engine's level; the filter itself works at Vital's. */
    void process(float *l, float *r, int n)
    {
        const float inv = 1.0f / (float) std::max(n, 1);
        Coefs d;
        d.coef = (tgt.coef - cur.coef) * inv; d.reso = (tgt.reso - cur.reso) * inv; d.drive = (tgt.drive - cur.drive) * inv; d.post = (tgt.post - cur.post) * inv;
        d.lo = (tgt.lo - cur.lo) * inv; d.bd = (tgt.bd - cur.bd) * inv; d.hi = (tgt.hi - cur.hi) * inv;
        Coefs c = cur;
        float *io[2] = { l, r };
        for (int i = 0; i < n; i++) {
            c.coef += d.coef; c.reso += d.reso; c.drive += d.drive; c.post += d.post; c.lo += d.lo; c.bd += d.bd; c.hi += d.hi;
            const float k = c.coef, k2 = 2.0f * k, kk = k * k;
            const float reso = c.reso / std::max(1.0f, k2 * 0.09f + 0.97f);
            const float fbm = k2 - kk - 1.0f, m2 = 1.0f - k;
            const float norm = 1.0f / (reso * (kk - k) + 1.0f);
            const float preNorm = 1.0f / ((kk - k) + 1.0f);
            for (int ch = 0; ch < 2; ch++) {
                float x = io[ch][i] * (1.0f / kVitalFull);
                if (steep) {
                    const float fb = fbm * p1[ch] + m2 * p2[ch];
                    const float in1 = (x - fb) * preNorm;
                    float dd = k * (in1 - p1[ch]); p1[ch] += dd; const float s1 = p1[ch]; p1[ch] += dd;
                    dd = k * (s1 - p2[ch]); p2[ch] += dd; const float s2 = p2[ch]; p2[ch] += dd;
                    const float bp = s1 - s2, hp = in1 - s1 - bp;
                    x = c.lo * s2 + c.bd * bp + c.hi * hp;
                }
                const float fb = fbm * q1[ch] + m2 * q2[ch];
                const float in1 = vitalTanh((c.drive * x - reso * fb) * norm);
                float dd = k * (in1 - q1[ch]); q1[ch] += dd; const float s1 = q1[ch]; q1[ch] += dd;
                dd = k * (s1 - q2[ch]); q2[ch] += dd; const float s2 = q2[ch]; q2[ch] += dd;
                const float bp = s1 - s2, hp = in1 - s1 - bp;
                io[ch][i] = (c.lo * s2 + c.bd * bp + c.hi * hp) * c.post * kVitalFull;
            }
        }
        cur = tgt;
        fresh = false;
        for (int ch = 0; ch < 2; ch++) {                                   /* a state that has run down to nothing is flushed (denormals) */
            p1[ch] = std::fabs(p1[ch]) < 1e-15f ? 0.0f : p1[ch]; p2[ch] = std::fabs(p2[ch]) < 1e-15f ? 0.0f : p2[ch];
            q1[ch] = std::fabs(q1[ch]) < 1e-15f ? 0.0f : q1[ch]; q2[ch] = std::fabs(q2[ch]) < 1e-15f ? 0.0f : q2[ch];
        }
    }
};

/* Vital's Ladder model (filters/ladder_filter.cpp): four saturating one-poles in a loop with the resonance, a tanh in front, and the stage taps mixed by the style (12 or 24 dB) and the Blend knob. Same
 * wiring and level convention as VitalSK above. */
struct VitalLadder {
    static constexpr float kVitalFull = 0.33650f;
    bool on = false;
    bool steep = false;
    double sr = 44100.0;
    struct Coefs { float coef = 0, reso = 0, drive = 1, post = 1, sc[5] = { 0, 0, 0, 0, 0 }; };
    Coefs cur, tgt;
    bool fresh = true;
    float fs[2][4] = {}, sat[2][4] = {}, cs[2][4] = {};                /* each stage: filter_state_, sat_filter_state_, current_state_ */

    void setSampleRate(double s) { sr = s; }
    void reset() { fresh = true; for (int c = 0; c < 2; c++) for (int k = 0; k < 4; k++) fs[c][k] = sat[c][k] = cs[c][k] = 0.0f; }
    static float algSat(float v) { const float sq = v * v; return v - v * sq * 0.9f / (sq + 3.0f); }

    void set(float hz, float res, float driveGain, float blend, bool steep24)
    {
        steep = steep24;
        const float f = std::min(std::min(std::max(hz, 1.0f), 20000.0f), (float) sr * 0.49f);
        const float sc = f / (float) sr * 3.14159265f;
        tgt.coef = std::tan(std::min(0.499f * 3.14159265f, sc / (sc + 1.0f)));
        const float rp = std::min(std::max(res, 0.0f), 1.0f);
        const float adjust = steep24 ? std::sin(rp * (0.5f * 3.14159265f)) : rp;
        const float driveDb = 20.0f * std::log10(std::max(driveGain, 1e-6f));
        tgt.reso = 0.001f + (4.1f - 0.001f) * adjust + (driveDb / 36.0f) * rp * 5.0f;
        const float b = std::min(std::max(blend - 1.0f, -1.0f), 1.0f);
        float band = std::sqrt(1.0f - b * b);
        const float lo = b < 0.0f ? -b : 0.0f, hi = b >= 0.0f ? b : 0.0f;
        const float driveMult = (steep24 ? std::sin(rp) : rp) + 1.0f;
        const float resScale = driveMult + (1.0f - driveMult) * hi;
        tgt.drive = driveGain * resScale;
        static const float lp24[5] = { 0, 0, 0, 0, 1 }, bp24[5] = { 0, 0, -1, 2, -1 }, hp24[5] = { 1, -4, 6, -4, 1 };
        static const float lp12[5] = { 0, 0, 1, 0, 0 }, bp12[5] = { 0, 1, -1, 0, 0 }, hp12[5] = { 1, -2, 1, 0, 0 };
        if (steep24) {
            band = 1.0f - std::fabs(b);
            tgt.post = 1.0f / std::sqrt((driveGain - 1.0f) * 0.25f + 1.0f);
            for (int i = 0; i < 5; i++) tgt.sc[i] = lo * lp24[i] + band * bp24[i] + hi * hp24[i];
        } else {
            tgt.post = 1.0f / std::sqrt((driveGain - 1.0f) * 0.5f + 1.0f);
            for (int i = 0; i < 5; i++) tgt.sc[i] = lo * lp12[i] + band * bp12[i] + hi * hp12[i];
        }
        if (fresh) cur = tgt;
    }

    void process(float *l, float *r, int n)
    {
        const float inv = 1.0f / (float) std::max(n, 1);
        Coefs d;
        d.coef = (tgt.coef - cur.coef) * inv; d.reso = (tgt.reso - cur.reso) * inv; d.drive = (tgt.drive - cur.drive) * inv; d.post = (tgt.post - cur.post) * inv;
        for (int i = 0; i < 5; i++) d.sc[i] = (tgt.sc[i] - cur.sc[i]) * inv;
        Coefs c = cur;
        float *io[2] = { l, r };
        for (int i = 0; i < n; i++) {
            c.coef += d.coef; c.reso += d.reso; c.drive += d.drive; c.post += d.post;
            for (int k = 0; k < 5; k++) c.sc[k] += d.sc[k];
            const float g1 = c.coef * 1.66f, g2 = g1 * g1, g3 = g1 * g2;
            for (int ch = 0; ch < 2; ch++) {
                const float x = io[ch][i] * (1.0f / kVitalFull);
                const float st1 = sat[ch][3] + g1 * sat[ch][2];
                const float st2 = st1 + g2 * sat[ch][1];
                const float st = st2 + g3 * sat[ch][0];
                const float in = vitalTanh(x * c.drive - c.reso * st);
                float so = in;
                for (int k = 0; k < 4; k++) {
                    const float dl = c.coef * (so - sat[ch][k]);
                    fs[ch][k] += dl; cs[ch][k] = algSat(fs[ch][k]); fs[ch][k] += dl; sat[ch][k] = algSat(fs[ch][k]);
                    so = cs[ch][k];
                }
                float total = c.sc[0] * in;
                for (int k = 0; k < 4; k++) total += c.sc[k + 1] * cs[ch][k];
                io[ch][i] = total * c.post * kVitalFull;
            }
        }
        cur = tgt;
        fresh = false;
        for (int ch = 0; ch < 2; ch++) for (int k = 0; k < 4; k++) {
            if (std::fabs(fs[ch][k]) < 1e-15f) fs[ch][k] = 0.0f;
            if (std::fabs(sat[ch][k]) < 1e-15f) sat[ch][k] = 0.0f;
            if (std::fabs(cs[ch][k]) < 1e-15f) cs[ch][k] = 0.0f;
        }
    }
};

} // namespace tb
