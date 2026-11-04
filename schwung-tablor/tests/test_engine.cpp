/* Engine-level checks: what the third oscillator, wide unison and sync do once
 * they are behind a Voice and the pot array. Like test_dsp.cpp these assert
 * measured behaviour. Runs natively in the build container.
 */

#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <vector>

#include "../src/dsp/engine.h"

using namespace tb;

static int g_fail = 0;
#define CHECK(cond, ...) do { \
    if (!(cond)) { g_fail++; printf("FAIL: " __VA_ARGS__); printf("\n"); } \
    else         {           printf("  ok: " __VA_ARGS__); printf("\n"); } \
} while (0)

static std::shared_ptr<Wavetable> sineTable()
{
    std::vector<float> s(2048);
    for (int i = 0; i < 2048; i++)
        s[(size_t) i] = std::sin(2.0f * (float) M_PI * (float) i / 2048.0f);
    auto wt = std::make_shared<Wavetable>();
    wtBuild(*wt, s.data(), 1, 2048, 44100.0f, 44100.0f);
    return wt;
}

static void note(Engine &e, int n, int vel = 100)
{
    const uint8_t on[] = { 0x90, (uint8_t) n, (uint8_t) vel };
    e.onMidi(on, 3);
}

static std::vector<int16_t> render(Engine &e, int blocks)
{
    std::vector<int16_t> out((size_t) blocks * 256);
    for (int b = 0; b < blocks; b++)
        e.renderBlock(out.data() + (size_t) b * 256, 128);
    return out;
}

/* Left channel, from the second half so the envelope has settled. */
static double heardHz(const std::vector<int16_t> &x)
{
    const int frames = (int) x.size() / 2;
    int crossings = 0;
    for (int i = frames / 2 + 1; i < frames; i++)
        if (x[(size_t) (i - 1) * 2] <= 0 && x[(size_t) i * 2] > 0) crossings++;
    return crossings * 44100.0 / (frames - frames / 2 - 1);
}

static double rms(const std::vector<int16_t> &x)
{
    double acc = 0.0;
    const int frames = (int) x.size() / 2;
    for (int i = frames / 2; i < frames; i++)
        acc += (double) x[(size_t) i * 2] * x[(size_t) i * 2];
    return std::sqrt(acc / (frames - frames / 2));
}

/* Only WT3 sounding: an instant, open patch on one oscillator. */
static void soloWt3(Engine &e)
{
    e.pots[TB_P_WT1_LEVEL] = 0;
    e.pots[TB_P_WT3_LEVEL] = 100;
    e.pots[TB_P_VCA_A] = 0;
    e.pots[TB_P_VCA_S] = 127;
}

static void testThirdOscillator()
{
    printf("== third oscillator ==\n");
    auto sine = sineTable();
    {
        Engine e;
        CHECK((int) e.pots[TB_P_WT3_LEVEL] == 0, "WT3 is silent by default, so old presets are unchanged");
        note(e, 69);
        render(e, 8);
        CHECK(e.activeStreams() == 1, "a default note runs one stream (%d)", e.activeStreams());
    }
    {
        Engine e;
        soloWt3(e);
        e.setTable(2, sine);
        note(e, 69);
        auto a = render(e, 700);
        const double hz = heardHz(a);
        CHECK(hz > 430.0 && hz < 450.0, "WT3 alone plays its own table at pitch (%.1f Hz)", hz);
        CHECK(rms(a) > 1000.0, "WT3 alone is audible (rms %.0f)", rms(a));
    }
    {
        Engine e;
        soloWt3(e);
        e.setTable(2, sine);
        e.pots[TB_P_WT3_TUNE] = 12;
        note(e, 69);
        const double hz = heardHz(render(e, 700));
        CHECK(hz > 860.0 && hz < 900.0, "WT3 Tune moves it an octave (%.1f Hz)", hz);
    }
    {
        /* its table is its own: changing WT1's must not reach it */
        Engine a, b;
        soloWt3(a); soloWt3(b);
        a.setTable(2, sine); b.setTable(2, sine);
        b.setTable(0, makeInitTable());
        srand(7); note(a, 60); auto ra = render(a, 100);
        srand(7); note(b, 60); auto rb = render(b, 100);
        CHECK(ra == rb, "WT3 does not read WT1's table");
    }
    {
        /* an envelope aimed at WT3 Level brings it up from a zero pot */
        Engine e;
        e.pots[TB_P_WT1_LEVEL] = 0;
        e.pots[TB_P_WT3_LEVEL] = 0;
        e.pots[TB_P_VCA_A] = 0;
        e.pots[TB_P_VCA_S] = 127;
        e.pots[TB_P_ME1_DST] = MD_WT3_LEVEL;
        e.pots[TB_P_ME1_AMT] = 127;
        e.pots[TB_P_ME1_S] = 127;
        e.setTable(2, sine);
        note(e, 69);
        auto a = render(e, 400);
        CHECK(rms(a) > 1000.0, "EG -> WT3 Level opens it (rms %.0f)", rms(a));
    }
}

