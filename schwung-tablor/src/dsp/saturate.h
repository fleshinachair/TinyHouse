/* The module's two nonlinear stages: filter drive and the master soft clipper.
 * Both run per sample on the audio callback, so both use a rational tanh and not
 * std::tanh. Neither touches a signal it has no business touching: drive at 0 is a
 * bypass and the clipper is exactly linear below its knee, so a patch that uses
 * neither is bit-identical to one built without them.
 */
#pragma once

#include <algorithm>
#include <cmath>

namespace tb {

/* Pade tanh. Slope 1 at 0, and it reaches exactly +/-1 with slope 0 at +/-3, so
 * clamping there adds no corner. */
inline float fastTanh(float x)
{
    if (x >= 3.0f) return 1.0f;
    if (x <= -3.0f) return -1.0f;
    const float x2 = x * x;
    return x * (27.0f + x2) / (27.0f + 9.0f * x2);
}

/* Vital's filter drive (filters/sallen_key_filter.cpp setupFilter; calibration/vital_vfilter_probe.py --drive). The input, in Vital's own full scale, goes through tanh(drive x a) with a = 1 / (1 + 2 resonance) and the
 * output is taken down by 1 / sqrt(drive); written as one stage ahead of the (linear) filter and scaled so that a drive of 0 dB is the signal as it was. The plugin's passband lands 4 to 7 dB under that stage's by 20 dB of
 * drive, so a trim of 0.3 dB per dB is applied, held from 20 dB up. g is the drive as a gain, ramped from g0 to gT across the block. */
inline void vitalFilterDriveIn(float *l, float *r, int n, float g0, float gT, float a, float passGain = 1.0f)
{
    constexpr float kVitalFull = 0.46345f;                    /* 10^(-6.7 / 20): the engine's level against Vital's */
    auto trim = [](float gain) { return std::pow(std::min(gain, 10.0f), -0.3f); };
    const float gStep = (gT - g0) / (float) n;
    float g = g0, t = trim(g0);
    const float tStep = (trim(gT) - t) / (float) n;
    for (int i = 0; i < n; i++) {
        const float k = g * a / kVitalFull;
        const float post = kVitalFull / (a * std::sqrt(g)) * t * passGain;
        t += tStep;
        l[i] = post * fastTanh(k * l[i]);
        r[i] = post * fastTanh(k * r[i]);
        g += gStep;
    }
}

/* With Vital's drive the saturator sits inside the filter's feedback, so the resonant peak shrinks as the drive rises (measured through the plugin: the peak is about 8 dB lower by 18 dB of drive). drive01 is the
 * drive as a fraction of its 30 dB. */
inline float vitalFilterDriveQ(float q, float drive01)
{
    const float s = std::min(1.0f, drive01 * 30.0f / 18.0f);
    return std::max(0.5f, q * (1.0f - 0.65f * s));
}

/* Linear to the knee, then a tanh shoulder that meets it with matching slope and
 * can never pass full scale. A hard clamp at the output turned loud chords into
 * clipping, and the only way to avoid that was turning whole presets down.
 *
 * This is a clipper, not a limiter: there is no gain reduction, so it shapes what
 * it is given. The shoulder runs out at an input of 1.6 (about +4 dB over full
 * scale), and anything louder than that is flat at the top. */
inline constexpr float kLimiterKnee = 0.7f;

inline float softLimit(float x)
{
    const float a = std::fabs(x);
    if (a <= kLimiterKnee) return x;
    const float y = kLimiterKnee + (1.0f - kLimiterKnee) * fastTanh((a - kLimiterKnee) / (1.0f - kLimiterKnee));
    return x < 0.0f ? -y : y;
}

} // namespace tb
