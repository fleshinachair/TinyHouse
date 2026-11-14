/* Native test of src/dsp/granular.h against the laws measured on Serum 2 (docs/GRANULAR_PROBE_NOTES.md). It mirrors the Serum probes: a click file read by grains shows the
 * grain starts and the window height directly. Build: scripts/zig-build.sh native (test_granular.exe). */
#include <chrono>
#include <cmath>
#include <cstdio>
#include <vector>

#include "../src/dsp/granular.h"

using namespace tb;

static int g_fail = 0;
#define CHECK(cond, ...) do { if (!(cond)) { g_fail++; printf("FAIL: " __VA_ARGS__); printf("\n"); } else { printf("  ok: " __VA_ARGS__); printf("\n"); } } while (0)

static const float SR = 44100.0f;

static std::shared_ptr<SampleSet> makeSet(const std::vector<float> &x, int channels = 1)
{
    auto set = std::make_shared<SampleSet>();
    SampleRegion r;
    r.root = 60; r.rate = 44100.0f; r.channels = channels; r.frames = (int) (x.size() / channels);
    for (float v : x) r.pcm.push_back((int16_t) std::lround(v * 32767.0f));
    set->regions.push_back(std::move(r));
    return set;
}

static std::vector<float> clickFile(bool negative)
{
    std::vector<float> x(8 * 44100, 0.0f);
    x[(size_t) (0.05 * 44100)] = 0.9f;
    if (negative) x[(size_t) (0.15 * 44100)] = -0.9f;
    return x;
}

static std::vector<float> toneFile()
{
    std::vector<float> x(8 * 44100);
    for (size_t i = 0; i < x.size(); i++) x[i] = 0.5f * (float) std::sin(2.0 * M_PI * 1000.0 * (double) i / 44100.0);
    return x;
}

static std::vector<float> render(GrainCloud &c, const GrainParams &gp, double seconds, float semis = 0.0f, float gain = 1.0f)
{
    std::vector<float> out((size_t) (seconds * SR)), r(out.size());
    for (size_t i = 0; i < out.size(); i += 128) {
        const int n = (int) std::min<size_t>(128, out.size() - i);
        c.processAdding(gp, semis, gain, SR, out.data() + i, r.data() + i, n);
    }
    return out;
}

/* Local extremes above a fraction of the peak, at least 1 ms apart (the same detector as the Serum probe). */
static std::vector<std::pair<int, float>> events(const std::vector<float> &y, float thr = 0.05f)
{
    float mx = 0.0f;
    for (float v : y) mx = std::max(mx, std::fabs(v));
    std::vector<std::pair<int, float>> out;
    for (size_t i = 1; i + 1 < y.size(); i++) {
        const float a = std::fabs(y[i]);
        if (a > thr * mx && a >= std::fabs(y[i - 1]) && a >= std::fabs(y[i + 1])) {
            if (!out.empty() && (int) i - out.back().first < 44) { if (a > std::fabs(out.back().second)) out.back() = { (int) i, y[i] }; }
            else out.push_back({ (int) i, y[i] });
        }
    }
    return out;
}