/* The option list the host draws and the index the DSP acts on are one contract. */
static void testDestinationOrder()
{
    printf("== mod destination order ==\n");
    const tb_param_t &p = tb_params[TB_P_ME1_DST];
    CHECK(p.n_options == MD_COUNT, "one option per destination (%d / %d)", p.n_options, (int) MD_COUNT);
    struct { int d; const char *name; } want[] = {
        { MD_NONE, "None" }, { MD_WT1_POS, "WT1 Pos" }, { MD_AMP, "Amp" },   /* indices saved presets rely on */
        { MD_WT3_POS, "WT3 Pos" }, { MD_WT3_LEVEL, "WT3 Level" },
        { MD_WT1_SYNC, "WT1 Sync" }, { MD_WT2_SYNC, "WT2 Sync" }, { MD_WT3_SYNC, "WT3 Sync" },
    };
    for (const auto &w : want)
        CHECK(!strcmp(p.options[w.d], w.name), "option %d is %s (%s)", w.d, w.name, p.options[w.d]);
    CHECK(MD_AMP == 17, "old destinations kept their index (Amp = %d)", (int) MD_AMP);
}

static void testStreamBudget()
{
    printf("== stream budget ==\n");
    auto widest = [](Engine &e) {
        for (int k : { (int) TB_P_WT1_UNI, (int) TB_P_WT2_UNI, (int) TB_P_WT3_UNI }) e.pots[k] = 16;
        e.pots[TB_P_WT2_LEVEL] = 100;
        e.pots[TB_P_WT3_LEVEL] = 100;
    };
    const int chord[8] = { 36, 43, 48, 55, 60, 64, 67, 72 };

    for (int voices : { 8, 4, 2, 1 }) {
        Engine e;
        widest(e);
        e.pots[TB_P_VOICES] = (float) voices;
        for (int n : chord) note(e, n);
        render(e, 4);
        int s = e.activeStreams();
        CHECK(s <= kStreamBudget, "%d voices, 3 x 16: %d streams, in budget", voices, s);
        CHECK(s >= kStreamBudget * 3 / 4 || voices == 1,
              "and it is spent, not wasted (%d of %d)", s, kStreamBudget);
    }
    {
        Engine e;
        widest(e);
        e.pots[TB_P_VOICE_MODE] = 1;
        for (int n : chord) note(e, n);
        render(e, 4);
        CHECK(e.activeStreams() == 48, "mono gets its whole stack (%d of 48)", e.activeStreams());
    }
    {
        /* the patches this exists for fit whole */
        Engine e;
        e.pots[TB_P_VOICES] = 4;
        e.pots[TB_P_WT2_LEVEL] = 100;
        e.pots[TB_P_WT1_UNI] = 7;
        e.pots[TB_P_WT2_UNI] = 7;
        for (int i = 0; i < 4; i++) note(e, chord[i]);
        render(e, 4);
        CHECK(e.activeStreams() == 56, "a 4-note chord of 7+7 is not touched (%d of 56)", e.activeStreams());
    }
    {
        /* Turning Voices down under a held chord leaves every note sounding,
         * and the budget has to hold through that as well. */
        Engine e;
        widest(e);
        for (int n : chord) note(e, n);
        render(e, 4);
        e.pots[TB_P_VOICES] = 1;
        render(e, 4);
        CHECK(e.activeStreams() <= kStreamBudget,
              "Voices turned down under a chord stays in budget (%d)", e.activeStreams());
    }
    {
        /* A second note must not change the first one's unison: a stack that
         * changes size mid-note is a level step. */
        Engine e;
        e.pots[TB_P_WT1_UNI] = 16;
        e.pots[TB_P_VOICES] = 4;
        note(e, 48);
        render(e, 4);
        const int one = e.activeStreams();
        note(e, 55);
        render(e, 4);
        CHECK(e.activeStreams() == one * 2, "a second note leaves the first stack alone (%d -> %d)",
              one, e.activeStreams());
    }
    {
        /* Every oscillator that is on survives the squeeze, however lopsided. */
        Engine e;
        e.pots[TB_P_WT1_UNI] = 16;
        e.pots[TB_P_WT2_LEVEL] = 100;
        e.pots[TB_P_WT3_LEVEL] = 100;
        for (int n : chord) note(e, n);
        render(e, 4);
        CHECK(e.activeStreams() <= kStreamBudget && e.activeStreams() >= 8 * 3,
              "a lopsided 16+1+1 keeps all three oscs (%d streams)", e.activeStreams());
    }
    {
        /* An oscillator's on/off follows its routing, so an envelope passing
         * through zero cannot make the others jump. */
        Engine e;
        e.pots[TB_P_WT1_UNI] = 16;
        e.pots[TB_P_WT2_UNI] = 16;
        e.pots[TB_P_WT2_LEVEL] = 0;
        e.pots[TB_P_ME1_DST] = MD_WT2_LEVEL;
        e.pots[TB_P_ME1_AMT] = 127;
        e.pots[TB_P_ME1_A] = 0;
        e.pots[TB_P_ME1_D] = 70;
        e.pots[TB_P_ME1_S] = 0;
        e.pots[TB_P_VCA_S] = 127;
        note(e, 48);
        render(e, 4);
        int early = e.activeStreams();
        render(e, 300);
        int late = e.activeStreams();
        /* 8 voices share: 8 streams, 4 + 4 while WT2 sounds and still 4 after */
        CHECK(early == 8 && late == 4, "WT1 keeps its share as WT2's level falls (%d -> %d)", early, late);
    }
}

