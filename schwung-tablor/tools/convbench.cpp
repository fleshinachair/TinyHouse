/* The convolver's cost per 128-sample engine block, for an IR of a given length (seconds) and channel count: mean and worst block in microseconds.
 *   convbench [seconds=2.0] [channels=2] */
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include "../src/dsp/conv.h"

int main(int argc, char **argv)
{
    const double secs = argc > 1 ? atof(argv[1]) : 2.0;
    const int ch = argc > 2 ? atoi(argv[2]) : 2;
    const int len = (int) (secs * 44100.0);
    tb::SampleSet set;
    tb::SampleRegion r;
    r.channels = ch; r.frames = len; r.rate = 44100.0f;
    r.pcm.resize((size_t) len * ch);
    uint32_t s = 1;
    for (auto &v : r.pcm) { s = s * 1664525u + 1013904223u; v = (int16_t) ((int32_t) s >> 18); }
    set.regions.push_back(std::move(r));
    auto plan = tb::convBuild(set, 44100.0f);
    const int blocks = 44100 * 20 / 128;
    double total = 0.0, worst = 0.0;
    float acc = 0.0f;
    for (int b = 0; b < blocks; b++) {
        const auto t0 = std::chrono::steady_clock::now();
        for (int i = 0; i < 128; i++) { float wl, wr; plan->step(0.001f * (float) (i - 64), 0.0005f * (float) (i - 32), wl, wr); acc += wl + wr; }
        const double us = std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - t0).count();
        total += us; if (us > worst) worst = us;
    }
    printf("%.1f s IR, %d ch: mean %.1f us/block, worst %.1f us/block (budget 2900 us; checksum %g)\n", secs, ch, total / blocks, worst, acc);
    return 0;
}
