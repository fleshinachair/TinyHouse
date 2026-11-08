/* The master FX block: width, delay, reverb and the four-band EQ. Like the other tests these
 * assert measured behaviour, not that the code runs: where a number comes from Serum
 * (calibration/fx_probe.py) the test says so.
 */

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#include "../src/dsp/engine.h"
#include "../src/dsp/fx.h"

using namespace tb;

static int g_fail = 0;
#define CHECK(cond, ...) do { \
    if (!(cond)) { g_fail++; printf("FAIL: " __VA_ARGS__); printf("\n"); } \
    else         {           printf("  ok: " __VA_ARGS__); printf("\n"); } \
} while (0)

static const float SR = 44100.0f;

/* Deterministic white noise in -1..1 */
static float rnd(uint32_t &s)
{
    s = s * 1664525u + 1013904223u;
    return (float) (int32_t) s / 2147483648.0f;
}

/* Run n samples of a mono source through the block in 128-sample blocks. */
static void run(Fx &fx, std::vector<float> &L, std::vector<float> &R)
{
    for (size_t i = 0; i < L.size(); i += 128) {
        const int n = (int) std::min<size_t>(128, L.size() - i);
        fx.process(L.data() + i, R.data() + i, n);
    }
}

/* Steady-state gain of one sine through the block, in dB. */
static double sineGainDb(const FxSettings &s, double hz)
{
    Fx fx(SR);
    fx.set(s);
    const int n = (int) SR;
    std::vector<float> L((size_t) n), R((size_t) n);
    for (int i = 0; i < n; i++) L[(size_t) i] = R[(size_t) i] = (float) (0.1 * std::sin(6.283185307 * hz * i / SR));
    run(fx, L, R);
    double a = 0, b = 0;
    for (int i = n / 2; i < n; i++) { a += 0.01 * 0.5; b += (double) L[(size_t) i] * L[(size_t) i]; }
    return 10.0 * std::log10(b / a);
}

static FxSettings band(int type, float hz, float gainDb, float q)
{
    FxSettings s;
    s.band[0].type = type; s.band[0].hz = hz; s.band[0].gainDb = gainDb; s.band[0].q = q;
    return s;
}

static void testBypass()
{
    printf("== bypass ==\n");
    Fx fx(SR);
    fx.set(FxSettings());
    CHECK(!fx.active(), "defaults: nothing to do, so the engine skips the block");
    std::vector<float> L(512), R(512);
    uint32_t s = 7;
    for (size_t i = 0; i < L.size(); i++) { L[i] = rnd(s); R[i] = rnd(s); }
    auto L0 = L, R0 = R;
    run(fx, L, R);
    CHECK(L == L0 && R == R0, "and the signal is untouched, bit for bit");
}

static void testEq()
{
    printf("== EQ ==\n");
    const double bell = sineGainDb(band(EQ_BELL, 1000, 12, 1.25f), 1000);
    CHECK(std::fabs(bell - 12.0) < 0.2, "a bell is its set gain at its centre (%.2f dB)", bell);
    const double bellFar = sineGainDb(band(EQ_BELL, 1000, 12, 1.25f), 100);
    CHECK(std::fabs(bellFar) < 0.5, "and leaves two octaves out alone (%.2f dB)", bellFar);
    const double bellCut = sineGainDb(band(EQ_BELL, 1000, -12, 1.25f), 1000);
    CHECK(std::fabs(bellCut + 12.0) < 0.2, "a negative bell cuts (%.2f dB)", bellCut);

    const double loLow = sineGainDb(band(EQ_LOSHELF, 1000, 10, 0.64f), 50);
    const double loHigh = sineGainDb(band(EQ_LOSHELF, 1000, 10, 0.64f), 10000);
    CHECK(std::fabs(loLow - 10.0) < 0.5 && std::fabs(loHigh) < 0.5, "a low shelf lifts below and not above (%.1f / %.1f dB)", loLow, loHigh);
    const double hiLow = sineGainDb(band(EQ_HISHELF, 1000, 10, 0.64f), 50);
    const double hiHigh = sineGainDb(band(EQ_HISHELF, 1000, 10, 0.64f), 10000);
    CHECK(std::fabs(hiHigh - 10.0) < 0.5 && std::fabs(hiLow) < 0.5, "a high shelf lifts above and not below (%.1f / %.1f dB)", hiHigh, hiLow);

    const double hpStop = sineGainDb(band(EQ_HIPASS, 1000, 0, 0.71f), 125);
    const double hpPass = sineGainDb(band(EQ_HIPASS, 1000, 0, 0.71f), 8000);
    CHECK(hpStop < -30.0 && std::fabs(hpPass) < 0.5, "a high-pass is 12 dB per octave (%.1f dB three octaves down)", hpStop);
    const double lpStop = sineGainDb(band(EQ_LOPASS, 1000, 0, 0.71f), 8000);
    const double lpPass = sineGainDb(band(EQ_LOPASS, 1000, 0, 0.71f), 125);
    CHECK(lpStop < -30.0 && std::fabs(lpPass) < 0.5, "and so is a low-pass (%.1f dB)", lpStop);
    const double lpQ = sineGainDb(band(EQ_LOPASS, 1000, 0, 4.0f), 1000);
    CHECK(lpQ > 10.0, "a resonant cut rings at its corner (%.1f dB at Q 4)", lpQ);

    const double gainIgnored = sineGainDb(band(EQ_HIPASS, 1000, 12, 0.71f), 8000);
    CHECK(std::fabs(gainIgnored) < 0.5, "gain does nothing on a cut, as in Serum (%.2f dB)", gainIgnored);

    /* Serum's Reso -> Q, from calibration/fx_probe.py eqfit */
    CHECK(std::fabs(potEqQ(EQ_BELL, 50 * 1.27f) - 1.25f) < 0.05f, "Reso 50 is Q 1.25 on a bell (%.2f)", potEqQ(EQ_BELL, 63.5f));
    CHECK(std::fabs(potEqQ(EQ_BELL, 75 * 1.27f) - 5.5f) < 0.2f, "Reso 75 is Q 5.5 (%.2f)", potEqQ(EQ_BELL, 95.25f));
    CHECK(std::fabs(potEqQ(EQ_LOSHELF, 100 * 1.27f) - 2.1f) < 0.05f, "Reso 100 is Q 2.1 on a shelf (%.2f)", potEqQ(EQ_LOSHELF, 127));
    bool mono = true;
    for (int v = 1; v <= 127; v++) mono &= potEqQ(EQ_BELL, (float) v) >= potEqQ(EQ_BELL, (float) v - 1) - 1e-6f;
    CHECK(mono, "Q only rises with the pot");

    FxSettings off = band(EQ_OFF, 1000, 12, 1.0f);
    Fx fx(SR);
    fx.set(off);
    CHECK(!fx.active(), "a band set to Off is a bypass whatever its other cells say");

    /* a 20 kHz band must not go unstable at 44.1 kHz */
    const double top = sineGainDb(band(EQ_BELL, 20000, 12, 14.0f), 15000);
    CHECK(std::isfinite(top) && top < 12.5, "a 20 kHz, high-Q band stays stable (%.2f dB)", top);
}

/* The EQs inside a SplitMS node's branches work in the mid / side domain: a bell on the Side leaves a mono (mid) signal alone and lifts a pure side signal. */
static void testMidSideEq()
{
    printf("== mid / side EQ ==\n");
    auto gain = [](int branch, bool side) {
        FxSettings s;
        s.msBand[branch][0].type = EQ_BELL; s.msBand[branch][0].hz = 1000; s.msBand[branch][0].gainDb = 12; s.msBand[branch][0].q = 1.25f;
        Fx fx(SR);
        fx.set(s);
        const int n = (int) SR;
        std::vector<float> L((size_t) n), R((size_t) n);
        for (int i = 0; i < n; i++) { L[(size_t) i] = (float) (0.1 * std::sin(6.283185307 * 1000.0 * i / SR)); R[(size_t) i] = side ? -L[(size_t) i] : L[(size_t) i]; }
        run(fx, L, R);
        double a = 0, b = 0;
        for (int i = n / 2; i < n; i++) { a += 0.01 * 0.5; b += (double) L[(size_t) i] * L[(size_t) i]; }
        return 10.0 * std::log10(b / a);
    };
    const double sideOnSide = gain(1, true), sideOnMid = gain(1, false), midOnMid = gain(0, false), midOnSide = gain(0, true);
    CHECK(std::fabs(sideOnSide - 12.0) < 0.3 && std::fabs(sideOnMid) < 0.2, "a Side bell lifts a side signal by its gain and leaves a mid one alone (%.2f / %.2f dB)", sideOnSide, sideOnMid);
    CHECK(std::fabs(midOnMid - 12.0) < 0.3 && std::fabs(midOnSide) < 0.2, "a Mid bell lifts a mid signal and leaves a side one alone (%.2f / %.2f dB)", midOnMid, midOnSide);
}