/* A note an octave below the old +/-24 limit, and the same reach on the sub. */
static void testTuneRange()
{
    printf("== tune range ==\n");
    auto sine = sineTable();
    {
        Engine e;
        e.setTable(0, sine);
        e.pots[TB_P_VCA_A] = 0;
        e.pots[TB_P_VCA_S] = 127;
        e.pots[TB_P_WT1_TUNE] = -36;
        note(e, 69);
        const double hz = heardHz(render(e, 2000));
        CHECK(hz > 53.0 && hz < 57.0, "WT1 at -36 plays 3 octaves down (%.1f Hz for 55)", hz);
    }
    {
        Engine e;
        e.pots[TB_P_WT1_LEVEL] = 0;
        e.pots[TB_P_SUB_LEVEL] = 127;
        e.pots[TB_P_SUB_TUNE] = -48;
        e.pots[TB_P_VCA_A] = 0;
        e.pots[TB_P_VCA_S] = 127;
        note(e, 81);
        const double hz = heardHz(render(e, 2000));
        CHECK(hz > 53.0 && hz < 57.0, "sub at -48 plays 4 octaves down (%.1f Hz for 55)", hz);
    }
    CHECK(tb_params[TB_P_WT3_TUNE].min == -48 && tb_params[TB_P_WT3_TUNE].max == 48, "every oscillator reaches +/-48");
}

static void testSoftClipper()
{
    printf("== soft clipper ==\n");
    bool linear = true, bounded = true, monotonic = true, smooth = true;
    float prev = softLimit(-4.0f), prevSlope = -1.0f;
    for (float x = -4.0f; x <= 4.0f; x += 0.0005f) {
        const float y = softLimit(x);
        if (std::fabs(x) <= kLimiterKnee && y != x) linear = false;
        if (std::fabs(y) > 1.0f) bounded = false;
        if (y < prev - 1e-6f) monotonic = false;      /* ARM's fused multiplies round differently */
        const float slope = (y - prev) / 0.0005f;
        if (prevSlope >= 0.0f && std::fabs(slope - prevSlope) > 0.05f) smooth = false;
        prevSlope = slope;
        prev = y;
    }
    CHECK(linear, "exactly linear up to the knee");
    CHECK(bounded, "never passes full scale");
    CHECK(monotonic, "louder in is never quieter out");
    CHECK(smooth, "no corner at the knee or at the top");

    /* A chord a few dB over, which used to hard-clip, now rounds off. Past the
     * shoulder's reach, about +4 dB, it is flat again: this is not a limiter. */
    Engine e;
    auto sine = sineTable();
    e.setTable(0, sine);
    e.pots[TB_P_WT1_LEVEL] = 85;
    e.pots[TB_P_VOLUME] = 39;           /* the old 110: the volume range is +18 dB wider, so pots are 1/2.82 of what they were */
    e.pots[TB_P_VCA_A] = 0;
    e.pots[TB_P_VCA_S] = 127;
    for (int n : { 48, 55, 60, 63 }) note(e, n, 127);
    auto a = render(e, 700);
    int flat = 0, peak = 0;
    for (size_t i = 2; i + 2 < a.size(); i += 2) {
        const int v = std::abs((int) a[i]);
        peak = std::max(peak, v);
        if (v >= 32700 && a[i] == a[i + 2]) flat++;
    }
    CHECK(peak > 22937, "the test chord is really into the shoulder (peak %d)", peak);
    CHECK(flat == 0, "a chord over the knee has no flat tops (%d flat samples)", flat);
}