static void testGrid()
{
    printf("== grain grid, window and gain (click file, Scan 0) ==\n");
    auto two = makeSet(clickFile(true)), one = makeSet(clickFile(false));
    GrainParams gp; gp.scanPct = 0.0f;
    /* Density 1: N = 5.336, T = 0.5 s */
    gp.kd = 5.336f / 30.0f; gp.lenSeconds = 0.5f;
    GrainCloud c; c.start(one, 60);
    auto y = render(c, gp, 4.0);
    auto ev = events(y);
    std::vector<int> pos;
    for (auto &e : ev) if (e.first > 22050 && e.second > 0) pos.push_back(e.first);
    const double want = 0.5 / 5.336 * SR;
    double worst = 0.0;
    for (size_t i = 1; i < pos.size(); i++) worst = std::max(worst, std::fabs((double) (pos[i] - pos[i - 1]) - want));
    CHECK(pos.size() > 20 && worst < 2.0, "grains start every T/N: %zu pulses, spacing %.2f samples, worst deviation %.2f", pos.size(), want, worst);
    CHECK(std::fabs((double) (pos.back() - pos.front()) / (double) (pos.size() - 1) - want) < 0.05, "mean spacing %.3f vs %.3f samples", (double) (pos.back() - pos.front()) / (double) (pos.size() - 1), want);

    /* window: with N 0.95 grains do not overlap; the - pulse at age 150 ms over the + pulse at 50 ms is the window ratio */
    gp.kd = 0.95f / 30.0f; gp.lenSeconds = 0.5f;
    c.start(two, 60);
    y = render(c, gp, 4.0);
    ev = events(y, 0.02f);
    double pp = 0.0, nn = 0.0; int np = 0, nnn = 0;
    for (auto &e : ev) { if (e.first < 22050) continue; if (e.second > 0) { pp += e.second; np++; } else { nn += -e.second; nnn++; } }
    const double ratio = (nn / nnn) / (pp / np), wantR = std::pow(std::sin(M_PI * 0.15 / 0.5), 2) / std::pow(std::sin(M_PI * 0.05 / 0.5), 2);
    CHECK(np > 2 && std::fabs(ratio / wantR - 1.0) < 0.01, "Hann window: w(150ms)/w(50ms) = %.4f, expected %.4f", ratio, wantR);

    /* gain table: the + pulse of N in M=2 over M=1 */
    auto amp = [&](float N) {
        GrainParams g2 = gp; g2.kd = N / 30.0f;
        GrainCloud cc; cc.start(one, 60);
        auto yy = render(cc, g2, 4.0);
        double s = 0.0; int k = 0;
        for (auto &e : events(yy, 0.01f)) if (e.first > 22050) { s += std::fabs(e.second); k++; }
        return s / k;
    };
    const double a1 = amp(0.95f);
    for (int M : { 2, 8, 20, 31 }) {
        const double got = amp((float) M - 0.5f) / a1;
        CHECK(std::fabs(got / kGrainGain[M] - 1.0) < 0.002, "gain table M=%d: pulse %.4f vs %.4f", M, got, kGrainGain[M]);
    }
    CHECK(std::fabs(kGrainGain[31] / kGrainGain[30] - 0.9890) < 0.001, "table steps (M31/M30 %.4f)", kGrainGain[31] / kGrainGain[30]);
}

static void testNoteOn()
{
    printf("== note-on: grid starts at the note, pre-note grains only inside the file ==\n");
    auto tone = makeSet(toneFile());
    GrainParams gp; gp.kd = 5.336f / 30.0f; gp.lenSeconds = 1.0f;
    auto rms = [](const std::vector<float> &y, double a, double b) {
        double s = 0.0; int k = 0;
        for (size_t i = (size_t) (a * SR); i < (size_t) (b * SR) && i < y.size(); i++) { s += (double) y[i] * y[i]; k++; }
        return std::sqrt(s / std::max(k, 1));
    };
    GrainCloud c;
    gp.scanPct = 100.0f; gp.start = 0.0f;
    c.start(tone, 60); auto y1 = render(c, gp, 3.0);
    const double late1 = rms(y1, 2.0, 2.5), early1 = rms(y1, 0.0, 0.01);
    CHECK(early1 < 0.01 * late1, "Scan 100, Start 0: silent at note-on (%.5f vs steady %.4f), ramps up", early1, late1);
    CHECK(rms(y1, 0.25, 0.3) < 0.5 * late1 && rms(y1, 0.9, 1.0) > 0.8 * late1, "ramp over about one grain length (0.25 s: %.3f, 0.95 s: %.3f of steady)", rms(y1, 0.25, 0.3) / late1, rms(y1, 0.9, 1.0) / late1);
    gp.start = 0.25f;                                       /* 2 s into the file: every pre-note grain of the last second starts inside it */
    c.start(tone, 60); auto y2 = render(c, gp, 3.0);
    CHECK(rms(y2, 0.0, 0.01) > 0.9 * rms(y2, 2.0, 2.5), "Scan 100, Start 25 %%: full level at note-on (%.4f vs %.4f)", rms(y2, 0.0, 0.01), rms(y2, 2.0, 2.5));
    gp.start = 0.0f; gp.scanPct = 0.0f;
    c.start(tone, 60); auto y3 = render(c, gp, 3.0);
    CHECK(rms(y3, 0.0, 0.01) > 0.3 * rms(y3, 2.0, 2.5), "Scan 0: grains already sounding at note-on (%.5f vs %.5f)", rms(y3, 0.0, 0.01), rms(y3, 2.0, 2.5));
}