static void testDelay()
{
    printf("== delay ==\n");
    FxSettings s;
    s.delayMix = 1.0f; s.delayMs = 100; s.delayRMs = 200; s.delayFb = 0.0f; s.delayLoHz = 20; s.delayHiHz = 20000;
    const int kT0 = 512;
    const int n = (int) (SR * 1.2f);
    auto impulse = [&](const FxSettings &st, std::vector<float> &L, std::vector<float> &R) {
        Fx fx(SR);
        fx.set(st);
        L.assign((size_t) n, 0.0f); R.assign((size_t) n, 0.0f);
        L[kT0] = R[kT0] = 1.0f;                  /* after the first block: the mix ramp from reset is over */
        run(fx, L, R);
    };
    auto peakAt = [](const std::vector<float> &x, int from) {
        int best = from; for (int i = from; i < (int) x.size(); i++) if (std::fabs(x[(size_t) i]) > std::fabs(x[(size_t) best])) best = i;
        return best;
    };
    std::vector<float> L, R;
    impulse(s, L, R);
    const int eL = peakAt(L, kT0 + 100), eR = peakAt(R, kT0 + 100);
    CHECK(std::abs(eL - kT0 - 4410) <= 1 && std::abs(eR - kT0 - 8820) <= 1, "stereo mode: echoes at 100 ms and 200 ms (%d, %d samples)", eL - kT0, eR - kT0);
    CHECK(std::fabs(L[(size_t) eL] - 1.09f) < 0.05f, "a tap at wet 100 is %.3f of the click: the echo level of a noise burst is Serum's, and the delay's band-pass acts on the first echo", L[(size_t) eL]);
    CHECK(std::fabs(L[kT0]) < 1e-6f, "and the dry is gone at wet 100 (%.4f)", L[kT0]);

    FxSettings two = s;                          /* a second delay behind the first: its echoes are the first's echoes delayed again (Serum racks with two Delay nodes) */
    two.delay2Mix = 1.0f; two.delay2Ms = 300; two.delay2RMs = 300; two.delay2Fb = 0.0f; two.delay2LoHz = 20; two.delay2HiHz = 20000; two.delay2Ping = 0;
    std::vector<float> L2, R2;
    impulse(two, L2, R2);
    const int e2L = peakAt(L2, kT0 + 100), e2R = peakAt(R2, kT0 + 100);
    CHECK(std::abs(e2L - kT0 - 17640) <= 2 && std::abs(e2R - kT0 - 22050) <= 2, "two delays: the echoes land at 100 + 300 ms and 200 + 300 ms (%d, %d samples)", e2L - kT0, e2R - kT0);

    FxSettings ping = s;
    ping.delayPing = 1;
    impulse(ping, L, R);
    const int pL = peakAt(L, kT0 + 100) - kT0, pR = peakAt(R, kT0 + 100) - kT0;
    CHECK(std::abs(pL - 4410) <= 1 && std::abs(pR - 13230) <= 2, "ping-pong: left at 100 ms, right at 100 + 200 ms (%d, %d)", pL, pR);

    FxSettings fb = s;
    fb.delayMs = 100; fb.delayRMs = 100; fb.delayFb = 0.5f; fb.delayPing = 0;
    impulse(fb, L, R);
    auto win = [&](int centre) { double a = 0; for (int i = centre - 8; i < centre + 24; i++) a += (double) L[(size_t) i] * L[(size_t) i]; return std::sqrt(a); };
    const double e1 = win(kT0 + 4410), e2 = win(kT0 + 8820);
    CHECK(std::fabs(e2 / e1 - 0.5) < 0.04, "feedback 50 %% halves each repeat (%.3f)", e2 / e1);

    FxSettings half = s;
    half.delayMix = 0.5f;
    impulse(half, L, R);
    CHECK(std::fabs(L[kT0] - dryGain(0.5f)) < 0.02f, "wet 50 keeps %.2f of the dry (%.3f)", dryGain(0.5f), L[kT0]);

    FxSettings lp = fb;
    lp.delayHiHz = 500;
    impulse(lp, L, R);
    double hf1 = 0, hf2 = 0;
    for (int i = kT0 + 4400; i < kT0 + 4430; i++) hf1 += std::fabs(L[(size_t) i] - L[(size_t) i - 1]);
    for (int i = kT0 + 8800; i < kT0 + 8860; i++) hf2 += std::fabs(L[(size_t) i] - L[(size_t) i - 1]);
    CHECK(hf2 < hf1 * 0.8, "a low cut-off dulls each repeat (%.3f -> %.3f)", hf1, hf2);
}

/* RT60 from the slope of the tail between -10 and -40 dB of its first 1 s peak */
static double measureRt60(float t60, float dampHz, float *widthCorr = nullptr, float width = 1.0f)
{
    FxSettings s;
    s.revMix = 1.0f; s.revT60 = t60; s.revDampHz = dampHz; s.revLoHz = 20; s.revWidth = width;
    Fx fx(SR);
    fx.set(s);
    const int n = (int) (SR * 12.0f);
    std::vector<float> L((size_t) n, 0.0f), R((size_t) n, 0.0f);
    uint32_t seed = 3;
    for (int i = 0; i < (int) (0.25f * SR); i++) L[(size_t) i] = R[(size_t) i] = 0.5f * rnd(seed);
    run(fx, L, R);
    const int hop = 441;
    std::vector<double> e;
    for (int i = 0; i + hop <= n; i += hop) {
        double a = 0; for (int k = 0; k < hop; k++) a += (double) L[(size_t) (i + k)] * L[(size_t) (i + k)];
        e.push_back(10.0 * std::log10(a / hop + 1e-30));
    }
    int pk = 5;                                    /* skip the first 50 ms: the dry fading out over the first block */
    for (int i = 5; i < (int) e.size(); i++) if (e[(size_t) i] > e[(size_t) pk]) pk = i;
    int a = -1, b = -1;
    for (int i = pk; i < (int) e.size(); i++) {
        if (a < 0 && e[(size_t) i] < e[(size_t) pk] - 10) a = i;
        if (b < 0 && e[(size_t) i] < e[(size_t) pk] - 40) { b = i; break; }
    }
    if (widthCorr) {
        double lr = 0, ll = 0, rr = 0;
        for (int i = (int) (0.5f * SR); i < (int) (3.0f * SR); i++) { lr += (double) L[(size_t) i] * R[(size_t) i]; ll += (double) L[(size_t) i] * L[(size_t) i]; rr += (double) R[(size_t) i] * R[(size_t) i]; }
        *widthCorr = (float) (lr / std::sqrt(ll * rr + 1e-30));
    }
    if (a < 0 || b < 0) return -1;
    return 2.0 * (b - a) * hop / SR;                /* 30 dB took (b - a) hops, so 60 dB takes twice that */
}

static void testReverb()
{
    printf("== reverb ==\n");
    for (float t : { 0.5f, 2.0f, 6.0f }) {
        const double rt = measureRt60(t, 20000.0f);
        CHECK(rt > t * 0.75 && rt < t * 1.2, "decay %.1f s measures %.2f s (the damping filter costs ~15 %% even wide open)", t, rt);
    }
    const double dark = measureRt60(2.0f, 1500.0f);
    const double bright = measureRt60(2.0f, 20000.0f);
    CHECK(dark < bright, "damping shortens the tail (%.2f s dark, %.2f s open)", dark, bright);

    float corr = 0;
    measureRt60(2.0f, 12000.0f, &corr, 1.0f);
    CHECK(std::fabs(corr) < 0.35f, "width 1: the two sides are decorrelated (%.2f)", corr);
    measureRt60(2.0f, 12000.0f, &corr, 0.0f);
    CHECK(corr > 0.99f, "width 0: the tail is mono (%.3f)", corr);

    /* the longest setting must neither grow nor produce NaN */
    FxSettings s;
    s.revMix = 1.0f; s.revT60 = 30.0f; s.revDampHz = 20000.0f;
    Fx fx(SR);
    fx.set(s);
    std::vector<float> L((size_t) (SR * 20), 0.0f), R = L;
    uint32_t seed = 11;
    for (int i = 0; i < 4410; i++) L[(size_t) i] = R[(size_t) i] = rnd(seed);
    run(fx, L, R);
    bool finite = true; float pk = 0;
    for (size_t i = 0; i < L.size(); i++) { finite &= std::isfinite(L[i]) && std::isfinite(R[i]); pk = std::max(pk, std::fabs(L[i])); }
    CHECK(finite && pk < 2.0f, "30 s decay stays finite and bounded (peak %.2f)", pk);

    /* level: steady noise through the reverb at wet 100 vs the dry noise. Serum's is about 0 dB
     * and stays within +/-1.5 dB from its shortest to its longest hall, so the engine's has to
     * not grow with the decay either. */
    double lo = 99, hi = -99;
    for (float t60 : { 0.4f, 1.0f, 2.0f, 6.0f, 15.0f }) {
        FxSettings lv;
        lv.revMix = 1.0f; lv.revT60 = t60; lv.revDampHz = 12000.0f;
        Fx f2(SR);
        f2.set(lv);
        std::vector<float> a((size_t) (SR * 24)), b = a;
        seed = 5;
        for (size_t i = 0; i < a.size(); i++) a[i] = b[i] = 0.3f * rnd(seed);
        double din = 0; for (size_t i = (size_t) (SR * 20); i < a.size(); i++) din += (double) a[i] * a[i];
        run(f2, a, b);
        double dout = 0; for (size_t i = (size_t) (SR * 20); i < a.size(); i++) dout += (double) a[i] * a[i];
        const double db = 10 * std::log10(dout / din);
        printf("  info: steady wet-100 reverb at decay %.1f s: %+.2f dB re its input\n", t60, db);
        lo = std::min(lo, db); hi = std::max(hi, db);
    }
    CHECK(hi - lo < 3.0, "the wet level is flat across decay times (%.2f dB spread)", hi - lo);
    /* Serum's steady noise at wet 100 is -0.5 dB; the engine is set 3.5 dB above that (kRevOut 2.25, 1.5 times the noise-matched 1.50) because the tail of a played note,
     * the thing heard, ran 2-5 dB under Serum's with the noise-matched level (calibration/reverb_engine_only.py). Noise-like sources therefore get a wet that is about 3 dB hot. */
    CHECK(std::fabs(0.5 * (hi + lo) - 3.0) < 1.5, "and sits 3.5 dB over Serum's noise figure of -0.5 dB, the level that matches played-note tails (%.2f dB)", 0.5 * (hi + lo));

    /* a stage that is switched off and on again must not replay its old tail */
    FxSettings on;
    on.revMix = 1.0f; on.revT60 = 4.0f;
    Fx f3(SR);
    f3.set(on);
    std::vector<float> n1((size_t) SR, 0.0f), n2 = n1;
    seed = 9;
    for (size_t i = 0; i < 8000; i++) n1[i] = n2[i] = rnd(seed);
    run(f3, n1, n2);
    FxSettings offs = on; offs.revMix = 0;
    f3.set(offs);
    std::vector<float> z1((size_t) (SR * 0.3f), 0.0f), z2 = z1;
    run(f3, z1, z2);                              /* fades out, then clears */
    run(f3, z1, z2);
    f3.set(on);
    std::vector<float> q1((size_t) (SR * 0.5f), 0.0f), q2 = q1;
    run(f3, q1, q2);
    float qp = 0; for (float v : q1) qp = std::max(qp, std::fabs(v));
    CHECK(qp < 1e-15f, "switching the reverb off and back on starts it clean (peak %.2g)", qp);
}