/* Drive at 0 leaves a note untouched; turned up it adds level and harmonics. */
static void testFilterDrive()
{
    printf("== filter drive ==\n");
    auto sine = sineTable();
    auto setup = [&](Engine &e, int drive) {
        e.setTable(0, sine);
        e.pots[TB_P_WT1_LEVEL] = 60;
        e.pots[TB_P_VCA_A] = 0;
        e.pots[TB_P_VCA_S] = 127;
        e.pots[TB_P_FLT_DRIVE] = (float) drive;
    };
    Engine off, on;
    setup(off, 0);
    setup(on, 60);
    srand(3); note(off, 57); auto a = render(off, 700);
    srand(3); note(on, 57); auto b = render(on, 700);
    CHECK(rms(b) > rms(a) * 1.5, "drive adds level (rms %.0f -> %.0f)", rms(a), rms(b));

    /* The note was fully rendered through the driven path if a knob ever left 0. */
    Engine zero;
    setup(zero, 0);
    note(zero, 69);
    zero.pots[TB_P_FLT_DRIVE] = 40;
    render(zero, 20);
    zero.pots[TB_P_FLT_DRIVE] = 0;
    render(zero, 20);
    auto settled = render(zero, 10);
    Engine plain;
    setup(plain, 0);
    note(plain, 69);
    auto reference = render(plain, 50);
    double rmsSettled = 0, rmsPlain = 0;
    for (auto v : settled) rmsSettled += (double) v * v;
    for (size_t i = reference.size() - settled.size(); i < reference.size(); i++)
        rmsPlain += (double) reference[i] * reference[i];
    CHECK(std::fabs(10.0 * std::log10((rmsSettled + 1.0) / (rmsPlain + 1.0))) < 0.2,
          "back at 0 the level returns to the undriven note");

    /* The odd harmonics it adds, at a note where the table has none of its own. */
    double h3a = 0, h3b = 0;
    auto bandEnergy = [](const std::vector<int16_t> &x, double hz) {
        double re = 0, im = 0;
        const int frames = (int) x.size() / 2;
        for (int i = frames / 2, n = 0; i < frames; i++, n++) {
            re += x[(size_t) i * 2] * std::cos(2.0 * M_PI * hz * n / 44100.0);
            im += x[(size_t) i * 2] * std::sin(2.0 * M_PI * hz * n / 44100.0);
        }
        return std::sqrt(re * re + im * im) / (frames - frames / 2);
    };
    h3a = bandEnergy(a, 660.0);
    h3b = bandEnergy(b, 660.0);
    CHECK(h3b > h3a * 10.0, "drive adds the third harmonic (%.1f -> %.1f)", h3a, h3b);

    /* A resonant peak well past the level of the note is held back. A sine right on
     * the cutoff is the loudest thing a resonance can make of it; the volume is low so
     * the output clipper plays no part and only the drive stage can be responsible. */
    Engine ringy, held;
    for (Engine *x : { &ringy, &held }) {
        x->setTable(0, sine);
        x->pots[TB_P_WT1_LEVEL] = 100;
        x->pots[TB_P_FLT_FREQ] = 52;       /* note 60 */
        x->pots[TB_P_FLT_RES] = 120;
        x->pots[TB_P_VOLUME] = 7;          /* the old 20 (see the range note above) */
        x->pots[TB_P_VCA_A] = 0;
        x->pots[TB_P_VCA_S] = 127;
    }
    held.pots[TB_P_FLT_DRIVE] = 8;
    note(ringy, 60); note(held, 60);
    auto ra = render(ringy, 500), rb = render(held, 500);
    int pa = 0, pb = 0;
    for (auto v : ra) pa = std::max(pa, std::abs((int) v));
    for (auto v : rb) pb = std::max(pb, std::abs((int) v));
    CHECK(pb < pa, "drive rounds a ringing resonance (peak %d -> %d)", pa, pb);

    /* Turning it mid-note must not step: no sample jumps far from its neighbour. */
    Engine turn;
    setup(turn, 0);
    note(turn, 57);
    render(turn, 100);
    int worst = 0;
    for (int b2 = 0; b2 < 60; b2++) {
        turn.pots[TB_P_FLT_DRIVE] = (float) (b2 * 2);
        auto x = render(turn, 1);
        for (size_t i = 2; i < x.size(); i += 2)
            worst = std::max(worst, std::abs((int) x[i] - (int) x[i - 2]));
    }
    CHECK(worst < 12000, "turning drive mid-note does not step (largest jump %d)", worst);
}

/* The width of a wide stack: decorrelated streams must reach the Serum-like image. */
static void testUnisonWidth()
{
    printf("== unison width ==\n");
    auto sideMid = [](int spread) {
        Engine e;
        e.pots[TB_P_VOICE_MODE] = 1;
        e.pots[TB_P_WT1_UNI] = 16;
        e.pots[TB_P_WT1_DETUNE] = 40;
        e.pots[TB_P_WT1_SPREAD] = (float) spread;
        e.pots[TB_P_VCA_A] = 0;
        e.pots[TB_P_VCA_S] = 127;
        srand(9);
        note(e, 48);
        auto x = render(e, 1400);
        double m = 0, s = 0;
        for (size_t i = x.size() / 2; i + 1 < x.size(); i += 2) {
            const double l = x[i], r = x[i + 1];
            m += (l + r) * (l + r);
            s += (l - r) * (l - r);
        }
        return 10.0 * std::log10((s + 1.0) / (m + 1.0));
    };
    const double none = sideMid(0), half = sideMid(64), full = sideMid(127);
    CHECK(none < -30.0, "spread 0 is mono (side/mid %.1f dB)", none);
    CHECK(half > none + 10.0 && half < full - 2.0, "spread widens steadily (%.1f dB at half)", half);
    CHECK(full > -4.0, "full spread clears the old -4.8 dB limit (%.1f dB)", full);
}