static void testWindows()
{
    printf("== window shapes against the Serum measurements ==\n");
    auto near = [](float got, float want, float tol) { return std::fabs(got - want) < tol; };
    CHECK(near(grainWindow(kWinTukey, 100, 0, 0.25f), 0.5f, 1e-4f) && near(grainWindow(kWinTukey, 100, 0, 0.1f), 0.0955f, 2e-3f), "Tukey 100 = Hann");
    CHECK(near(grainWindow(kWinTukey, 50, 0, 0.1f), 0.345f, 3e-3f) && near(grainWindow(kWinTukey, 50, 0, 0.3f), 1.0f, 1e-4f), "Tukey 50 (taper 0.25)");
    CHECK(near(grainWindow(kWinTukey, 50, -50, 0.1f), 0.905f, 3e-3f), "Tukey 50 skew -50 (0.1 -> 0.905)");
    CHECK(near(grainWindow(kWinTriangle, 100, 0, 0.1f), 0.2f, 1e-4f) && near(grainWindow(kWinTriangle, 50, 0, 0.1f), 0.6f, 1e-4f), "Triangle 100 / 50");
    CHECK(near(grainWindow(kWinTriangle, 100, -50, 0.4f), 0.8f, 1e-3f) && near(grainWindow(kWinTriangle, 100, -50, 0.1f), 0.4f, 1e-3f), "Triangle skew -50");
    CHECK(near(grainWindow(kWinBlackmanHarris, 100, 0, 0.1f), 0.011f, 1e-3f) && near(grainWindow(kWinBlackmanHarris, 50, 0, 0.3f), 0.695f, 3e-3f), "Blackman-Harris");
    CHECK(near(grainWindow(kWinGaussian, 50, 0, 0.4f), 0.0067f, 1e-3f) && near(grainWindow(kWinGaussian, 50, 0, 0.5f), 1.0f, 1e-4f), "Gaussian 50");
    CHECK(near(grainWindow(kWinExpDec, 50, 0, 0.1f), 0.082f, 2e-3f) && near(grainWindow(kWinExpDec, 20, 0, 0.25f), std::exp(-1.0f), 1e-3f), "ExpDec 50 / 20");
    CHECK(near(grainWindow(kWinExpDec, 50, 80, 0.1f), 0.082f, 2e-3f), "ExpDec ignores skew");
}

/* Settings that change during a note (mod routes): the scan moves at the new speed from where it is, a Density raised from almost nothing starts grains at once,
 * and the slot level acts at once. */
static void testModulated()
{
    printf("== settings changed during a note ==\n");
    std::vector<float> ramp(8 * 44100);                     /* the sample value says where in the file a grain reads: value = position / 8 s */
    for (size_t i = 0; i < ramp.size(); i++) ramp[i] = 0.9f * (float) i / (float) ramp.size();
    auto set = makeSet(ramp);
    auto runSplit = [&](GrainParams a, GrainParams b, double tSwitch, double total, float gainA = 1.0f, float gainB = 1.0f) {
        GrainCloud c; c.start(set, 60);
        std::vector<float> out((size_t) (total * SR)), r(out.size());
        for (size_t i = 0; i < out.size(); i += 128) {
            const bool second = (double) i / SR >= tSwitch;
            c.processAdding(second ? b : a, 0.0f, second ? gainB : gainA, SR, out.data() + i, r.data() + i, (int) std::min<size_t>(128, out.size() - i));
        }
        return out;
    };
    GrainParams a; a.kd = 0.95f / 30.0f; a.lenSeconds = 0.1f; a.scanPct = 100.0f;      /* one grain at a time, 0.1 s long */
    GrainParams b = a; b.scanPct = 0.0f;
    auto y = runSplit(a, b, 1.0, 2.0);
    /* the grain peak (window 1 at its centre) is the ramp value at its read position: after the switch the scan holds near 1 s, so about 0.9 / 8 = 0.11 */
    float late = 0.0f;
    for (size_t i = (size_t) (1.5 * SR); i < y.size(); i++) late = std::max(late, std::fabs(y[i]));
    const float wantV = 0.9f / 8.0f * 32767.0f / 32768.0f;
    CHECK(std::fabs(late / wantV - 1.0f) < 0.08f, "scan 100 %% then 0 at 1 s: later grains read near 1 s (peak %.4f, expected about %.4f)", late, wantV);

    GrainParams d1 = a; d1.kd = 0.001f; d1.scanPct = 0.0f;   /* N 0.03: the next grain would be 3.3 s away */
    GrainParams d2 = d1; d2.kd = 0.5f;                        /* N 15: a grain every 6.7 ms */
    y = runSplit(d1, d2, 1.0, 1.3);
    int first = -1;
    for (size_t i = (size_t) (1.0 * SR); i < y.size(); i++) if (std::fabs(y[i]) > 1e-4f) { first = (int) i; break; }
    CHECK(first >= 0 && (first - 44100) < (int) (0.02 * SR), "Density raised at 1 s: grains within %.1f ms", first < 0 ? -1.0 : (first - 44100) * 1000.0 / SR);

    GrainParams g = a; g.scanPct = 0.0f; g.kd = 0.5f;
    y = runSplit(g, g, 1.0, 1.2, 1.0f, 0.0f);
    float after = 0.0f;
    for (size_t i = (size_t) (1.0 * SR) + 128; i < y.size(); i++) after = std::max(after, std::fabs(y[i]));
    CHECK(after == 0.0f, "slot level to 0 at 1 s: silent from the next block (max %.6f)", after);
}