static void testWide()
{
    printf("== width ==\n");
    FxSettings s;
    s.wideMix = 1.0f; s.wideRate = 0.8f; s.wideDepth = 0.6f;
    Fx fx(SR);
    fx.set(s);
    const int n = (int) (SR * 2);
    std::vector<float> L((size_t) n), R((size_t) n);
    uint32_t seed = 13;
    for (int i = 0; i < n; i++) L[(size_t) i] = R[(size_t) i] = 0.3f * rnd(seed);
    const auto in = L;
    run(fx, L, R);
    double md = 0, sd = 0, ms = 0;
    for (int i = 0; i < n; i++) {
        md = std::max(md, (double) std::fabs(0.5f * (L[(size_t) i] + R[(size_t) i]) - in[(size_t) i]));
        sd += (double) (L[(size_t) i] - R[(size_t) i]) * (L[(size_t) i] - R[(size_t) i]);
        ms += (double) in[(size_t) i] * in[(size_t) i];
    }
    CHECK(md < 1e-6, "the mono sum of the output is the input, so width costs no cancellation (%.2g)", md);
    CHECK(10 * std::log10(sd / 4 / ms) > -12.0, "and it does widen: side/mid %.1f dB", 10 * std::log10(sd / 4 / ms));
    FxSettings off;
    Fx f0(SR);
    f0.set(off);
    CHECK(!f0.active(), "width 0 is a bypass");
}

static void testLaws()
{
    printf("== wet and dry laws ==\n");
    /* measured on Serum's reverb: tail at wet 10/25/50/75 relative to wet 100 */
    const float meas[4] = { 0.027f, 0.152f, 0.51f, 0.87f };
    const float at[4] = { 0.1f, 0.25f, 0.5f, 0.75f };
    bool ok = true;
    for (int i = 0; i < 4; i++) ok &= std::fabs(wetGain(at[i]) - meas[i]) < 0.04f;
    CHECK(ok, "the wet law follows Serum's smoothstep");
    CHECK(std::fabs(dryGain(0.5f) - 0.785f) < 0.06f && std::fabs(dryGain(0.75f) - 0.26f) < 0.06f && dryGain(0.1f) == 1.0f && dryGain(1.0f) == 0.0f,
          "the dry law: 1 to 25 %%, %.2f at 50, %.2f at 75, 0 at 100 (Serum: 0.785 and 0.26)", dryGain(0.5f), dryGain(0.75f));
}

static void testStress()
{
    printf("== stress ==\n");
    uint32_t seed = 21;
    bool finite = true; float peak = 0;
    for (int trial = 0; trial < 12; trial++) {
        FxSettings s;
        s.wideMix = 0.5f * (rnd(seed) + 1); s.wideRate = 0.05f * std::pow(100.0f, 0.5f * (rnd(seed) + 1)); s.wideDepth = 0.5f * (rnd(seed) + 1);
        s.delayMix = 0.5f * (rnd(seed) + 1); s.delayMs = std::pow(1500.0f, 0.5f * (rnd(seed) + 1)); s.delayRMs = std::pow(1500.0f, 0.5f * (rnd(seed) + 1));
        s.delayFb = 0.97f * 0.5f * (rnd(seed) + 1); s.delayLoHz = potEqFreq(63.5f * (rnd(seed) + 1)); s.delayHiHz = potEqFreq(63.5f * (rnd(seed) + 1));
        s.delayPing = trial & 1;
        s.revMix = 0.5f * (rnd(seed) + 1); s.revT60 = 0.2f * std::pow(150.0f, 0.5f * (rnd(seed) + 1)); s.revDampHz = potEqFreq(63.5f * (rnd(seed) + 1));
        s.revLoHz = potEqFreq(63.5f * (rnd(seed) + 1)); s.revWidth = 0.5f * (rnd(seed) + 1);
        for (int b = 0; b < 4; b++) {
            s.band[b].type = (int) (3.0f * (rnd(seed) + 1)) % 6;
            s.band[b].hz = potEqFreq(63.5f * (rnd(seed) + 1)); s.band[b].gainDb = 24 * rnd(seed);
            s.band[b].q = potEqQ(s.band[b].type, 63.5f * (rnd(seed) + 1));
        }
        Fx fx(SR);
        fx.set(s);
        std::vector<float> L((size_t) (SR * 3)), R = L;
        for (size_t i = 0; i < L.size(); i++) L[i] = R[i] = i < (size_t) SR * 2 ? 0.3f * rnd(seed) : 0.0f;
        run(fx, L, R);
        for (size_t i = 0; i < L.size(); i++) { finite &= std::isfinite(L[i]) && std::isfinite(R[i]); peak = std::max(peak, std::max(std::fabs(L[i]), std::fabs(R[i]))); }
    }
    CHECK(finite, "12 random settings of every control stay finite");
    CHECK(peak < 400.0f, "and bounded (peak %.1f; a +24 dB bell on a q-14 resonance is the worst case)", peak);
}

/* the engine, through the pots */
static std::vector<int16_t> renderBlocks(Engine &e, int blocks)
{
    std::vector<int16_t> out((size_t) blocks * 256);
    for (int b = 0; b < blocks; b++) e.renderBlock(out.data() + (size_t) b * 256, 128);
    return out;
}

static void playChord(Engine &e)
{
    srand(1);                                     /* start phases are random: make them the same */
    for (int n : { 48, 55, 60 }) { const uint8_t on[] = { 0x90, (uint8_t) n, 100 }; e.onMidi(on, 3); }
}
static void releaseChord(Engine &e)
{
    for (int n : { 48, 55, 60 }) { const uint8_t off[] = { 0x80, (uint8_t) n, 0 }; e.onMidi(off, 3); }
}

static double tailRms(const std::vector<int16_t> &x, size_t from)
{
    double a = 0; size_t k = 0;
    for (size_t i = from; i < x.size() / 2; i++) { a += (double) x[i * 2] * x[i * 2]; k++; }
    return std::sqrt(a / (double) std::max<size_t>(k, 1));
}

static void testEngine()
{
    printf("== through the engine ==\n");
    auto setup = [](Engine &e) {
        e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127; e.pots[TB_P_VCA_R] = 20;
    };

    Engine a, b;
    setup(a); setup(b);
    playChord(a); playChord(b);
    const auto x = renderBlocks(a, 80), y = renderBlocks(b, 80);
    CHECK(x == y, "two engines at the default FX pots render the same");

    /* reverb tail outlives the note */
    Engine dry, wet;
    setup(dry); setup(wet);
    wet.pots[TB_P_REV_MIX] = 90; wet.pots[TB_P_REV_DECAY] = 90;
    playChord(dry); playChord(wet);
    renderBlocks(dry, 100); renderBlocks(wet, 100);
    releaseChord(dry); releaseChord(wet);
    renderBlocks(dry, 100); renderBlocks(wet, 100);                /* the release is gone */
    const auto dt = renderBlocks(dry, 100), wt = renderBlocks(wet, 100);
    CHECK(tailRms(dt, 0) < 1.0 && tailRms(wt, 0) > 20.0, "the reverb rings on after the note is gone (%.1f vs %.1f rms)", tailRms(wt, 0), tailRms(dt, 0));

    /* SPACE scales the mixes: 0 is dry, bit for bit */
    Engine s0, ref;
    setup(s0); setup(ref);
    s0.pots[TB_P_REV_MIX] = 100; s0.pots[TB_P_DLY_MIX] = 100; s0.pots[TB_P_FX_SPACE] = 0;
    playChord(s0); playChord(ref);
    CHECK(renderBlocks(s0, 60) == renderBlocks(ref, 60), "SPACE at 0 is the dry sound exactly");

    /* SPACE at 100 is the preset as programmed, and a little more is more */
    Engine p100, p127;
    setup(p100); setup(p127);
    for (Engine *e : { &p100, &p127 }) { e->pots[TB_P_REV_MIX] = 40; e->pots[TB_P_REV_DECAY] = 60; }
    p100.pots[TB_P_FX_SPACE] = 100; p127.pots[TB_P_FX_SPACE] = 127;
    playChord(p100); playChord(p127);
    renderBlocks(p100, 40); renderBlocks(p127, 40);
    releaseChord(p100); releaseChord(p127);
    renderBlocks(p100, 40); renderBlocks(p127, 40);
    CHECK(tailRms(renderBlocks(p127, 30), 0) > tailRms(renderBlocks(p100, 30), 0), "SPACE above 100 gives more of the tail");

    /* a knob turn mid-note does not step */
    Engine t;
    setup(t);
    t.pots[TB_P_REV_MIX] = 0;
    playChord(t);
    renderBlocks(t, 40);
    std::vector<int16_t> all;
    int maxJump = 0;
    for (int blk = 0; blk < 200; blk++) {
        if (blk == 50) t.pots[TB_P_REV_MIX] = 110;
        if (blk == 100) t.pots[TB_P_DLY_MIX] = 110;
        if (blk == 150) t.pots[TB_P_EQ1_TYPE] = 2, t.pots[TB_P_EQ1_GAIN] = 100;
        auto blk_out = renderBlocks(t, 1);
        all.insert(all.end(), blk_out.begin(), blk_out.end());
    }
    for (size_t i = 1; i < all.size() / 2; i++) maxJump = std::max(maxJump, std::abs((int) all[i * 2] - (int) all[(i - 1) * 2]));
    Engine base;
    setup(base);
    playChord(base);
    renderBlocks(base, 40);
    auto ref2 = renderBlocks(base, 200);
    int refJump = 0;
    for (size_t i = 1; i < ref2.size() / 2; i++) refJump = std::max(refJump, std::abs((int) ref2[i * 2] - (int) ref2[(i - 1) * 2]));
    CHECK(maxJump < refJump * 2 + 400, "turning mix and EQ mid-note adds no step (largest sample jump %d, the note's own %d)", maxJump, refJump);
}