static void testSyncThroughAVoice()
{
    printf("== sync through a voice ==\n");
    auto sine = sineTable();
    auto setup = [&](Engine &e) {
        e.setTable(0, sine);
        e.pots[TB_P_VCA_A] = 0;
        e.pots[TB_P_VCA_S] = 127;
    };
    Engine plain, synced;
    setup(plain); setup(synced);
    synced.pots[TB_P_WT1_SYNC] = 90;
    srand(3); note(plain, 57); auto a = render(plain, 400);
    srand(3); note(synced, 57); auto b = render(synced, 400);
    CHECK(a != b, "wt1_sync reaches the oscillator");
    const double hzA = heardHz(a);
    CHECK(hzA > 215.0 && hzA < 225.0, "the plain note is 220 Hz (%.1f)", hzA);

    /* An envelope aimed at sync does the same from a zero pot. */
    Engine swept;
    setup(swept);
    swept.pots[TB_P_ME1_DST] = MD_WT1_SYNC;
    swept.pots[TB_P_ME1_AMT] = 127;
    swept.pots[TB_P_ME1_D] = 70;
    srand(3); note(swept, 57); auto c = render(swept, 400);
    CHECK(a != c, "EG -> WT1 Sync sweeps it");
    bool bounded = true;
    for (auto v : c) bounded &= v > -32767 && v < 32767;
    CHECK(bounded, "and does not clip a single oscillator");
}

/* The second voice filter and the per-source balance: a source goes to Filter 1 (balance 0), to Filter 2 (127) or half to each (64). Filter 1 is
 * shut and Filter 2 open, so what comes out is the share Filter 2 got. */
static void testSecondFilter()
{
    printf("== second filter ==\n");
    auto level = [](int bal, int f2freq, int f1freq) {
        Engine e;
        soloWt3(e);
        e.pots[TB_P_FLT_FREQ] = (float) f1freq;
        e.pots[TB_P_FLT2_FREQ] = (float) f2freq;
        e.pots[TB_P_WT3_BAL] = (float) bal;
        e.setTable(2, sineTable());
        note(e, 69);
        return rms(render(e, 400));
    };
    const double shut = level(0, 127, 0), both = level(64, 127, 0), only2 = level(127, 127, 0), open1 = level(0, 0, 127);
    CHECK(shut < 0.02 * only2, "balance 0 goes to the shut Filter 1 only (%.0f vs %.0f)", shut, only2);
    CHECK(only2 > 1000.0, "balance 127 goes to the open Filter 2 (rms %.0f)", only2);
    CHECK(both > 0.44 * only2 && both < 0.56 * only2, "balance 64 gives Filter 2 half of the source (%.2f of full)", both / only2);
    CHECK(open1 > 0.95 * only2 && open1 < 1.05 * only2, "the two open filters pass the same level (%.2f)", open1 / only2);
    const double shut2 = level(127, 0, 127);
    CHECK(shut2 < 0.02 * only2, "balance 127 into a shut Filter 2 is silent (%.0f)", shut2);
}

/* The noise types: Pink HP is pink noise through a 12 dB high-pass near 950 Hz, so it has next to nothing below 200 Hz where plain pink has a lot. */
static void testNoiseTypes()
{
    printf("== noise types ==\n");
    auto lowShare = [](int type, double *total) {
        Engine e;
        e.pots[TB_P_WT1_LEVEL] = 0;
        e.pots[TB_P_NOISE_LEVEL] = 100;
        e.pots[TB_P_NOISE_TYPE] = (float) type;
        e.pots[TB_P_VCA_A] = 0;
        e.pots[TB_P_VCA_S] = 127;
        note(e, 48);
        auto x = render(e, 300);
        const int frames = (int) x.size() / 2;
        double lo = 0.0, all = 0.0, prev = 0.0;                       /* one-pole low-pass at ~200 Hz splits the lows off */
        for (int i = frames / 2; i < frames; i++) {
            const double v = x[(size_t) i * 2];
            prev += 0.028 * (v - prev);
            lo += prev * prev; all += v * v;
        }
        *total = std::sqrt(all / (frames - frames / 2));
        return lo / all;
    };
    double tPink = 0, tHp = 0, tHp12 = 0;
    const double pink = lowShare(1, &tPink), hp = lowShare(2, &tHp), hp12 = lowShare(3, &tHp12);
    CHECK(tPink > 200.0 && tHp > 200.0 && tHp12 > 200.0, "all three sound (rms %.0f %.0f %.0f)", tPink, tHp, tHp12);
    CHECK(hp < 0.1 * pink, "Pink HP has under a tenth of pink's low end (%.4f vs %.4f)", hp, pink);
    CHECK(hp12 < hp, "Pink HP 12 is higher still (%.4f)", hp12);
}

