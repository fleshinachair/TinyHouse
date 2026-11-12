/* The partitioned convolver against a direct convolution: random stereo and mono impulse responses of several lengths (shorter than the head, across the head/tail
 * boundary, many tail partitions), random input, the output compared sample by sample (the plan's output is 128 samples late). */
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include "../src/dsp/conv.h"

using namespace tb;
static int g_fail = 0;
#define CHECK(cond, ...) do { if (!(cond)) { g_fail++; printf("FAIL: " __VA_ARGS__); printf("\n"); } else { printf("  ok: " __VA_ARGS__); printf("\n"); } } while (0)

static float rnd(uint32_t &s) { s = s * 1664525u + 1013904223u; return (float) (int32_t) s / 2147483648.0f; }

static void trial(int len, int ch, float gainDb)
{
    uint32_t seed = 12345u + (uint32_t) len;
    SampleSet set;
    SampleRegion r;
    r.channels = ch; r.frames = len; r.rate = 44100.0f; r.gain = std::pow(10.0f, gainDb / 20.0f);
    r.pcm.resize((size_t) len * ch);
    std::vector<float> h[2];
    for (int c = 0; c < ch; c++) h[c].resize((size_t) len);
    for (int i = 0; i < len; i++)
        for (int c = 0; c < ch; c++) {
            const float v = rnd(seed) * std::exp(-4.0f * i / (float) len);
            const int16_t q = (int16_t) std::lround(v * 30000.0f);
            r.pcm[(size_t) i * ch + c] = q;
            h[c][(size_t) i] = q * r.gain / 32768.0f;
        }
    set.regions.push_back(std::move(r));
    auto plan = convBuild(set, 44100.0f);
    if (!plan) { CHECK(false, "plan built for %d taps", len); return; }
    const int n = len + 9000;
    std::vector<float> x[2], y[2];
    for (int c = 0; c < 2; c++) { x[c].resize((size_t) n); y[c].assign((size_t) n, 0.0f); }
    for (int i = 0; i < n; i++) { x[0][(size_t) i] = i < len + 3000 ? rnd(seed) : 0.0f; x[1][(size_t) i] = i < len + 3000 ? rnd(seed) : 0.0f; }
    std::vector<float> w[2] = { std::vector<float>((size_t) n), std::vector<float>((size_t) n) };
    for (int i = 0; i < n; i++) plan->step(x[0][(size_t) i], x[1][(size_t) i], w[0][(size_t) i], w[1][(size_t) i]);
    /* direct */
    double err = 0.0, ref = 0.0;
    for (int c = 0; c < 2; c++) {
        const std::vector<float> &hh = h[ch == 1 ? 0 : c];
        for (int i = 128; i < n; i++) {                            /* the output at i is the convolution at i - 128 */
            const int t = i - 128;
            double a = 0.0;
            const int kmax = std::min(len - 1, t);
            for (int k = 0; k <= kmax; k++) a += (double) hh[(size_t) k] * x[c][(size_t) (t - k)];
            const double d = w[c][(size_t) i] - a;
            err += d * d; ref += a * a;
        }
    }
    const double db = 10.0 * std::log10((err + 1e-30) / (ref + 1e-30));
    CHECK(db < -80.0, "%d taps, %d ch, gain %+.0f dB: error %.1f dB re the output", len, ch, gainDb, db);
}

int main()
{
    trial(300, 2, 0.0f);
    trial(4096, 2, -6.0f);
    trial(4097, 1, 0.0f);
    trial(9000, 2, 0.0f);
    trial(20000, 1, 6.0f);
    trial(60000, 2, -10.0f);
    printf("%s\n", g_fail ? "FAILED" : "all passed");
    return g_fail ? 1 : 0;
}