/* The on/off switches: each stage, and the whole block */
static void testSwitches()
{
    printf("== switches ==\n");
    auto setup = [](Engine &e) {
        e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127; e.pots[TB_P_VCA_R] = 20;
        e.pots[TB_P_REV_MIX] = 80; e.pots[TB_P_REV_DECAY] = 80;
        e.pots[TB_P_DLY_MIX] = 80; e.pots[TB_P_FX_WIDE] = 100;
        e.pots[TB_P_EQ1_TYPE] = 2; e.pots[TB_P_EQ1_GAIN] = 110; e.pots[TB_P_EQ1_FREQ] = 60;   /* a bell, well boosted */
    };
    /* After a settle, a switched-off stage is exactly the stage with its mix at 0 (and, for the EQ, with no
     * bands): the same path through the block, so the same samples. */
    auto same = [&](const char *what, int onPot, int offPot, float offVal) {
        Engine sw, ref;
        setup(sw); setup(ref);
        sw.pots[onPot] = 0;
        ref.pots[offPot] = offVal;
        playChord(sw); playChord(ref);
        renderBlocks(sw, 60); renderBlocks(ref, 60);
        CHECK(renderBlocks(sw, 40) == renderBlocks(ref, 40), "%s off is the same as the stage taken out", what);
    };
    same("reverb", TB_P_REV_ON, TB_P_REV_MIX, 0);
    same("delay", TB_P_DLY_ON, TB_P_DLY_MIX, 0);
    same("width", TB_P_WIDE_ON, TB_P_FX_WIDE, 0);
    same("EQ", TB_P_EQ_ON, TB_P_EQ1_TYPE, 0);

    Engine all, none;
    setup(all); setup(none);
    all.pots[TB_P_FX_ON] = 0;
    none.pots[TB_P_REV_MIX] = 0; none.pots[TB_P_DLY_MIX] = 0; none.pots[TB_P_FX_WIDE] = 0; none.pots[TB_P_EQ1_TYPE] = 0;
    playChord(all); playChord(none);
    renderBlocks(all, 60); renderBlocks(none, 60);
    CHECK(renderBlocks(all, 40) == renderBlocks(none, 40), "FX Enable off is every stage taken out");

    /* On is the default, and the EQ switched on is the EQ as it was */
    Engine on, def;
    setup(on); setup(def);
    on.pots[TB_P_FX_ON] = 1; on.pots[TB_P_EQ_ON] = 1; on.pots[TB_P_WIDE_ON] = 1; on.pots[TB_P_DLY_ON] = 1; on.pots[TB_P_REV_ON] = 1;
    playChord(on); playChord(def);
    CHECK(renderBlocks(on, 60) == renderBlocks(def, 60), "all switches On is the default");

    /* Switching mid-note does not step, nor does switching back. The sound itself changes with the stage
     * (a boosted EQ or a wide chorus has bigger sample steps than the dry chord), so the switch window is
     * compared with the steady sound on each side of it, not with one fixed baseline. */
    auto jumpIn = [](const std::vector<int16_t> &x, int from, int to) {
        int m = 0;
        for (int i = from * 128 + 1; i < to * 128; i++) m = std::max(m, std::abs((int) x[(size_t) i * 2] - (int) x[(size_t) (i - 1) * 2]));
        return m;
    };
    const struct { int pot; const char *name; } sw[] = { { TB_P_EQ_ON, "EQ" }, { TB_P_WIDE_ON, "width" }, { TB_P_DLY_ON, "delay" },
                                                         { TB_P_REV_ON, "reverb" }, { TB_P_FX_ON, "FX Enable" } };
    for (const auto &w : sw) {
        Engine t;
        setup(t); playChord(t); renderBlocks(t, 40);
        std::vector<int16_t> all2;
        for (int blk = 0; blk < 300; blk++) {
            if (blk == 100) t.pots[w.pot] = 0;
            if (blk == 200) t.pots[w.pot] = 1;
            auto b = renderBlocks(t, 1);
            all2.insert(all2.end(), b.begin(), b.end());
        }
        const int pre = jumpIn(all2, 60, 100), off = jumpIn(all2, 115, 200), on = jumpIn(all2, 215, 300);
        const int off1 = jumpIn(all2, 100, 115), on1 = jumpIn(all2, 200, 215);
        CHECK(off1 < 2 * std::max(pre, off) + 400 && on1 < 2 * std::max(off, on) + 400,
              "switching %s off and on mid-note adds no step (off %d vs steady %d/%d, on %d vs %d/%d)", w.name, off1, pre, off, on1, off, on);
    }

    /* the volume range: squared, 127 is 18.0 dB above 45 (the old 127 is the new 45) */
    Engine lo, hi;
    for (Engine *e : { &lo, &hi }) { e->pots[TB_P_WT1_LEVEL] = 8; e->pots[TB_P_VCA_A] = 0; e->pots[TB_P_VCA_S] = 127; }
    lo.pots[TB_P_VOLUME] = 45; hi.pots[TB_P_VOLUME] = 127;
    playChord(lo); playChord(hi);
    renderBlocks(lo, 20); renderBlocks(hi, 20);
    const double rl = tailRms(renderBlocks(lo, 30), 0), rh = tailRms(renderBlocks(hi, 30), 0);
    CHECK(std::fabs(20.0 * std::log10(rh / rl) - 18.0) < 0.3, "volume 127 is 18 dB over volume 45 (%.2f dB)", 20.0 * std::log10(rh / rl));
}