/* Serum's noise Color: the engine's High Pass and Low Pass pots (12 dB/octave, 20 Hz..20 kHz, log), and Brown noise (3 dB per octave down in an octave band). */
static void testNoiseColour()
{
    printf("== noise colour ==\n");
    auto shares = [](int type, int hp, int lp, double *rms) {
        Engine e;
        e.pots[TB_P_WT1_LEVEL] = 0;
        e.pots[TB_P_NOISE_LEVEL] = 100;
        e.pots[TB_P_NOISE_TYPE] = (float) type;
        e.pots[TB_P_NOISE_HP] = (float) hp;
        e.pots[TB_P_NOISE_LP] = (float) lp;
        e.pots[TB_P_VCA_A] = 0;
        e.pots[TB_P_VCA_S] = 127;
        note(e, 48);
        auto x = render(e, 300);
        const int frames = (int) x.size() / 2;
        double lo = 0.0, hi = 0.0, all = 0.0, prev = 0.0, prevHi = 0.0;
        for (int i = frames / 2; i < frames; i++) {
            const double v = x[(size_t) i * 2];
            prev += 0.028 * (v - prev);                    /* ~200 Hz low-pass: the lows */
            prevHi += 0.5 * (v - prevHi);                  /* v - this is a high-pass near 7 kHz */
            lo += prev * prev; hi += (v - prevHi) * (v - prevHi); all += v * v;
        }
        *rms = std::sqrt(all / (frames - frames / 2));
        return std::array<double, 2>{ lo / all, hi / all };
    };
    double r0, r1, r2, r3, r4;
    const auto neutral = shares(1, 0, 127, &r0);
    const auto hp = shares(1, 64, 127, &r1);                /* 20 Hz x 1000^(64/127) = 640 Hz */
    const auto lp = shares(1, 0, 64, &r2);
    const auto brown = shares(4, 0, 127, &r3);
    const auto white = shares(0, 0, 127, &r4);
    CHECK(r0 > 100 && r1 > 100 && r2 > 100 && r3 > 100 && r4 > 100, "every setting sounds (rms %.0f %.0f %.0f %.0f %.0f)", r0, r1, r2, r3, r4);
    CHECK(hp[0] < 0.1 * neutral[0], "High Pass at 640 Hz takes out the low end (%.4f vs %.4f)", hp[0], neutral[0]);
    CHECK(lp[1] < 0.1 * neutral[1], "Low Pass at 640 Hz takes out the high end (%.5f vs %.5f)", lp[1], neutral[1]);
    CHECK(brown[0] > neutral[0] && brown[1] < neutral[1], "Brown has more low end and less high end than Pink");
    CHECK(white[1] > neutral[1], "White has more high end than Pink");
}

/* Delay Sync: the times are a 120 BPM beat's; with Sync On they scale with the host tempo (x 120 / BPM). A noise burst, the first echo's time at 120 and at 60 BPM. */
static void testDelaySync()
{
    printf("== delay sync ==\n");
    auto firstEcho = [](float bpm, int sync) {
        Engine e;
        e.setHostBpm(bpm);
        e.pots[TB_P_WT1_LEVEL] = 0;
        e.pots[TB_P_NOISE_LEVEL] = 100;
        e.pots[TB_P_NOISE_TYPE] = 0;
        e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127; e.pots[TB_P_VCA_R] = 0;
        e.pots[TB_P_DLY_ON] = 1; e.pots[TB_P_DLY_MIX] = 127; e.pots[TB_P_DLY_FB] = 0;
        e.pots[TB_P_DLY_TIME] = 80; e.pots[TB_P_DLY_TIMER] = 80;                    /* 1500^(80/127) = 100 ms */
        e.pots[TB_P_DLY_SYNC] = (float) sync;
        note(e, 48);
        auto x = render(e, 20);                                                   /* ~58 ms of burst */
        const uint8_t off[] = { 0x80, 48, 0 };
        e.onMidi(off, 3);
        auto y = render(e, 400);
        x.insert(x.end(), y.begin(), y.end());
        const int frames = (int) x.size() / 2, blk = 128;
        std::vector<double> env;
        for (int i = 0; i + blk <= frames; i += blk) {
            double a = 0;
            for (int j = 0; j < blk; j++) a += (double) x[(size_t) (i + j) * 2] * x[(size_t) (i + j) * 2];
            env.push_back(a / blk);
        }
        double peak = 0;
        for (double v : env) peak = std::max(peak, v);
        for (int b = 1; b < (int) env.size(); b++)                                /* the output is the wet part alone: its first block above a fifth of the peak */
            if (env[(size_t) b] > 0.2 * peak) return b * 128.0 / 44100.0 * 1000.0;
        return -1.0;
    };
    const double t120 = firstEcho(120.0f, 1), t60 = firstEcho(60.0f, 1), tOff = firstEcho(60.0f, 0);
    CHECK(t120 > 0 && t60 > 0 && tOff > 0, "an echo is found (%.0f %.0f %.0f ms)", t120, t60, tOff);
    CHECK(std::fabs(t60 / t120 - 2.0) < 0.15, "Sync On at 60 BPM doubles the time of 120 BPM (%.0f vs %.0f ms)", t60, t120);
    CHECK(std::fabs(tOff / t120 - 1.0) < 0.15, "Sync Off ignores the host tempo (%.0f vs %.0f ms)", tOff, t120);
}

