/* How much of each convolution IR is inaudible tail: for every .tbs given, the length and the length that keeps all but the last -X dB of the IR's energy.
 *   convtrim_probe file.tbs ...      (zig c++ -O2 -std=c++17 -I src -I src/dsp tools/convtrim_probe.cpp -o convtrim_probe.exe) */
#include "../src/dsp/sample.h"
#include <cmath>
#include <cstdio>
using namespace tb;
int main(int argc, char **argv)
{
    printf("%-28s %7s %7s | kept samples at -20 -30 -40 -50 -60 dB of tail energy (s)\n", "file", "sec", "ch");
    for (int a = 1; a < argc; a++) {
        SampleSet s;
        if (!sampleLoad(argv[a], s) || s.regions.empty()) continue;
        const SampleRegion &r = s.regions[0];
        if (r.frames < 4096) continue;
        const int ch = r.channels == 2 ? 2 : 1;
        std::vector<double> e((size_t) r.frames);
        double tot = 0;
        for (int i = 0; i < r.frames; i++) { double v = 0; for (int c = 0; c < ch; c++) { const double x = r.pcm[(size_t) i * r.channels + c]; v += x * x; } e[(size_t) i] = v; tot += v; }
        const char *base = strrchr(argv[a], '/'); base = base ? base + 1 : argv[a];
        printf("%-28s %7.2f %7d |", base, r.frames / (double) r.rate, ch);
        for (double db : { -20.0, -30.0, -40.0, -50.0, -60.0 }) {
            const double lim = tot * std::pow(10.0, db / 10.0);
            double acc = 0; int keep = r.frames;
            for (int i = r.frames - 1; i >= 0; i--) { acc += e[(size_t) i]; if (acc > lim) { keep = i + 1; break; } }
            printf(" %5.2f", keep / (double) r.rate);
        }
        printf("\n");
    }
}