/* The performance knobs on top of a preset: Decay, Drive and the two shelves */
static void testTone()
{
    printf("== tone ==" "\n");
    auto setup = [](Engine &e) {
        e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_D] = 70; e.pots[TB_P_VCA_S] = 0; e.pots[TB_P_VCA_R] = 20;
    };
    auto rmsOf = [](const std::vector<int16_t> &x) { return tailRms(x, 0); };

    /* Decay: 64 is the preset as programmed, up is longer, down is shorter, on the amp envelope */
    auto tail = [&](float decay) {
        Engine e; setup(e);
        e.pots[TB_P_DECAY] = decay;
        playChord(e);
        renderBlocks(e, 25);
        return rmsOf(renderBlocks(e, 25));
    };
    const double t64 = tail(64), t100 = tail(100), t30 = tail(30);
    CHECK(t100 > t64 * 1.5 && t30 < t64 * 0.6, "Decay moves the amp envelope's decay (tail rms %.0f at 30, %.0f at 64, %.0f at 100)", t30, t64, t100);
    Engine d0, d1;
    setup(d0); setup(d1);
    d1.pots[TB_P_DECAY] = 64;
    playChord(d0); playChord(d1);
    CHECK(renderBlocks(d0, 40) == renderBlocks(d1, 40), "Decay at 64 is the default exactly");

    /* the same macro moves the FILTER envelope too: a closing filter sounds later with a higher Decay */
    auto bright = [&](float decay) {
        Engine e;
        e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127;
        e.pots[TB_P_FLT_FREQ] = 20; e.pots[TB_P_FLT_ENV] = 120; e.pots[TB_P_FLT_A] = 0;
        e.pots[TB_P_FLT_D] = 60; e.pots[TB_P_FLT_S] = 0;
        e.pots[TB_P_DECAY] = decay;
        playChord(e);
        renderBlocks(e, 6);                                /* the filter is still closing here for all three */
        const auto x = renderBlocks(e, 10);
        double a = 0; size_t k = 0;
        for (size_t i = 1; i < x.size() / 2; i++) { const double d = (double) x[i * 2] - x[(i - 1) * 2]; a += d * d; k++; }
        return std::sqrt(a / (double) k);                  /* the sample-to-sample difference: the top end */
    };
    CHECK(bright(100) > bright(64) * 1.3 && bright(30) < bright(64) * 0.8, "and the filter envelope's (brightness %.0f at 30, %.0f at 64, %.0f at 100)", bright(30), bright(64), bright(100));

    /* Shelves: flat at 64, and each does its +/-12 dB. The biquad at 0 dB is the identity. */
    {
        StereoBiquad lo, hi;
        lo.c.set(EQ_LOSHELF, 150.0f, 0.0f, 0.7071f, SR);
        hi.c.set(EQ_HISHELF, 5000.0f, 0.0f, 0.7071f, SR);
        uint32_t seed = 7;
        float worst = 0;
        for (int i = 0; i < 20000; i++) {
            const float x = 0.5f * rnd(seed);
            worst = std::max(worst, std::fabs(hi.tick(0, lo.tick(0, x)) - x));
        }
        CHECK(worst < 1e-5f, "the two shelves at 0 dB pass a signal through (largest difference %.2e)", worst);   /* the engine skips them there: exact */
    }
    auto lowRms = [&](float lo, float hi) {
        Engine e;
        e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127; e.pots[TB_P_LO_SHELF] = lo; e.pots[TB_P_HI_SHELF] = hi;
        e.pots[TB_P_WT1_TUNE] = 0;
        srand(1);
        for (int n : { 28, 31 }) { const uint8_t on[] = { 0x90, (uint8_t) n, 100 }; e.onMidi(on, 3); }
        renderBlocks(e, 60);
        return rmsOf(renderBlocks(e, 30));
    };
    const double lflat = lowRms(64, 64), lup = lowRms(127, 64), ldown = lowRms(0, 64);
    CHECK(20 * std::log10(lup / lflat) > 5.0 && 20 * std::log10(ldown / lflat) < -5.0,
          "Low Shelf boosts and cuts the lows (%+.1f dB, %+.1f dB at the ends)", 20 * std::log10(lup / lflat), 20 * std::log10(ldown / lflat));
    auto highDiff = [&](float lo, float hi) {
        Engine e;
        e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127; e.pots[TB_P_LO_SHELF] = lo; e.pots[TB_P_HI_SHELF] = hi;
        srand(1);
        for (int n : { 84, 91 }) { const uint8_t on[] = { 0x90, (uint8_t) n, 100 }; e.onMidi(on, 3); }
        renderBlocks(e, 60);
        const auto x = renderBlocks(e, 30);
        double a = 0; size_t k = 0;
        for (size_t i = 1; i < x.size() / 2; i++) { const double d = (double) x[i * 2] - x[(i - 1) * 2]; a += d * d; k++; }
        return std::sqrt(a / (double) k);
    };
    const double hflat = highDiff(64, 64), hup = highDiff(64, 127), hdown = highDiff(64, 0);
    CHECK(20 * std::log10(hup / hflat) > 3.0 && 20 * std::log10(hdown / hflat) < -3.0,
          "High Shelf boosts and cuts the top (%+.1f dB, %+.1f dB at the ends)", 20 * std::log10(hup / hflat), 20 * std::log10(hdown / hflat));

    /* Drive: 0 is the signal; up rounds the peaks and keeps the level near where it was */
    auto peakRms = [&](float drive, double &pk) {
        Engine e;
        e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127; e.pots[TB_P_DRIVE] = drive; e.pots[TB_P_VOLUME] = 60;
        playChord(e);
        renderBlocks(e, 60);
        const auto x = renderBlocks(e, 30);
        pk = 0;
        for (int16_t v : x) pk = std::max(pk, (double) std::abs((int) v));
        return rmsOf(x);
    };
    double pk0, pk127;
    const double r0 = peakRms(0, pk0), r127 = peakRms(127, pk127);
    CHECK(pk127 < pk0 && std::fabs(20 * std::log10(r127 / r0)) < 6.0 && r127 != r0,
          "Drive at full rounds the peaks (%.0f -> %.0f) and keeps the level (%+.1f dB)", pk0, pk127, 20 * std::log10(r127 / r0));

    /* no step when a knob is thrown mid-note: compare the switch window with the steady sound either side */
    auto jumpIn = [](const std::vector<int16_t> &x, int from, int to) {
        int m = 0;
        for (int i = from * 128 + 1; i < to * 128; i++) m = std::max(m, std::abs((int) x[(size_t) i * 2] - (int) x[(size_t) (i - 1) * 2]));
        return m;
    };
    const struct { int pot; float to; const char *name; } knobs[] = {
        { TB_P_DRIVE, 127, "Drive" }, { TB_P_LO_SHELF, 127, "Low Shelf" }, { TB_P_LO_SHELF, 0, "Low Shelf down" },
        { TB_P_HI_SHELF, 127, "High Shelf" }, { TB_P_HI_SHELF, 0, "High Shelf down" }, { TB_P_DECAY, 110, "Decay" } };
    for (const auto &k : knobs) {
        Engine e;
        e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127; e.pots[TB_P_VOLUME] = 50;
        playChord(e); renderBlocks(e, 40);
        std::vector<int16_t> all;
        for (int blk = 0; blk < 200; blk++) {
            if (blk == 100) e.pots[k.pot] = k.to;
            auto b = renderBlocks(e, 1);
            all.insert(all.end(), b.begin(), b.end());
        }
        const int pre = jumpIn(all, 60, 100), post = jumpIn(all, 130, 200), win = jumpIn(all, 100, 130);
        CHECK(win < 2 * std::max(pre, post) + 400, "throwing %s mid-note adds no step (window %d vs steady %d/%d)", k.name, win, pre, post);
    }
}

/* Master width and the mono-below crossover */
static void testStereo()
{
    printf("== stereo ==\n");
    auto stereoSetup = [](Engine &e, int note) {
        e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127;
        e.pots[TB_P_WT1_UNI] = 5; e.pots[TB_P_WT1_SPREAD] = 127; e.pots[TB_P_WT1_DETUNE] = 40;
        (void) note;
    };
    /* RMS of the side (L-R)/2 and mid over a held note, from one render */
    struct Img { double side, mid; };
    auto image = [&](float width, float monoHz, int note) {
        Engine e;
        stereoSetup(e, note);
        e.pots[TB_P_MWIDTH] = width; e.pots[TB_P_MONOHZ] = monoHz;
        srand(1);
        { const uint8_t on[] = { 0x90, (uint8_t) note, 100 }; e.onMidi(on, 3); }
        renderBlocks(e, 80);
        const auto x = renderBlocks(e, 40);
        double s = 0, m = 0; size_t k = 0;
        for (size_t i = 0; i < x.size() / 2; i++) {
            const double l = x[i * 2], r = x[i * 2 + 1];
            s += 0.25 * (l - r) * (l - r); m += 0.25 * (l + r) * (l + r); k++;
        }
        return Img { std::sqrt(s / (double) k), std::sqrt(m / (double) k) };
    };

    /* neutral is the default, and the unison stack really has a side to work on */
    const Img base = image(64, 0, 48);
    CHECK(base.side > 0.05 * base.mid, "the test note has a stereo image (side %.0f, mid %.0f)", base.side, base.mid);

    /* Width: 0 is mono, 127 is about twice the side, the mid does not move */
    const Img w0 = image(0, 0, 48), w127 = image(127, 0, 48);
    CHECK(w0.side < 0.02 * base.side, "width 0 is mono (side %.1f against %.0f)", w0.side, base.side);
    CHECK(std::fabs(20 * std::log10(w127.side / base.side) - 5.9) < 0.8, "width 127 doubles the side (%+.1f dB)", 20 * std::log10(w127.side / base.side));
    CHECK(std::fabs(20 * std::log10(w127.mid / base.mid)) < 0.3 && std::fabs(20 * std::log10(w0.mid / base.mid)) < 0.3, "and the mid is untouched (%+.2f, %+.2f dB)",
          20 * std::log10(w127.mid / base.mid), 20 * std::log10(w0.mid / base.mid));

    /* Mono below: the side of a low note goes, the side of a note well above the crossover stays */
    const Img lowMono = image(64, 127, 36), lowBase = image(64, 0, 36);
    CHECK(lowMono.side < 0.6 * lowBase.side, "mono below 500 Hz takes the side out of a low note (%.0f against %.0f; its harmonics above the crossover stay, and how many is down to the random unison phases, which differ between this PC and the Move)", lowMono.side, lowBase.side);
    const Img highMono = image(64, 20, 84), highBase = image(64, 0, 84);
    CHECK(highMono.side > 0.9 * highBase.side, "and leaves a note far above the crossover alone (%.0f against %.0f)", highMono.side, highBase.side);
    CHECK(std::fabs(20 * std::log10(lowMono.mid / lowBase.mid)) < 0.3, "the mid is not touched by it (%+.2f dB)", 20 * std::log10(lowMono.mid / lowBase.mid));

    /* neither steps when thrown mid-note */
    auto jumpIn = [](const std::vector<int16_t> &x, int from, int to) {
        int m = 0;
        for (int i = from * 128 + 1; i < to * 128; i++) m = std::max(m, std::abs((int) x[(size_t) i * 2] - (int) x[(size_t) (i - 1) * 2]));
        return m;
    };
    const struct { int pot; float to; const char *name; } knobs[] = { { TB_P_MWIDTH, 0, "Master Width to mono" }, { TB_P_MWIDTH, 127, "Master Width to wide" },
                                                                      { TB_P_MONOHZ, 100, "Mono Below on" } };
    for (const auto &k : knobs) {
        Engine e;
        stereoSetup(e, 36);
        srand(1);
        for (int n : { 36, 43 }) { const uint8_t on[] = { 0x90, (uint8_t) n, 100 }; e.onMidi(on, 3); }
        renderBlocks(e, 40);
        std::vector<int16_t> all;
        for (int blk = 0; blk < 200; blk++) {
            if (blk == 100) e.pots[k.pot] = k.to;
            auto b = renderBlocks(e, 1);
            all.insert(all.end(), b.begin(), b.end());
        }
        const int pre = jumpIn(all, 60, 100), post = jumpIn(all, 150, 200), win = jumpIn(all, 100, 150);
        CHECK(win < 2 * std::max(pre, post) + 400, "throwing %s mid-note adds no step (window %d vs steady %d/%d)", k.name, win, pre, post);
    }
}

/* Distortion: the first stage of the block */
static double goertzelDb(const std::vector<float> &x, double hz)
{
    double c = 2 * std::cos(6.283185307 * hz / SR), s1 = 0, s2 = 0;
    for (float v : x) { const double s = v + c * s1 - s2; s2 = s1; s1 = s; }
    const double p = s1 * s1 + s2 * s2 - c * s1 * s2;
    return 10 * std::log10(p / (double) x.size() / (double) x.size() * 4 + 1e-20);
}