/* The Serum velocity law: flat with VCA Velocity 0, linear in the velocity at 127, and the same gain at velocity 100 whatever the depth. */
static void testVelocityLaw()
{
    printf("== velocity law ==\n");
    auto level = [](int vel, int depth, int law) {
        Engine e;
        e.pots[TB_P_WT1_LEVEL] = 100;
        e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127;
        e.pots[TB_P_VCA_VEL] = (float) depth;
        e.pots[TB_P_VCA_VLAW] = (float) law;
        note(e, 60, vel);
        auto x = render(e, 200);
        double a = 0;
        for (size_t i = x.size() / 2; i < x.size(); i += 2) a += (double) x[i] * x[i];
        return 10.0 * std::log10(a / (x.size() / 4) + 1e-9);
    };
    const double flat20 = level(20, 0, 1), flat127 = level(127, 0, 1), flat100 = level(100, 0, 1);
    CHECK(std::fabs(flat20 - flat127) < 0.2, "depth 0: velocity 20 and 127 are the same loudness (%.2f vs %.2f dB)", flat20, flat127);
    const double lin50 = level(50, 127, 1), lin100 = level(100, 127, 1), lin127 = level(127, 127, 1);
    CHECK(std::fabs((lin50 - lin100) - 20.0 * std::log10(50.0 / 100.0)) < 0.5, "depth 127: velocity 50 is 20 log10(50/100) re 100 (%.2f dB)", lin50 - lin100);
    CHECK(std::fabs((lin127 - lin100) - 20.0 * std::log10(127.0 / 100.0)) < 0.5, "and 127 is 20 log10(127/100) above it (%.2f dB)", lin127 - lin100);
    CHECK(std::fabs(flat100 - lin100) < 0.2, "velocity 100 has the same level at either depth (%.2f vs %.2f dB)", flat100, lin100);
}

/* A note starts with a short ramp, not at full level. With the attack at 0 the engine used to start a note at whatever the oscillator was doing
 * (a step of up to the whole amplitude, a click, and the rapid notes of a mono bass clicked each time); Serum's own start takes 1.5 to 3 ms. The
 * measure: the biggest of the first 8 samples against the block's peak, over 12 oscillator phases. */
static void testNoteOnset()
{
    printf("== note onset ==\n");
    auto first = [](unsigned seed, int attackPot) {
        srand(seed);
        Engine e;
        e.setTable(0, sineTable());
        e.pots[TB_P_WT1_LEVEL] = 100; e.pots[TB_P_WT2_LEVEL] = 0; e.pots[TB_P_WT3_LEVEL] = 0;
        e.pots[TB_P_VCA_A] = (float) attackPot; e.pots[TB_P_VCA_S] = 127;
        note(e, 60 + (int) seed, 100);
        auto x = render(e, 4);
        double head = 0, peak = 0;
        for (int i = 0; i < 8; i++) head = std::max(head, (double) std::abs(x[(size_t) i * 2]));
        for (int i = 0; i < 400; i++) peak = std::max(peak, (double) std::abs(x[(size_t) i * 2]));
        return head / std::max(peak, 1.0);
    };
    double worst0 = 0;
    for (unsigned s = 1; s <= 12; s++) worst0 = std::max(worst0, first(s, 0));
    CHECK(worst0 < 0.12, "attack 0: the first 8 samples reach at most %.0f %% of the peak (3 ms floor; the old 0.5 ms floor gave up to 90 %%)", worst0 * 100.0);
    CHECK(potAttackTime(0) >= 0.003f && potAttackTime(20) >= 0.003f && std::fabs(potAttackTime(60) - (0.0005f + 10.0f * std::pow(60.0f / 127.0f, 5.0f))) < 1e-6f,
          "the floor is 3 ms and leaves longer attacks alone (%.1f ms at 0, %.1f ms at 60)", potAttackTime(0) * 1000.0f, potAttackTime(60) * 1000.0f);
}

/* The FX buses: a send is added to the main path (an empty bus is a plain copy, so Direct plus a full send is +6 dB), a source routed to None is
 * on the bus alone, and a bus rack's effects run on what it is sent, tail and all. */