static void benchmark()
{
    printf("== CPU (this machine; the Move is slower, run tablor_cli there) ==\n");
    std::vector<float> st(8 * 44100 * 2);
    for (size_t i = 0; i < st.size(); i++) st[i] = 0.3f * (float) std::sin(0.01 * (double) i);
    auto set = makeSet(st, 2);
    GrainParams gp; gp.kd = 0.9457f; gp.lenSeconds = 1.0f; gp.scanPct = 100.0f;
    GrainCloud clouds[8];
    for (int v = 0; v < 8; v++) clouds[v].start(set, 60 + v);
    std::vector<float> l(128), r(128);
    const int blocks = (int) (SR / 128.0f) * 6;
    int live = 0;
    const auto t0 = std::chrono::steady_clock::now();
    for (int b = 0; b < blocks; b++)
        for (int v = 0; v < 8; v++) {
            std::fill(l.begin(), l.end(), 0.0f); std::fill(r.begin(), r.end(), 0.0f);
            clouds[v].processAdding(gp, (float) v, 1.0f, SR, l.data(), r.data(), 128);
            if (b == blocks - 1) live += clouds[v].liveGrains();
        }
    const double sec = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    printf("  8 voices x Density 800 (%d grains live), 6 s of audio in %.3f s = %.1f %% of one core here\n", live, sec, 100.0 * sec / 6.0);
}

/* A ramp file (value = position in the file, 0..0.9 over 8 s): the first samples of a grain say where it started reading. One grain at a time (N 0.949), T = 0.1 s, rectangular window. */
static std::vector<float> rampFile()
{
    std::vector<float> x(8 * 44100);
    for (size_t i = 0; i < x.size(); i++) x[i] = 0.9f * (float) i / (float) x.size();
    return x;
}

static GrainParams oneAtATime()
{
    GrainParams gp;
    gp.kd = 0.9487f / 30.0f; gp.lenSeconds = 0.1f; gp.winShape = kWinTukey; gp.winParam = 0.0f;
    return gp;
}

/* Where each grain on the grid starts reading (seconds into the file), from its first samples; -1 where the grain is absent. */
static std::vector<double> startPositions(const std::vector<float> &y, double T)
{
    const double spacing = T / 0.9487 * SR;
    std::vector<double> pos;
    for (int k = 0; (k + 1) * spacing + 8 < (double) y.size(); k++) {
        const int i = (int) std::lround(k * spacing) + 3;
        pos.push_back(std::fabs(y[(size_t) i]) < 1e-4f ? -1.0 : std::fabs(y[(size_t) i]) / 0.9 * 8.0);
    }
    return pos;
}

