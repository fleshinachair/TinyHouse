/* The module's two nonlinear stages: filter drive and the master soft clipper.
 * Both run per sample on the audio callback, so both use a rational tanh and not
 * std::tanh. Neither touches a signal it has no business touching: drive at 0 is a
 * bypass and the clipper is exactly linear below its knee, so a patch that uses
 * neither is bit-identical to one built without them.
 */
#pragma once

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