static void testDist()
{
    printf("== distortion ==\n");
    /* a sine at f0 through the block with the distortion alone, analysed after it has settled */
    const double f0 = 220.0;
    auto through = [&](float drive, float mix, int mode, float amp) {
        FxSettings s;
        s.distDrive = drive; s.distMix = mix; s.distMode = mode;
        Fx fx(SR);
        fx.set(s);
        const int n = (int) SR;
        std::vector<float> L((size_t) n), R((size_t) n);
        for (int i = 0; i < n; i++) L[(size_t) i] = R[(size_t) i] = (float) (amp * std::sin(6.283185307 * f0 * i / SR));
        run(fx, L, R);
        return std::vector<float>(L.begin() + n / 2, L.end());
    };
    const auto clean = through(0.0f, 1.0f, 0, 0.3f);
    CHECK(goertzelDb(clean, 2 * f0) < goertzelDb(clean, f0) - 80.0, "with Drive at 0 the block passes the sine as it is");
    const auto soft = through(0.6f, 1.0f, 0, 0.3f), asym = through(0.6f, 1.0f, 1, 0.3f), hard = through(0.6f, 1.0f, 2, 0.3f);
    const double f1s = goertzelDb(soft, f0), f1a = goertzelDb(asym, f0);
    CHECK(goertzelDb(soft, 3 * f0) > f1s - 30.0, "Soft makes odd harmonics (3rd at %.1f dB below the fundamental)", f1s - goertzelDb(soft, 3 * f0));
    CHECK(goertzelDb(soft, 2 * f0) < f1s - 55.0, "and next to no even ones (2nd %.1f dB down)", f1s - goertzelDb(soft, 2 * f0));
    CHECK(goertzelDb(asym, 2 * f0) > f1a - 30.0, "Asym makes even harmonics (2nd at %.1f dB below)", f1a - goertzelDb(asym, 2 * f0));
    CHECK(goertzelDb(hard, 3 * f0) > goertzelDb(hard, f0) - 25.0, "Hard clips (3rd %.1f dB below)", goertzelDb(hard, f0) - goertzelDb(hard, 3 * f0));
    double rms0 = 0, rms1 = 0;
    for (size_t i = 0; i < clean.size(); i++) { rms0 += (double) clean[i] * clean[i]; rms1 += (double) soft[i] * soft[i]; }
    CHECK(std::fabs(10 * std::log10(rms1 / rms0)) < 6.0, "the level stays near where it was (%+.1f dB)", 10 * std::log10(rms1 / rms0));
    const auto dry = through(0.9f, 0.0f, 0, 0.3f);
    double worst = 0;
    for (size_t i = 0; i < dry.size(); i++) worst = std::max(worst, (double) std::fabs(dry[i] - clean[i]));
    CHECK(worst < 1e-6, "Mix at 0 is the dry signal (largest difference %.1e)", worst);

    /* switched on mid-note it does not step, and FX Enable takes it out */
    auto jumpIn = [](const std::vector<int16_t> &x, int from, int to) {
        int m = 0;
        for (int i = from * 128 + 1; i < to * 128; i++) m = std::max(m, std::abs((int) x[(size_t) i * 2] - (int) x[(size_t) (i - 1) * 2]));
        return m;
    };
    Engine e;
    e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127; e.pots[TB_P_VOLUME] = 50;
    playChord(e); renderBlocks(e, 40);
    std::vector<int16_t> all;
    for (int blk = 0; blk < 200; blk++) {
        if (blk == 100) e.pots[TB_P_DIST_DRIVE] = 100;
        auto b = renderBlocks(e, 1);
        all.insert(all.end(), b.begin(), b.end());
    }
    const int pre = jumpIn(all, 60, 100), post = jumpIn(all, 130, 200), win = jumpIn(all, 100, 130);
    CHECK(win < 2 * std::max(pre, post) + 400, "switching distortion on mid-note adds no step (window %d vs steady %d/%d)", win, pre, post);
    Engine off, ref;
    for (Engine *x : { &off, &ref }) { x->pots[TB_P_VCA_A] = 0; x->pots[TB_P_VCA_S] = 127; }
    off.pots[TB_P_DIST_DRIVE] = 100; off.pots[TB_P_FX_ON] = 0;
    playChord(off); playChord(ref);
    renderBlocks(off, 40); renderBlocks(ref, 40);
    CHECK(renderBlocks(off, 30) == renderBlocks(ref, 30), "FX Enable off takes the distortion out");

    /* The calibrated law: out = level (1 - s^2) x + s out curve(gain x) */
    auto cal = [&](float gain, float out, float level, float s, int mode, float amp) {
        FxSettings st;
        st.distCal = true; st.distGain = gain; st.distOut = out; st.distLevel = level; st.distMix = s; st.distMode = mode;
        Fx fx(SR);
        fx.set(st);
        const int n = (int) SR;
        std::vector<float> L((size_t) n), R((size_t) n);
        for (int i = 0; i < n; i++) L[(size_t) i] = R[(size_t) i] = (float) (amp * std::sin(6.283185307 * f0 * i / SR));
        run(fx, L, R);
        return std::vector<float>(L.begin() + n / 2, L.end());
    };
    const auto dryCal = cal(5.0f, 1.0f, 1.0f, 0.0f, 0, 0.3f);
    double dd = 0;
    for (size_t i = 0; i < dryCal.size(); i++) dd = std::max(dd, (double) std::fabs(dryCal[i] - clean[i]));
    CHECK(dd < 1e-5, "calibrated, s = 0 and level 1: the dry signal (largest difference %.1e)", dd);
    const auto lin = cal(0.05f, 2.0f, 1.0f, 1.0f, 0, 0.3f);                       /* tanh(0.05 x) is linear: the output gain over the gain */
    CHECK(std::fabs(goertzelDb(lin, f0) - goertzelDb(clean, f0) - 20 * std::log10(0.1)) < 0.2, "calibrated, a small gain: the fundamental is gain x out (%.2f dB)", goertzelDb(lin, f0) - goertzelDb(clean, f0));
    const auto lv = cal(0.05f, 2.0f, 2.0f, 1.0f, 0, 0.3f);
    CHECK(std::fabs(goertzelDb(lv, f0) - goertzelDb(lin, f0) - 6.02) < 0.1, "Level is a gain on the sum (%.2f dB for x2)", goertzelDb(lv, f0) - goertzelDb(lin, f0));
    const auto half = cal(0.05f, 2.0f, 1.0f, 0.5f, 0, 0.3f);                      /* (1 - 0.25) + 0.5 * 0.1 = 0.8 of the dry */
    CHECK(std::fabs(goertzelDb(half, f0) - goertzelDb(clean, f0) - 20 * std::log10(0.8)) < 0.2, "Wet is a smoothstep-style mix: dry part 1 - s^2 (%.2f dB)", goertzelDb(half, f0) - goertzelDb(clean, f0));
    const auto sine = cal(30.0f, 0.3f, 1.0f, 1.0f, 3, 0.3f);
    CHECK(goertzelDb(sine, 3 * f0) > goertzelDb(sine, f0) - 25.0, "the sine curve folds: odd harmonics (3rd %.1f dB below)", goertzelDb(sine, f0) - goertzelDb(sine, 3 * f0));
    const auto asymC = cal(8.0f, 0.3f, 1.0f, 1.0f, 1, 0.3f);
    CHECK(goertzelDb(asymC, 2 * f0) > goertzelDb(asymC, f0) - 40.0, "the biased curve makes even harmonics (2nd %.1f dB below)", goertzelDb(asymC, f0) - goertzelDb(asymC, 2 * f0));

    /* The pre-filter sits ahead of the curve, on the wet part: a high-pass above the tone leaves the clipper nothing to clip, a low-pass above it changes nothing. */
    auto preCal = [&](int type, float hz) {
        FxSettings st;
        st.distCal = true; st.distGain = 30.0f; st.distOut = 0.3f; st.distLevel = 1.0f; st.distMix = 1.0f; st.distMode = 0;
        st.distPre = type; st.distPreHz = hz; st.distPreQ = 0.7071f;
        Fx fx(SR);
        fx.set(st);
        const int n = (int) SR;
        std::vector<float> L((size_t) n), R((size_t) n);
        for (int i = 0; i < n; i++) L[(size_t) i] = R[(size_t) i] = (float) (0.3 * std::sin(6.283185307 * f0 * i / SR));
        run(fx, L, R);
        return std::vector<float>(L.begin() + n / 2, L.end());
    };
    const auto clipped = cal(30.0f, 0.3f, 1.0f, 1.0f, 0, 0.3f), hp = preCal(2, 8.0f * f0), lp = preCal(1, 8.0f * f0);
    CHECK(goertzelDb(hp, f0) < goertzelDb(clipped, f0) - 15.0, "a pre-filter high-pass above the tone pulls the tone out of the curve (%.1f dB down)", goertzelDb(clipped, f0) - goertzelDb(hp, f0));
    CHECK(std::fabs(goertzelDb(lp, f0) - goertzelDb(clipped, f0)) < 0.5, "a pre-filter low-pass above the tone leaves it alone (%.2f dB)", goertzelDb(lp, f0) - goertzelDb(clipped, f0));
}