static void testScanRangeAndLoops()
{
    printf("== scan range, loops, manual position, Random settings (ramp file) ==\n");
    auto ramp = makeSet(rampFile());
    const double SP = 0.1 / 0.9487;
    auto at = [&](const std::vector<double> &p, double t) { return p[(size_t) std::lround(t / SP)]; };
    auto tk = [&](double t) { return (double) std::lround(t / SP) * SP; };     /* the birth time on the grid nearest t */
    {   /* End 50 %: the scan stops at 4 s */
        GrainParams gp = oneAtATime(); gp.scanPct = 100.0f; gp.end = 0.5f;
        GrainCloud c; c.start(ramp, 60);
        auto p = startPositions(render(c, gp, 7.0), 0.1);
        CHECK(std::fabs(at(p, 2.0) - tk(2.0)) < 0.02 && at(p, 5.0) < 0.0, "End 50 %%: at 2 s the grain reads its time (%.3f of %.3f), at 5 s there is none (%.3f)", at(p, 2.0), tk(2.0), at(p, 5.0));
    }
    {   /* a negative rate starts at End and stops at Start */
        GrainParams gp = oneAtATime(); gp.scanPct = -100.0f; gp.start = 0.25f;
        GrainCloud c; c.start(ramp, 60);
        auto p = startPositions(render(c, gp, 7.0), 0.1);
        CHECK(std::fabs(at(p, 1.0) - (8.0 - tk(1.0))) < 0.03 && at(p, 5.0) > 2.9 && at(p, 6.5) < 0.0, "scan -100 %%: at 1 s the grain reads 8 s minus its time (%.3f), still at 5 s (%.3f), stopped at Start (2 s) by 6.5 s (%.3f)", at(p, 1.0), at(p, 5.0), at(p, 6.5));
    }
    {   /* Forward loop 25..50 %, Start 25 % */
        GrainParams gp = oneAtATime(); gp.scanPct = 100.0f; gp.start = 0.25f; gp.loopMode = kLoopForward; gp.loopStart = 0.25f; gp.loopEnd = 0.5f;
        GrainCloud c; c.start(ramp, 60);
        auto p = startPositions(render(c, gp, 7.0), 0.1);
        CHECK(std::fabs(at(p, 1.0) - (2.0 + tk(1.0))) < 0.03 && std::fabs(at(p, 2.2) - tk(2.2)) < 0.05 && std::fabs(at(p, 6.3) - (2.0 + std::fmod(tk(6.3), 2.0))) < 0.05, "Forward loop 2..4 s: %.3f at 1 s, wrapped to %.3f at 2.2 s, %.3f at 6.3 s (the loop position of the time)", at(p, 1.0), at(p, 2.2), at(p, 6.3));
    }
    {   /* PingPong loop 25..50 %, Start 0: runs to 4 s, then back to 2 s, then forward */
        GrainParams gp = oneAtATime(); gp.scanPct = 100.0f; gp.loopMode = kLoopPingPong; gp.loopStart = 0.25f; gp.loopEnd = 0.5f;
        GrainCloud c; c.start(ramp, 60);
        auto p = startPositions(render(c, gp, 8.0), 0.1);
        CHECK(std::fabs(at(p, 3.0) - tk(3.0)) < 0.03 && std::fabs(at(p, 4.5) - (4.0 - (tk(4.5) - 4.0))) < 0.05 && std::fabs(at(p, 6.4) - (2.0 + tk(6.4) - 6.0)) < 0.05, "PingPong loop: %.3f at 3 s, back at %.3f at 4.5 s, forward again %.3f at 6.4 s", at(p, 3.0), at(p, 4.5), at(p, 6.4));
    }
    {   /* Start past the loop end: no loop, it plays straight to the file end */
        GrainParams gp = oneAtATime(); gp.scanPct = 100.0f; gp.start = 0.6f; gp.loopMode = kLoopForward; gp.loopStart = 0.25f; gp.loopEnd = 0.5f;
        GrainCloud c; c.start(ramp, 60);
        auto p = startPositions(render(c, gp, 5.0), 0.1);
        CHECK(std::fabs(at(p, 1.0) - (4.8 + tk(1.0))) < 0.03 && at(p, 4.0) < 0.0, "Start past the loop end plays on to the end: %.3f at 1 s, none at 4 s (%.3f)", at(p, 1.0), at(p, 4.0));
    }
    {   /* manual position */
        GrainParams gp = oneAtATime(); gp.scanPct = 100.0f; gp.start = 0.25f; gp.manual = true; gp.position = 0.5f;
        GrainCloud c; c.start(ramp, 60);
        auto p = startPositions(render(c, gp, 5.0), 0.1);
        CHECK(std::fabs(at(p, 0.5) - 4.0) < 0.03 && std::fabs(at(p, 4.0) - 4.0) < 0.03, "Manual Position 50 %%: every grain reads 4.0 s (%.3f, %.3f)", at(p, 0.5), at(p, 4.0));
    }
    {   /* Random Offset: v = 1 spreads the start by up to +-0.15 of the file; Random Gain only turns grains down; Random Direction reverses a share of the grains */
        GrainParams gp = oneAtATime(); gp.scanPct = 0.0f; gp.start = 0.5f;
        GrainCloud c; c.start(ramp, 60);
        auto base = startPositions(render(c, gp, 6.0), 0.1);
        gp.rOffset = 1.0f; GrainCloud c2; c2.start(ramp, 60);
        auto ro = startPositions(render(c2, gp, 6.0), 0.1);
        double lo = 99, hi = -99, lo0 = 99, hi0 = -99;
        for (double v : ro) if (v >= 0) { lo = std::min(lo, v); hi = std::max(hi, v); }
        for (double v : base) if (v >= 0) { lo0 = std::min(lo0, v); hi0 = std::max(hi0, v); }
        CHECK(hi0 - lo0 < 0.01 && hi - lo > 1.8 && lo > 4.0 - 1.25 && hi < 4.0 + 1.25, "Random Offset 100 %%: the start spreads over %.2f..%.2f s (none: %.3f..%.3f), within +-1.2 s", lo, hi, lo0, hi0);
        gp.rOffset = 0.0f; gp.rGain = 0.5f; GrainCloud c3; c3.start(ramp, 60);
        auto rg = startPositions(render(c3, gp, 6.0), 0.1);
        double mn = 99, mx = 0; for (double v : rg) if (v >= 0) { mn = std::min(mn, v); mx = std::max(mx, v); }
        CHECK(mx <= 4.0 * 1.005 && mn >= 4.0 * 0.5 * 0.99 && mn < 4.0 * 0.7, "Random Gain 50 %%: grain levels between 0.5 and 1 of the plain (%.3f..%.3f of 4.0)", mn, mx);
        gp.rGain = 0.0f; gp.rDir = 1.0f; GrainCloud c4; c4.start(ramp, 60);
        auto y = render(c4, gp, 3.0);
        int fwd = 0, back = 0; const double spacing = 0.1 / 0.9487 * SR;
        for (int k = 1; k < 25; k++) { const int i = (int) std::lround(k * spacing) + 3; (y[(size_t) i + 40] > y[(size_t) i] ? fwd : back)++; }
        CHECK(fwd == 0 && back == 24, "Random Direction 100 %%: every grain reads backwards (%d forward, %d backwards)", fwd, back);
        gp.rDir = 0.5f; GrainCloud c5; c5.start(ramp, 60);
        y = render(c5, gp, 12.0); fwd = back = 0;
        for (int k = 1; k < 110; k++) { const int i = (int) std::lround(k * spacing) + 3; (y[(size_t) i + 40] > y[(size_t) i] ? fwd : back)++; }
        CHECK(fwd > 35 && back > 35, "Random Direction 50 %%: about half reversed (%d forward, %d backwards)", fwd, back);
    }
    {   /* Random Pan: the sides differ, the louder side up to 1.65 of the centre, the quieter down towards 0 */
        GrainParams gp = oneAtATime(); gp.scanPct = 0.0f; gp.start = 0.5f; gp.rPan = 1.0f;
        std::vector<float> l(6 * 44100), r(l.size());
        GrainCloud c; c.start(ramp, 60);
        for (size_t i = 0; i < l.size(); i += 128) c.processAdding(gp, 0.0f, 1.0f, SR, l.data() + i, r.data() + i, (int) std::min<size_t>(128, l.size() - i));
        const double spacing = 0.1 / 0.9487 * SR;
        double minx = 9, maxx = -9, maxs = 0, mins = 9;
        for (int k = 1; k < 55; k++) { const int i = (int) std::lround(k * spacing) + 3; const double L = l[(size_t) i], R = r[(size_t) i], x = (R - L) / (R + L); minx = std::min(minx, x); maxx = std::max(maxx, x); maxs = std::max(maxs, std::max(L, R) / 0.45); mins = std::min(mins, std::min(L, R) / 0.45); }
        CHECK(minx < -0.7 && maxx > 0.7 && maxs > 1.4 && maxs < 1.66 && mins < 0.35, "Random Pan 100 %%: pan x spreads %.2f..%.2f, louder side up to %.2f, quieter side down to %.2f of the centre level", minx, maxx, maxs, mins);
        float gl, gr; grainPanGains(1.0f, gl, gr);
        CHECK(std::fabs(gl - 0.0f) < 0.01 && std::fabs(gr - 1.654f) < 0.01, "pan law at the extreme: L %.3f R %.3f (measured 0 and 1.654)", gl, gr);
    }
}

int main()
{
    testGrid();
    testNoteOn();
    testWindows();
    testModulated();
    testScanRangeAndLoops();
    benchmark();
    printf(g_fail ? "\n%d FAILED\n" : "\nall passed\n", g_fail);
    return g_fail ? 1 : 0;
}