static void testFxBuses()
{
    printf("== FX buses ==\n");
    auto level = [](int route, int send, bool reverb) {
        Engine e;
        e.pots[TB_P_WT1_LEVEL] = 100;
        e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127;
        e.pots[TB_P_VCA_VLAW] = 1; e.pots[TB_P_VCA_VEL] = 0;
        e.pots[TB_P_WT1_POST] = (float) route;
        e.pots[TB_P_WT1_BUS1] = (float) send;
        if (reverb) {
            e.busPots[0][TB_P_REV_ON] = 1; e.busPots[0][TB_P_REV_MIX] = 127; e.busPots[0][TB_P_REV_DECAY] = 90;
        }
        note(e, 60, 100);
        auto x = render(e, 200);
        double a = 0;
        for (size_t i = x.size() / 2; i < x.size(); i += 2) a += (double) x[i] * x[i];
        return 10.0 * std::log10(a / (x.size() / 4) + 1e-9);
    };
    const double direct = level(1, 0, false), both = level(1, 127, false), onlyBus = level(2, 127, false), silent = level(2, 0, false);
    CHECK(std::fabs((both - direct) - 6.02) < 0.3, "Direct plus a full send to an empty bus is +6 dB (%.2f dB)", both - direct);
    CHECK(std::fabs(onlyBus - direct) < 0.3, "route None with a full send sounds like the Direct route alone (%.2f vs %.2f dB)", onlyBus, direct);
    CHECK(silent < -60.0, "route None with no send is silent (%.1f dB)", silent);
    /* the tail: note off, then the reverb on the bus keeps ringing */
    Engine e;
    e.pots[TB_P_WT1_LEVEL] = 100; e.pots[TB_P_WT1_POST] = 2; e.pots[TB_P_WT1_BUS1] = 127;
    e.pots[TB_P_VCA_R] = 5;
    e.busPots[0][TB_P_REV_ON] = 1; e.busPots[0][TB_P_REV_MIX] = 127; e.busPots[0][TB_P_REV_DECAY] = 100;
    note(e, 60, 100);
    render(e, 100);
    const uint8_t off[] = { 0x80, 60, 0 };
    e.onMidi(off, 3);
    render(e, 60);
    auto tail = render(e, 40);
    CHECK(rms(tail) > 50.0, "the bus reverb is still ringing after the note and the dry signal are gone (rms %.0f)", rms(tail));
    /* Out skips the main rack, Direct (Serum's Master) does not: a main-rack reverb rings after the note for one and not for the other; and what a bus returns goes through it */
    auto ring = [](int route, int send) {
        Engine e;
        e.pots[TB_P_WT1_LEVEL] = 100; e.pots[TB_P_WT1_POST] = (float) route; e.pots[TB_P_WT1_BUS1] = (float) send;
        e.pots[TB_P_VCA_R] = 5;
        e.pots[TB_P_REV_ON] = 1; e.pots[TB_P_REV_MIX] = 127; e.pots[TB_P_REV_DECAY] = 100;
        note(e, 60, 100);
        render(e, 100);
        const uint8_t off[] = { 0x80, 60, 0 };
        e.onMidi(off, 3);
        render(e, 60);
        return rms(render(e, 40));
    };
    CHECK(ring(3, 0) < 5.0, "an Out source skips the main reverb: nothing rings after the note (rms %.0f)", ring(3, 0));
    CHECK(ring(1, 0) > 50.0, "a Direct source goes through it (rms %.0f)", ring(1, 0));
    CHECK(ring(2, 127) > 50.0, "a bus's return goes through the main rack too (rms %.0f)", ring(2, 127));
    /* a voice filter's output is a source too: None with a full send is the same level as Master with none; Master with a full send is +6 dB; it does not follow the velocity */
    auto fltLevel = [](int out, int send, int vel) {
        Engine e;
        e.pots[TB_P_WT1_LEVEL] = 100;
        e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127;
        e.pots[TB_P_VCA_VLAW] = 1; e.pots[TB_P_VCA_VEL] = 127;
        e.pots[TB_P_FLT1_OUT] = (float) out; e.pots[TB_P_FLT1_BUS1] = (float) send;
        note(e, 60, vel);
        auto x = render(e, 200);
        double a = 0;
        for (size_t i = x.size() / 2; i < x.size(); i += 2) a += (double) x[i] * x[i];
        return 10.0 * std::log10(a / (x.size() / 4) + 1e-9);
    };
    const double f0 = fltLevel(0, 0, 100), fNone = fltLevel(1, 127, 100), fBoth = fltLevel(0, 127, 100), fSoft = fltLevel(1, 127, 50), f0Soft = fltLevel(0, 0, 50);
    CHECK(std::fabs(fNone - f0) < 0.3, "Filter 1 output None with a full send is Master with none (%.2f vs %.2f dB)", fNone, f0);
    CHECK(std::fabs((fBoth - f0) - 6.02) < 0.3 || std::fabs((fBoth - f0) - 6.02) < 0.6, "Master plus a full send is +6 dB (%.2f dB)", fBoth - f0);
    CHECK(std::fabs(fSoft - fNone) < 0.3 && f0Soft < f0 - 3.0, "the send does not follow the velocity while the main path does (%.2f dB vs %.2f dB at velocity 50)", fSoft - fNone, f0Soft - f0);
    /* in series: Filter 1's output into Filter 2 filters it again */
    auto serial = [](int out) {
        Engine e;
        e.pots[TB_P_WT1_LEVEL] = 100; e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127;
        e.pots[TB_P_FLT_FREQ] = 45; e.pots[TB_P_FLT2_FREQ] = 45; e.pots[TB_P_FLT2_TYPE] = 1;
        e.pots[TB_P_FLT1_OUT] = (float) out;
        note(e, 72, 100);
        auto x = render(e, 200);
        double a = 0;
        for (size_t i = x.size() / 2; i < x.size(); i += 2) a += (double) x[i] * x[i];
        return 10.0 * std::log10(a / (x.size() / 4) + 1e-9);
    };
    CHECK(serial(3) < serial(0) - 3.0, "Filter 1 into Filter 2 in series is quieter than Filter 1 alone (%.1f vs %.1f dB)", serial(3), serial(0));
}

int main()
{
    testThirdOscillator();
    testDestinationOrder();
    testStreamBudget();
    testTuneRange();
    testSoftClipper();
    testFilterDrive();
    testUnisonWidth();
    testSyncThroughAVoice();
    testSecondFilter();
    testNoiseTypes();
    testNoiseColour();
    testDelaySync();
    testVelocityLaw();
    testFxBuses();
    testNoteOnset();
    printf("\n%s (%d failures)\n", g_fail ? "ENGINE TESTS FAILED" : "ENGINE TESTS PASSED", g_fail);
    return g_fail ? 1 : 0;
}