/* The Filter FX: a filter right after the distortion, its cutoff set from outside each block. */
static double filterGainDb(int type, float hz, float q, double sineHz)
{
    Fx fx(SR);
    fx.set(FxSettings());
    fx.setFilter(true, type, hz, q);
    const int n = (int) SR;
    std::vector<float> L((size_t) n), R((size_t) n);
    for (int i = 0; i < n; i++) L[(size_t) i] = R[(size_t) i] = (float) (0.1 * std::sin(6.283185307 * sineHz * i / SR));
    run(fx, L, R);
    double a = 0, b = 0;
    for (int i = n / 2; i < n; i++) { a += 0.01 * 0.5; b += (double) L[(size_t) i] * L[(size_t) i]; }
    return 10.0 * std::log10(b / a);
}

static void testFxFilter()
{
    Fx off(SR);
    off.set(FxSettings());
    CHECK(!off.active(), "no Filter FX: the block is a bypass");
    Fx on(SR);
    on.set(FxSettings());
    on.setFilter(true, 1, 1000.0f, 0.7071f);
    CHECK(on.active(), "a Filter FX alone keeps the block running");
    on.setFilter(false, 1, 1000.0f, 0.7071f);
    CHECK(!on.active(), "and switching it off lets the block skip again");
    const double pass = filterGainDb(1, 1000.0f, 0.7071f, 100.0), stop = filterGainDb(1, 1000.0f, 0.7071f, 4000.0);
    CHECK(std::fabs(pass) < 0.5 && stop < -30.0, "a 24 dB low-pass passes below and cuts above its cutoff (%.1f / %.1f dB)", pass, stop);
    const double lo = filterGainDb(2, 1000.0f, 0.7071f, 100.0), hi = filterGainDb(2, 1000.0f, 0.7071f, 4000.0);
    CHECK(lo < -20.0 && std::fabs(hi) < 0.5, "a high-pass does the opposite (%.1f / %.1f dB)", lo, hi);
    const double moved = filterGainDb(1, 8000.0f, 0.7071f, 4000.0);
    CHECK(std::fabs(moved) < 1.0, "the cutoff can be moved from block to block: at 8 kHz the 4 kHz tone passes (%.1f dB)", moved);
}

/* Chorus, Flanger and Phaser: bypass when off, the wet levels they were measured at, and what each does to a noise burst. */
static double noisePowerDb(Fx &fx, std::vector<float> &L, std::vector<float> &R, double *corr = nullptr)
{
    uint32_t seed = 2463534242u;
    const int n = (int) (2.0f * SR);
    L.assign((size_t) n, 0.0f); R.assign((size_t) n, 0.0f);
    for (int i = 0; i < n; i++) {                      /* xorshift32: white enough for a correlation (rnd() above is not) */
        seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5;
        L[(size_t) i] = R[(size_t) i] = 0.2f * ((float) (seed >> 8) / 8388608.0f - 1.0f);
    }
    double in = 0;
    for (int i = n / 2; i < n; i++) in += (double) L[(size_t) i] * L[(size_t) i];
    run(fx, L, R);
    double out = 0, lr = 0, ll = 0, rr = 0;
    for (int i = n / 2; i < n; i++) {
        out += (double) L[(size_t) i] * L[(size_t) i];
        lr += (double) L[(size_t) i] * R[(size_t) i]; ll += (double) L[(size_t) i] * L[(size_t) i]; rr += (double) R[(size_t) i] * R[(size_t) i];
    }
    if (corr) *corr = lr / std::sqrt(ll * rr + 1e-30);
    return 10.0 * std::log10(out / in);
}

static void testModFx()
{
    std::vector<float> L, R;
    {
        Fx fx(SR);
        fx.set(FxSettings());
        CHECK(!fx.active(), "no modulated effect: nothing to do");
    }
    {   /* a mix of 0 is a bypass */
        FxSettings s;
        s.chorRate = 1.0f; s.flanRate = 1.0f; s.phasRate = 1.0f;
        Fx fx(SR);
        fx.set(s);
        CHECK(!fx.active(), "mixes at 0: the block is skipped");
    }
    {   /* chorus wet 100: four voices at -10 dB re the input, left and right decorrelated */
        FxSettings s;
        s.chorMix = 1.0f; s.chorDepth = 0.5f; s.chorRate = 1.0f;
        Fx fx(SR);
        fx.set(s);
        double corr = 0;
        const double db = noisePowerDb(fx, L, R, &corr);
        CHECK(std::fabs(db + 5.5) < 1.5, "chorus at wet 100 with the filter open is %.1f dB re the input (Serum's four voices: -3.5 dB per band)", db);
        CHECK(std::fabs(corr) < 0.3, "and its sides are decorrelated (%.2f)", corr);
        CHECK(fx.active(), "chorus: block runs");
    }
    {   /* chorus at wet 50 keeps most of the dry: -2.3 dB in Serum */
        FxSettings s;
        s.chorMix = 0.5f; s.chorDepth = 0.5f; s.chorRate = 1.0f;
        Fx fx(SR);
        fx.set(s);
        const double db = noisePowerDb(fx, L, R);
        CHECK(std::fabs(db + 1.0) < 1.5, "chorus at wet 50 is %.1f dB re the input", db);
    }
    {   /* flanger wet 100 passes the signal level with the input, and combs it */
        FxSettings s;
        s.flanMix = 1.0f; s.flanDepth = 0.5f; s.flanRate = 0.5f;
        Fx fx(SR);
        fx.set(s);
        const double db = noisePowerDb(fx, L, R);
        CHECK(std::fabs(db + 1.5) < 1.5, "flanger at wet 100 is %.1f dB re the input (Serum -1.5)", db);
    }
    {   /* phaser wet 100: all-pass chain summed with the dry */
        FxSettings s;
        s.phasMix = 1.0f; s.phasDepth = 0.5f; s.phasRate = 0.5f; s.phasPoles = 6;
        Fx fx(SR);
        fx.set(s);
        const double db = noisePowerDb(fx, L, R);
        CHECK(db > -1.5 && db < 2.5, "phaser at wet 100 is %.1f dB re the input (Serum about +1)", db);
        for (size_t i = 0; i < L.size(); i++) if (!std::isfinite(L[i]) || std::fabs(L[i]) > 5.0f) { CHECK(false, "phaser stays finite and bounded"); break; }
    }
    {   /* feedback is stable at its limit */
        FxSettings s;
        s.chorMix = s.flanMix = s.phasMix = 1.0f;
        s.chorFb = s.flanFb = 0.8f; s.phasFb = 0.8f; s.phasPoles = 12;
        Fx fx(SR);
        fx.set(s);
        noisePowerDb(fx, L, R);
        float peak = 0;
        for (size_t i = 0; i < L.size(); i++) peak = std::max(peak, std::fabs(L[i]));
        CHECK(std::isfinite(peak) && peak < 20.0f, "all three at maximum feedback stay bounded (peak %.2f)", peak);
    }
}

static void testComp()
{
    printf("== compressor ==\n");
    const double f0 = 220.0;
    auto rmsDb = [](const std::vector<float> &x) {
        double a = 0; for (float v : x) a += (double) v * v;
        return 10 * std::log10(a / (double) x.size() + 1e-20);
    };
    auto through = [&](const FxSettings &s, float amp, int seconds = 1) {
        Fx fx(SR);
        fx.set(s);
        const int n = (int) SR * seconds;
        std::vector<float> L((size_t) n), R((size_t) n);
        for (int i = 0; i < n; i++) L[(size_t) i] = R[(size_t) i] = (float) (amp * std::sin(6.283185307 * f0 * i / SR));
        run(fx, L, R);
        return std::vector<float>(L.begin() + n / 2, L.end());
    };
    FxSettings c;
    c.compMix = 1; c.compThreshDb = -20; c.compRatio = 4; c.compAttackMs = 1; c.compReleaseMs = 50;
    const float quiet = 0.03f, loud = 0.5f;                    /* -30.5 and -6 dB peak; the detector reads rms + 4.77 dB */
    const double inQ = rmsDb(through(FxSettings(), quiet)), inL = rmsDb(through(FxSettings(), loud));
    const double dq = rmsDb(through(c, quiet)) - inQ, dl = rmsDb(through(c, loud)) - inL;
    CHECK(std::fabs(dq) < 0.3, "below the threshold the level is untouched (%+.2f dB)", dq);
    const double expect = -(1.0 - 1.0 / 4.0) * (20 * std::log10((double) loud) - 3.01 + 4.77 - -20.0);
    CHECK(std::fabs(dl - expect) < 1.0, "above it the level is pulled down by (1 - 1/ratio) of the excess (%+.1f dB, expected %+.1f)", dl, expect);
    FxSettings off = c; off.compRatio = 1;
    CHECK(!Fx(SR).active() && std::fabs(rmsDb(through(off, loud)) - inL) < 1e-3, "ratio 1 is a bypass");
    FxSettings mk = c; mk.compMakeupDb = 6;
    CHECK(std::fabs(rmsDb(through(mk, quiet)) - inQ - 6.0) < 0.3, "makeup adds its dB");
    FxSettings half = c; half.compMix = 0.5f;
    const double dh = rmsDb(through(half, loud)) - inL;
    CHECK(dh > dl + 0.5 && dh < 0.0, "mix blends the compressed and the dry signal (%+.1f dB between %+.1f and 0)", dh, dl);
    /* release: after a loud passage the gain comes back over about the release time */
    Fx fx(SR);
    fx.set(c);
    std::vector<float> L((size_t) (SR * 2)), R(L.size()), X(L.size());
    for (size_t i = 0; i < L.size(); i++) {
        const float amp = i < (size_t) SR ? loud : 0.05f;
        X[i] = L[i] = R[i] = (float) (amp * std::sin(6.283185307 * f0 * (double) i / SR));
    }
    run(fx, L, R);
    auto gainDb = [&](double t0) {
        const size_t a = (size_t) (t0 * SR), b = a + (size_t) (0.01 * SR);
        return rmsDb(std::vector<float>(L.begin() + (long) a, L.begin() + (long) b)) - rmsDb(std::vector<float>(X.begin() + (long) a, X.begin() + (long) b));
    };
    CHECK(gainDb(1.005) < -1.0 && std::fabs(gainDb(1.9)) < 0.3, "the gain recovers after the passage (%+.1f dB just after, %+.2f dB 0.9 s later)", gainDb(1.005), gainDb(1.9));
    /* multiband: flat when its gains are 0, a band's gain moves only that band, and the floor lifts quiet material */
    {
        auto tone = [&](double f, const FxSettings &s, float amp) {
            Fx fx2(SR);
            fx2.set(s);
            const int n2 = (int) SR;
            std::vector<float> L2((size_t) n2), R2((size_t) n2);
            for (int i = 0; i < n2; i++) L2[(size_t) i] = R2[(size_t) i] = (float) (amp * std::sin(6.283185307 * f * i / SR));
            run(fx2, L2, R2);
            return rmsDb(std::vector<float>(L2.begin() + n2 / 2, L2.end())) - 20 * std::log10((double) amp / std::sqrt(2.0));
        };
        FxSettings m;
        m.compMix = 1; m.compMulti = true; m.compRatio = 1; m.compAttackMs = 1; m.compReleaseMs = 50;
        for (int b = 0; b < 3; b++) m.compThreshBandDb[b] = 20;                /* never compresses */
        const double f50 = tone(50, m, 0.1f), f1k = tone(1000, m, 0.1f), f8k = tone(8000, m, 0.1f), f200 = tone(200, m, 0.1f), f2k4 = tone(2400, m, 0.1f);
        CHECK(std::fabs(f50) < 0.5 && std::fabs(f1k) < 0.5 && std::fabs(f8k) < 0.5 && std::fabs(f200) < 0.5, "three bands with 0 dB gains sum flat (%+.2f %+.2f %+.2f %+.2f dB)", f50, f200, f1k, f8k);
        CHECK(std::fabs(f2k4) < 1.0, "including at the upper crossover (%+.2f dB)", f2k4);
        FxSettings g = m; g.compBandGainDb[0] = 12;
        const double a50 = tone(50, g, 0.1f), a1k = tone(1000, g, 0.1f), a8k = tone(8000, g, 0.1f);
        CHECK(std::fabs(a50 - 12) < 0.7 && std::fabs(a1k) < 0.5 && std::fabs(a8k) < 0.5, "the low band's gain moves the low band only (%+.1f %+.1f %+.1f dB)", a50, a1k, a8k);
        FxSettings h = m; h.compBandGainDb[2] = -12;
        CHECK(std::fabs(tone(8000, h, 0.1f) + 12) < 0.7 && std::fabs(tone(1000, h, 0.1f)) < 0.5, "and the high band's moves the high band only");
        FxSettings u = m; u.compRatio = 4; u.compBelow = 1; u.compKneeDb = 6;
        for (int b = 0; b < 3; b++) u.compThreshBandDb[b] = -30;               /* floor at -41 dB */
        const double lift = tone(1000, u, 0.003f);                              /* about -53 dB rms */
        CHECK(lift > 6.0, "Below pulls a quiet band up toward the floor (%+.1f dB)", lift);
        const double loudDown = tone(1000, u, 0.5f);
        CHECK(loudDown < -3.0, "and the same setting still pulls a loud one down (%+.1f dB)", loudDown);
    }
    /* switches */
    Engine e1, e2;
    for (Engine *x : { &e1, &e2 }) { x->pots[TB_P_VCA_A] = 0; x->pots[TB_P_VCA_S] = 127; x->pots[TB_P_COMP_RATIO] = 100; x->pots[TB_P_COMP_THRESH] = 40; x->pots[TB_P_COMP_MIX] = 127; }
    e2.pots[TB_P_COMP_ON] = 0;
    playChord(e1); playChord(e2);
    renderBlocks(e1, 40); renderBlocks(e2, 40);
    Engine e3; e3.pots[TB_P_VCA_A] = 0; e3.pots[TB_P_VCA_S] = 127; playChord(e3); renderBlocks(e3, 40);
    CHECK(renderBlocks(e2, 20) == renderBlocks(e3, 20), "Comp Enable off takes the compressor out");
    CHECK(renderBlocks(e1, 20) != renderBlocks(e3, 20), "and on, it changes the sound");
}

/* The LFOs: rate, shape, routing, Trig against Free */
static void testLfo()
{
    printf("== lfo ==\n");
    /* the level of the held note in windows of 5 blocks (14.5 ms): the LFO shows as the level swinging */
    auto levels = [](Engine &e, int windows) {
        std::vector<double> out;
        for (int w = 0; w < windows; w++) {
            const auto x = renderBlocks(e, 5);
            out.push_back(tailRms(x, 0));
        }
        return out;
    };
    auto setup = [](Engine &e, bool routed, float ratePot) {
        e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127; e.pots[TB_P_WT1_LEVEL] = routed ? 0 : 100;     /* routed: the LFO is all the level there is */
        if (routed) {
            e.pots[TB_P_LFO1_RATE] = ratePot;
            e.pots[TB_P_LR1_SRC] = 1; e.pots[TB_P_LR1_DST] = 3; e.pots[TB_P_LR1_AMT] = 127;     /* LFO1 -> WT1 Level, full */
        }
    };
    auto depth = [](const std::vector<double> &v) {
        double lo = 1e18, hi = 0;
        for (size_t i = 10; i < v.size(); i++) { lo = std::min(lo, v[i]); hi = std::max(hi, v[i]); }
        return hi / std::max(lo, 1e-9);
    };

    Engine flat, swung;
    setup(flat, false, 0); setup(swung, true, 75);                         /* pot 75 is about 5 Hz */
    playChord(flat); playChord(swung);
    const auto vf = levels(flat, 100), vs = levels(swung, 100);
    CHECK(depth(vf) < 1.3, "with no route the level is steady (swing x%.2f)", depth(vf));
    CHECK(depth(vs) > 3.0, "an LFO routed to a level makes it swing (x%.1f)", depth(vs));
    {   /* Smooth: a one-pole on the LFO's output; a long time constant (pot 100 is about 0.1 s) flattens the 5 Hz swing */
        Engine sm;
        setup(sm, true, 75); sm.pots[TB_P_LFO1_SMOOTH] = 100;
        playChord(sm);
        const auto vm = levels(sm, 100);
        CHECK(depth(vm) < 0.5 * depth(vs), "LFO Smooth flattens the swing (x%.1f against x%.1f)", depth(vm), depth(vs));
    }

    /* the period: the level's autocorrelation peaks one LFO cycle (0.2 s = about 14 windows) away */
    {
        std::vector<double> d(vs.begin() + 10, vs.end());
        double mean = 0; for (double v : d) mean += v; mean /= (double) d.size();
        for (double &v : d) v -= mean;
        int best = 0; double bv = -1e18;
        for (int lag = 8; lag <= 24; lag++) {
            double a = 0; for (size_t i = 0; i + (size_t) lag < d.size(); i++) a += d[i] * d[i + (size_t) lag];
            if (a > bv) { bv = a; best = lag; }
        }
        const double period = best * 0.0145;
        CHECK(std::fabs(period - 0.2) < 0.045, "at the rate asked for (one cycle %.3f s against 0.200)", period);
    }

    /* Trig restarts the shape on every note: the same note struck at different times swings the same way. Free joins the
     * running cycle, so it does not. */
    auto struck = [&](float mode, int idle) {
        Engine e;
        setup(e, true, 75);
        e.pots[TB_P_LFO1_MODE] = mode;
        renderBlocks(e, idle);
        playChord(e);
        return levels(e, 12);
    };
    auto same = [](const std::vector<double> &a, const std::vector<double> &b) {
        double worst = 0;
        for (size_t i = 0; i < a.size(); i++) worst = std::max(worst, std::fabs(a[i] - b[i]) / std::max(std::max(a[i], b[i]), 1.0));
        return worst;
    };
    const auto t1 = struck(0, 5), t2 = struck(0, 22), f1 = struck(1, 5), f2 = struck(1, 22);
    CHECK(same(t1, t2) < 0.12, "Trig: a note struck at another time swings the same way (worst difference %.0f %%)", 100 * same(t1, t2));
    CHECK(same(f1, f2) > 0.25, "Free: it joins the running cycle instead (worst difference %.0f %%)", 100 * same(f1, f2));

    /* Bi swings around the base instead of only up: with the base level at 0, Uni gives a level, Bi gives half the cycle */
    Engine uni, bi;
    for (Engine *e : { &uni, &bi }) {
        setup(*e, true, 75);
        e->pots[TB_P_WT1_LEVEL] = 60;
        e->pots[TB_P_LR1_AMT] = 96;                                         /* half amount */
    }
    bi.pots[TB_P_LR1_BIP] = 1;
    playChord(uni); playChord(bi);
    const auto vu = levels(uni, 100), vb = levels(bi, 100);
    double mu = 0, mb = 0;
    for (size_t i = 10; i < vu.size(); i++) { mu += vu[i]; mb += vb[i]; }
    CHECK(mb < mu * 0.9, "Bi swings below the base as well, so it is quieter than Uni on average (%.0f against %.0f)", mb / 90, mu / 90);
}

int main()
{
    testBypass();
    testEq();
    testMidSideEq();
    testDelay();
    testReverb();
    testWide();
    testLaws();
    testStress();
    testEngine();
    testSwitches();
    testTone();
    testStereo();
    testDist();
    testComp();
    testFxFilter();
    testModFx();
    testLfo();
    printf("\n%s (%d failures)\n", g_fail ? "FX TESTS FAILED" : "FX TESTS PASSED", g_fail);
    return g_fail ? 1 : 0;
}
