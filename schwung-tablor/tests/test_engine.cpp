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
    CHECK(tb_params[TB_P_WT3_TUNE].min == -72 && tb_params[TB_P_WT3_TUNE].max == 72, "every oscillator reaches +/-72");
    {   /* Pitch Track off: the oscillator plays MIDI note 0 plus its tune whatever key is held */
        double hz[2];
        for (int i = 0; i < 2; i++) {
            Engine e;
            e.setTable(0, sine);
            e.pots[TB_P_VCA_A] = 0;
            e.pots[TB_P_VCA_S] = 127;
            e.pots[TB_P_WT1_FIXED] = 1;
            e.pots[TB_P_WT1_TUNE] = 57;          /* note 57 = A3 = 220 Hz */
            note(e, i ? 40 : 90);
            hz[i] = heardHz(render(e, 2000));
        }
        CHECK(hz[0] > 215.0 && hz[0] < 225.0 && hz[1] > 215.0 && hz[1] < 225.0, "a fixed oscillator ignores the key (%.1f and %.1f Hz for 220)", hz[0], hz[1]);
    }
    {   /* a spare envelope into WT1 Pitch: 48 semitones at full amount (a held sustain of 1) */
        Engine e;
        e.setTable(0, sine);
        e.pots[TB_P_VCA_A] = 0;
        e.pots[TB_P_VCA_S] = 127;
        e.pots[TB_P_ME1_DST] = 43;
        e.pots[TB_P_ME1_AMT] = 64 + 31;          /* about +0.5 = 24 semitones */
        e.pots[TB_P_ME1_A] = 0;
        e.pots[TB_P_ME1_S] = 127;
        note(e, 45);                             /* 110 Hz, two octaves up = 440 */
        const double hz = heardHz(render(e, 2000));
        CHECK(hz > 425.0 && hz < 455.0, "a spare envelope into WT1 Pitch moves it 24 st (%.1f Hz for 440)", hz);
    }
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

/* A mono note arriving over a releasing one takes its voice over where the envelope is: a fresh start() zeroed the amplitude envelope, a step down from the
 * release level that was a click (Lead - Rollin: 20 dB more sub-bass than Serum's from the steps between its short notes). */
static void testMonoTakeover()
{
    printf("== mono takeover ==\n");
    auto run = [](bool overReleasing) {
        srand(7);
        Engine e;
        e.setTable(0, sineTable());
        e.pots[TB_P_WT1_LEVEL] = 100; e.pots[TB_P_WT2_LEVEL] = 0; e.pots[TB_P_WT3_LEVEL] = 0;
        e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127; e.pots[TB_P_VCA_R] = 90;
        e.pots[TB_P_VOICE_MODE] = 1;
        note(e, 60, 100);
        render(e, 100);
        const uint8_t off[] = { 0x80, 60, 0 };
        if (overReleasing) { e.onMidi(off, 3); render(e, 4); }
        auto before = render(e, 1);
        note(e, 64, 100);
        auto after = render(e, 1);
        double steady = 1, jump = 0;
        for (int i = 1; i < 128; i++) steady = std::max(steady, std::abs((double) before[(size_t) i * 2] - (double) before[(size_t) (i - 1) * 2]));
        jump = std::abs((double) after[0] - (double) before[(size_t) 127 * 2]);
        for (int i = 1; i < 8; i++) jump = std::max(jump, std::abs((double) after[(size_t) i * 2] - (double) after[(size_t) (i - 1) * 2]));
        return jump / steady;
    };
    const double held = run(false), releasing = run(true);
    CHECK(releasing < 2.0, "a note arriving over a releasing one joins without a step (largest step %.1f x the steady wave's; a fresh start() zeroed the envelope and phase)", releasing);
    CHECK(held < 2.0, "a note arriving over a held one joins without a step (%.1f x)", held);
}

/* The clip player: a key down starts the pattern, which plays at the host tempo, looped, transposed from C3, and a key up stops it. At 120 BPM a
 * beat is 0.5 s = 172.3 blocks. */
static void testClipPlayer()
{
    printf("== clip player ==\n");
    auto make = [] {
        auto e = std::make_unique<Engine>();
        e->setTable(0, sineTable());
        e->pots[TB_P_WT1_LEVEL] = 100; e->pots[TB_P_WT2_LEVEL] = 0; e->pots[TB_P_WT3_LEVEL] = 0;
        e->pots[TB_P_VCA_A] = 0; e->pots[TB_P_VCA_S] = 127; e->pots[TB_P_VCA_R] = 0;
        e->setClip(parseClip("192|96,48,69,100"));               /* two beats; A4 starts at beat 1 and lasts half a beat */
        return e;
    };
    {
        auto e = make();
        note(*e, 60);                                            /* C3 plays the pattern as written */
        auto a = render(*e, 160);
        double early = 0; for (int i = 0; i < (int) a.size(); i++) early = std::max(early, (double) std::abs(a[(size_t) i]));
        CHECK(early < 50, "nothing sounds before the first note (peak %.0f)", early);
        auto b = render(*e, 60);                                  /* 0.47 s .. 0.74 s: the note, 0.25 s long */
        double on = 0; for (int i = 0; i < (int) b.size(); i++) on = std::max(on, (double) std::abs(b[(size_t) i]));
        CHECK(on > 1000, "the note sounds at its beat (peak %.0f)", on);
        auto c = render(*e, 100);                                 /* 0.64 s .. 0.93 s: the note ended at 0.75 s */
        CHECK(rms(c) < 50, "and ends after its length (rms %.0f)", rms(c));
        auto d = render(*e, 230);                                 /* the loop comes round: beat 1 of the second pass, 1.5 s (block 517) */
        double again = 0; for (int i = 0; i < (int) d.size(); i++) again = std::max(again, (double) std::abs(d[(size_t) i]));
        CHECK(again > 1000, "the pattern loops (peak %.0f)", again);
        const uint8_t off[] = { 0x80, 60, 0 };
        e->onMidi(off, 3);
        render(*e, 10);
        auto f = render(*e, 400);
        CHECK(rms(f) < 50, "a key up stops it for good (rms %.0f)", rms(f));
    }
    {
        auto e = make();
        note(*e, 69);                                            /* nine semitones up: the pattern's A4 sounds at F#5 */
        render(*e, 172);
        auto b = render(*e, 40);
        const double hz = heardHz(b);
        CHECK(hz > 730.0 && hz < 750.0, "the key transposes the pattern from C3 (%.1f Hz, F#5 is 740)", hz);
    }
    {
        Engine e;
        e.setTable(0, sineTable());
        e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127;
        note(e, 69);
        auto a = render(e, 100);
        CHECK(rms(a) > 1000.0, "without a pattern the key plays its own note (rms %.0f)", rms(a));
    }
}

/* The arpeggiator: held keys played one at a time, one 1/16 step (0.125 s, 43 blocks) each; the Pattern shape plays a pattern of indexes into them. */
/* The two sample oscillators: each plays its own set at its own level and tuning. */
static std::shared_ptr<SampleSet> sineSet(int cycles)
{
    auto s = std::make_shared<SampleSet>();
    SampleRegion r;
    r.root = 60; r.loops = true; r.loopStart = 0.0f; r.loopEnd = 1.0f; r.rate = 44100.0f; r.frames = 4410;
    r.pcm.resize(4410);
    for (int i = 0; i < 4410; i++) r.pcm[(size_t) i] = (int16_t) (12000.0 * std::sin(2.0 * M_PI * cycles * i / 4410.0));
    s->regions.push_back(std::move(r));
    return s;
}

static void testSample2()
{
    printf("== second sample oscillator ==\n");
    auto near = [](double x, double want) { return std::fabs(x - want) < want * 0.1; };
    auto make = [] {
        auto e = std::make_unique<Engine>();
        e->pots[TB_P_WT1_LEVEL] = 0; e->pots[TB_P_WT2_LEVEL] = 0; e->pots[TB_P_WT3_LEVEL] = 0;
        e->pots[TB_P_VCA_A] = 0; e->pots[TB_P_VCA_S] = 127; e->pots[TB_P_VCA_R] = 0;
        return e;
    };
    auto hear = [&](bool first, bool second) {
        auto e = make();
        if (first)  { e->setSample(sineSet(10));  e->pots[TB_P_SMP_LEVEL] = 100; }
        if (second) { e->setSample2(sineSet(10)); e->pots[TB_P_SMP2_LEVEL] = 100; e->pots[TB_P_SMP2_TUNE] = 12; }
        note(*e, 60);
        render(*e, 40);
        return render(*e, 60);
    };
    auto a = hear(true, false), b = hear(false, true), c = hear(true, true);
    CHECK(near(heardHz(a), 100.0), "the first sample plays at its root (%.1f Hz, 100 expected)", heardHz(a));
    CHECK(near(heardHz(b), 200.0), "the second sample plays alone, an octave up by its own tuning (%.1f Hz, 200 expected)", heardHz(b));
    CHECK(rms(c) > rms(a) * 1.05, "both together are louder than the first alone (rms %.0f vs %.0f)", rms(c), rms(a));
    {
        auto e = make();
        e->setSample3(sineSet(10)); e->pots[TB_P_SMP3_LEVEL] = 100; e->pots[TB_P_SMP3_TUNE] = 19;
        note(*e, 60); render(*e, 40);
        auto f = render(*e, 60);
        CHECK(near(heardHz(f), 300.0), "the third sample plays alone, a fifth and an octave up (%.1f Hz, 300 expected)", heardHz(f));
    }
    auto d = make(); d->setSample2(sineSet(10)); d->pots[TB_P_SMP2_LEVEL] = 0; note(*d, 60); render(*d, 40);
    CHECK(rms(render(*d, 60)) < 50, "a level of 0 is silent (rms %.0f)", rms(render(*d, 1)));
}

static void testSamplePan()
{
    printf("== sample oscillator pan ==\n");
    auto chan = [](const std::vector<int16_t> &x, int ch) {
        double acc = 0.0; const int frames = (int) x.size() / 2;
        for (int i = frames / 2; i < frames; i++) acc += (double) x[(size_t) i * 2 + ch] * x[(size_t) i * 2 + ch];
        return std::sqrt(acc / (frames - frames / 2));
    };
    auto hear = [&](int pan) {
        auto e = std::make_unique<Engine>();
        e->pots[TB_P_WT1_LEVEL] = 0; e->pots[TB_P_WT2_LEVEL] = 0; e->pots[TB_P_WT3_LEVEL] = 0;
        e->pots[TB_P_VCA_A] = 0; e->pots[TB_P_VCA_S] = 127; e->pots[TB_P_VCA_R] = 0;
        e->setSample(sineSet(10)); e->pots[TB_P_SMP_LEVEL] = 100; e->pots[TB_P_SMP_PAN] = (float) pan;
        note(*e, 60); render(*e, 40);
        auto x = render(*e, 60);
        return std::make_pair(chan(x, 0), chan(x, 1));
    };
    const auto c = hear(0), p = hear(14), h = hear(50), hl = hear(-50);
    CHECK(std::fabs(c.first - c.second) < 0.02 * c.first, "pan 0 is centred (L %.0f, R %.0f)", c.first, c.second);
    /* Serum: t = pan / 50, L = (1 - t)(1 + 0.172 t), R = (1 + t)(1 - 0.172 t) relative to the centre (measured: 0.7541 / 1.2188 at pan 14.035) */
    CHECK(std::fabs(p.first / c.first - 0.7545) < 0.03 && std::fabs(p.second / c.second - 1.2190) < 0.03, "pan 14 gains %.3f / %.3f (0.754 / 1.219 expected)", p.first / c.first, p.second / c.second);
    CHECK(h.first < 0.01 * c.first && std::fabs(h.second / c.second - 1.6557) < 0.04, "pan 50 is hard right at 1.656 (L %.0f, R %.3f x centre)", h.first, h.second / c.second);
    CHECK(hl.second < 0.01 * c.second && std::fabs(hl.first / c.first - 1.6557) < 0.04, "pan -50 is hard left (R %.0f, L %.3f x centre)", hl.second, hl.first / c.first);
}

static void testArp()
{
    printf("== arpeggiator ==\n");
    auto make = [](const char *spec) {
        auto e = std::make_unique<Engine>();
        e->setTable(0, sineTable());
        e->pots[TB_P_WT1_LEVEL] = 100; e->pots[TB_P_WT2_LEVEL] = 0; e->pots[TB_P_WT3_LEVEL] = 0;
        e->pots[TB_P_VCA_A] = 0; e->pots[TB_P_VCA_S] = 127; e->pots[TB_P_VCA_R] = 0;
        e->setArp(parseArp(spec));
        note(*e, 60); note(*e, 64); note(*e, 67);
        return e;
    };
    /* the pitch heard in step k: blocks [k*43.07 + 14, +22) */
    auto steps = [](Engine &e, int n) {
        std::vector<double> hz;
        double at = 0;
        for (int k = 0; k < n; k++) {
            const double from = k * 43.07 + 14.0;
            render(e, (int) (from - at)); at = std::floor(from);
            hz.push_back(heardHz(render(e, 22))); at += 22;
        }
        return hz;
    };
    auto near = [](double a, double b) { return std::fabs(a - b) < 0.1 * b;   /* heardHz counts crossings over a short window: good to a few %, notes here are 20% apart at least */ };
    {
        auto e = make("0,24,100,0,0,0,0,12,0,127,0");
        auto h = steps(*e, 7);
        const double c3 = 261.6, e3 = 329.6, g3 = 392.0;
        CHECK(near(h[0], c3) && near(h[1], e3) && near(h[2], g3) && near(h[3], c3) && near(h[4], e3) && near(h[6], c3),
              "Up walks the held keys upward and round (%.0f %.0f %.0f %.0f %.0f ... %.0f Hz)", h[0], h[1], h[2], h[3], h[4], h[6]);
    }
    {
        auto e = make("1,24,100,0,0,0,0,12,0,127,0");
        auto h = steps(*e, 4);
        CHECK(near(h[0], 392.0) && near(h[1], 329.6) && near(h[2], 261.6) && near(h[3], 392.0), "Down walks them downward (%.0f %.0f %.0f %.0f Hz)", h[0], h[1], h[2], h[3]);
    }
    {
        auto e = make("2,24,100,0,0,0,0,12,0,127,0");
        auto h = steps(*e, 5);
        CHECK(near(h[0], 261.6) && near(h[1], 329.6) && near(h[2], 392.0) && near(h[3], 329.6) && near(h[4], 261.6), "UpDown turns round at the ends (%.0f %.0f %.0f %.0f %.0f Hz)", h[0], h[1], h[2], h[3], h[4]);
    }
    {
        auto e = make("0,24,100,1,12,0,0,12,0,127,0");
        auto h = steps(*e, 5);
        CHECK(near(h[3], 523.3) && near(h[4], 659.3), "a transpose range repeats the keys an octave up (%.0f %.0f Hz)", h[3], h[4]);
    }
    {
        auto e = make("4,24,100,0,0,0,0,12,0,127,96|0,24,2,100|24,24,0,100|48,24,4,100");     /* indexes 2, 0, 4 of three keys: G3, C3, E3 (wrap range 0) */
        auto h = steps(*e, 4);
        CHECK(near(h[0], 392.0) && near(h[1], 261.6) && near(h[2], 329.6), "a pattern plays indexes into the held keys (%.0f %.0f %.0f Hz)", h[0], h[1], h[2]);
        auto b = make("4,24,100,0,0,0,2,12,0,127,96|0,24,4,100");                                 /* index 4 of three keys, wrap range 2: E3 an octave up */
        auto g = steps(*b, 1);
        CHECK(near(g[0], 659.3), "a pattern index past the keys wraps up an octave (%.0f Hz)", g[0]);
    }
    {
        auto e = make("0,24,50,0,0,0,0,12,0,127,0");                                              /* gate 50: the note ends half way through its step */
        render(*e, 8);
        auto on = render(*e, 4);
        render(*e, 12);
        auto off = render(*e, 6);
        CHECK(rms(on) > 1000.0 && rms(off) < 50.0, "a 50%% gate ends the note half way (%.0f then %.0f rms)", rms(on), rms(off));
        const uint8_t up[] = { 0x80, 60, 0 };
        for (int k : { 60, 64, 67 }) { uint8_t m[] = { 0x80, (uint8_t) k, 0 }; e->onMidi(m, 3); }
        render(*e, 10);
        auto f = render(*e, 200);
        CHECK(rms(f) < 50.0, "releasing every key stops the arpeggio (rms %.0f)", rms(f));
        (void) up;
    }
    {
        auto e = std::make_unique<Engine>();
        e->setTable(0, sineTable());
        e->pots[TB_P_WT1_LEVEL] = 100; e->pots[TB_P_WT2_LEVEL] = 0; e->pots[TB_P_WT3_LEVEL] = 0;
        e->pots[TB_P_VCA_A] = 0; e->pots[TB_P_VCA_S] = 127; e->pots[TB_P_VCA_R] = 0;
        e->setArp(parseArp("0,24,100,0,0,0,0,12,0,59,0"));                                         /* zone up to B2: C3 is outside and plays as a plain held note */
        note(*e, 60);
        render(*e, 100);
        auto a = render(*e, 20);
        render(*e, 20);
        auto b = render(*e, 20);
        CHECK(near(heardHz(a), 261.6) && near(heardHz(b), 261.6) && rms(b) > 1000.0, "a key outside the zone plays its own note (%.0f Hz)", heardHz(b));
    }
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
    /* a bus rack's own Filter FX (b1.fxf_*): off it is transparent, a low-pass at a low cutoff takes the level down, an open one does not */
    auto filtered = [](bool on, int freq) {
        Engine e;
        e.pots[TB_P_WT1_LEVEL] = 100; e.pots[TB_P_WT1_POST] = 2; e.pots[TB_P_WT1_BUS1] = 127;
        e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127;
        e.busPots[0][TB_P_FXF_ON] = on ? 1.0f : 0.0f; e.busPots[0][TB_P_FXF_TYPE] = 1; e.busPots[0][TB_P_FXF_FREQ] = (float) freq; e.busPots[0][TB_P_FXF_ENV] = 64;
        note(e, 72, 100);
        auto x = render(e, 200);
        double a = 0;
        for (size_t i = x.size() / 2; i < x.size(); i += 2) a += (double) x[i] * x[i];
        return 10.0 * std::log10(a / (x.size() / 4) + 1e-9);
    };
    const double fOff = filtered(false, 20), fLow = filtered(true, 20), fOpen = filtered(true, 127);
    CHECK(fLow < fOff - 12.0, "a closed bus Filter FX (LP 24) takes a saw's level well down (%.1f dB against %.1f off)", fLow, fOff);
    CHECK(std::fabs(fOpen - fOff) < 1.0, "an open bus Filter FX leaves the level alone (%.1f dB against %.1f off)", fOpen, fOff);
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
    /* the other way: a source on Filter 2 only, Filter 2's output into Filter 1 filters it again, and the sound is not lost */
    auto serial2 = [](int out) {
        Engine e;
        e.pots[TB_P_WT1_LEVEL] = 100; e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127;
        e.pots[TB_P_WT1_BAL] = 127;
        e.pots[TB_P_FLT_FREQ] = 45; e.pots[TB_P_FLT2_FREQ] = 45; e.pots[TB_P_FLT2_TYPE] = 1;
        e.pots[TB_P_FLT2_OUT] = (float) out;
        note(e, 72, 100);
        auto x = render(e, 200);
        double a = 0;
        for (size_t i = x.size() / 2; i < x.size(); i += 2) a += (double) x[i] * x[i];
        return 10.0 * std::log10(a / (x.size() / 4) + 1e-9);
    };
    CHECK(serial2(3) < serial2(0) - 3.0 && serial2(3) > -80.0, "Filter 2 into Filter 1 in series is quieter than Filter 2 alone and not silent (%.1f vs %.1f dB)", serial2(3), serial2(0));
}

/* An LFO route to "FX Filter Freq" (MOD_DST 24) moves the effects block's Filter FX cutoff: a closed low-pass with a constant LFO at 1 and a full Uni route is opened by it */
static void testFxFilterLfo()
{
    auto level = [](bool route) {
        Engine e;
        e.pots[TB_P_WT1_LEVEL] = 100;
        e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127;
        e.pots[TB_P_FX_ON] = 1; e.pots[TB_P_FXF_ON] = 1; e.pots[TB_P_FXF_TYPE] = 1; e.pots[TB_P_FXF_FREQ] = 20; e.pots[TB_P_FXF_ENV] = 64;
        for (int k = 0; k < 16; k++) e.pots[TB_P_LFO1_S0 + k] = 127;          /* the LFO sits at 1 */
        e.pots[TB_P_LFO1_RATE] = 0;
        if (route) { e.pots[TB_P_LR1_SRC] = 1; e.pots[TB_P_LR1_DST] = 24; e.pots[TB_P_LR1_AMT] = 127; e.pots[TB_P_LR1_BIP] = 0; }
        note(e, 72, 100);
        auto x = render(e, 200);
        double a = 0;
        for (size_t i = x.size() / 2; i < x.size(); i += 2) a += (double) x[i] * x[i];
        return 10.0 * std::log10(a / (x.size() / 4) + 1e-9);
    };
    const double closed = level(false), opened = level(true);
    CHECK(opened > closed + 10.0, "an LFO route to FX Filter Freq opens a closed Filter FX low-pass (%.1f dB against %.1f)", opened, closed);
}

/* An LFO route to "Util Out" / "Bus1 Out" (MOD_DST 36, 37) moves the Level Out knob of the Utils that ends the main rack / bus 1: the gain is (2 x (the stored knob + the offset))^2,
 * the stored knob being the rack's util_out pot / 127 (0 = no route, the signal untouched; measured on Serum: -12 dB at knob 0.25, +12 dB at 1.0). */
static void testUtilOutLfo()
{
    auto level = [](int rack, int base, bool route) {
        Engine e;
        e.pots[TB_P_WT1_LEVEL] = 100; e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127; e.pots[TB_P_VOLUME] = 10; e.pots[TB_P_WT1_LEVEL] = 20;       /* quiet enough that the output limiter is not engaged */
        float *rp = e.pots;
        if (rack == 1) { e.pots[TB_P_WT1_POST] = 2; e.pots[TB_P_WT1_BUS1] = 127; rp = e.busPots[0]; }
        rp[TB_P_UTIL_OUT] = (float) base;
        for (int k = 0; k < 16; k++) e.pots[TB_P_LFO1_S0 + k] = 127;          /* the LFO sits at 1 */
        e.pots[TB_P_LFO1_RATE] = 0;
        if (route) { e.pots[TB_P_LR1_SRC] = 1; e.pots[TB_P_LR1_DST] = rack == 1 ? 37 : 36; e.pots[TB_P_LR1_AMT] = 127; e.pots[TB_P_LR1_BIP] = 0; }
        note(e, 72, 100);
        auto x = render(e, 200);
        double a = 0;
        for (size_t i = x.size() / 2; i < x.size(); i += 2) a += (double) x[i] * x[i];
        return 10.0 * std::log10(a / (x.size() / 4) + 1e-9);
    };
    for (int rack = 0; rack <= 1; rack++) {
        const double plain = level(rack, 0, false);
        const double half = level(rack, 64, false);         /* knob 0.504, no route: unity */
        const double quiet = level(rack, 32, false);        /* knob 0.252, no route: (2 x 0.252)^2 = -11.9 dB */
        const double opened = level(rack, 1, true);         /* a knob near 0 and the LFO at 1 with a full route: knob 1.0, +12 dB */
        CHECK(std::fabs(half - plain) < 0.3, "rack %d: a Utils Level Out stored at its default leaves the level alone (%.2f vs %.2f dB)", rack, half, plain);
        CHECK(std::fabs(quiet - (plain - 11.9)) < 0.6, "rack %d: a Level Out stored at 0.25 takes it down 12 dB (%.2f against %.2f)", rack, quiet, plain - 11.9);
        CHECK(std::fabs(opened - (plain + 12.04)) < 0.6, "rack %d: a full LFO route opens it to +12 dB (%.2f against %.2f)", rack, opened, plain + 12.04);
    }
}

/* A sample voice plays for as long as its sample. A dangling-if in Voice::process ran smp.release() on every block, not only when the voice ended: every sample
 * preset played its first 128 samples (3 ms) and nothing after, and the brass and strings multisamples, which fade in, were silent. */
static void testSampleVoicePlaysThrough()
{
    printf("\n== sample voice plays through ==\n");
    auto set = std::make_shared<SampleSet>();
    SampleRegion r;
    r.rate = 44100.0f; r.root = 60; r.frames = 44100 * 2;
    r.pcm.resize((size_t) r.frames);
    for (int i = 0; i < r.frames; i++) r.pcm[(size_t) i] = (int16_t) (12000.0 * std::sin(2.0 * M_PI * 261.63 * i / 44100.0));
    set->regions.push_back(std::move(r));
    Engine e;
    e.pots[TB_P_WT1_LEVEL] = 0;
    e.pots[TB_P_VCA_A] = 0;
    e.pots[TB_P_VCA_S] = 127;
    e.pots[TB_P_SMP_LEVEL] = 110;
    e.setSample(set);
    note(e, 60);
    auto a = render(e, 600);
    std::vector<int16_t> head(a.begin(), a.begin() + 256 * 100), tail(a.end() - 256 * 100, a.end());
    CHECK(rms(head) > 500.0, "the first 100 blocks are audible (rms %.0f)", rms(head));
    CHECK(rms(tail) > 0.5 * rms(head), "and so are blocks 500-600, a second and a half in (rms %.0f against %.0f)", rms(tail), rms(head));
}

/* A sample slot in its Granular kind (granular.h): audible through the voice, silent at Density 0, a spare-envelope route to Density wakes it, the Sample kind is untouched. */
static void testGranularSlot()
{
    printf("\n== granular sample slot ==\n");
    auto set = std::make_shared<SampleSet>();
    SampleRegion r;
    r.rate = 44100.0f; r.root = 60; r.frames = 44100 * 4;
    r.pcm.resize((size_t) r.frames);
    for (int i = 0; i < r.frames; i++) r.pcm[(size_t) i] = (int16_t) (12000.0 * std::sin(2.0 * M_PI * 261.63 * i / 44100.0));
    set->regions.push_back(std::move(r));
    auto base = [&](Engine &e) {
        e.pots[TB_P_WT1_LEVEL] = 0; e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127;
        e.pots[TB_P_SMP_LEVEL] = 110; e.setSample(set);
    };
    double sampleRms = 0.0;
    { Engine e; base(e); note(e, 60); sampleRms = rms(render(e, 300)); CHECK(sampleRms > 500.0, "the Sample kind still plays (rms %.0f)", sampleRms); }
    { Engine e; base(e); e.pots[TB_P_SMP_KIND] = 1; e.pots[TB_P_SMP_GSTART] = 25; note(e, 60);
      const double g = rms(render(e, 300)); CHECK(g > 500.0, "the Granular kind plays (rms %.0f)", g); }
    { Engine e; base(e); e.pots[TB_P_SMP_KIND] = 1; e.pots[TB_P_SMP_GDENS] = 0; note(e, 60);
      const double g = rms(render(e, 300)); CHECK(g < 1.0, "Density 0 is silence (rms %.1f)", g); }
    { Engine e; base(e); e.pots[TB_P_SMP_KIND] = 1; e.pots[TB_P_SMP_GDENS] = 0; e.pots[TB_P_SMP_GSTART] = 25;
      e.pots[TB_P_ME1_DST] = MD_SMP1_GDENS; e.pots[TB_P_ME1_AMT] = 100; e.pots[TB_P_ME1_A] = 0; e.pots[TB_P_ME1_S] = 127; note(e, 60);
      const double g = rms(render(e, 300)); CHECK(g > 100.0, "an envelope route to Density wakes it (rms %.0f)", g); }
    { Engine e; base(e); e.pots[TB_P_SMP_KIND] = 1; e.pots[TB_P_SMP_GSCAN] = 0; e.pots[TB_P_SMP_LEVEL] = 0; note(e, 60);
      const double g = rms(render(e, 300)); CHECK(g < 1.0, "level 0 is silence (rms %.1f)", g); }
    { Engine e; base(e); e.pots[TB_P_SMP_KIND] = 1; e.pots[TB_P_SMP_GSCAN] = 0; note(e, 60); render(e, 100);
      const uint8_t off[] = { 0x80, 60, 0 }; e.onMidi(off, 3); render(e, 600);
      CHECK(e.activeStreams() == 0, "a released note ends"); }

    /* ---- tempo scan, ScanRange, the BPM grids and unison (measured on Serum, docs/GRANULAR_PROBE_NOTES.md) ---- */
    {
        auto ramp = std::make_shared<SampleSet>();
        SampleRegion rr; rr.rate = 44100.0f; rr.root = 60; rr.frames = 44100 * 4; rr.pcm.resize((size_t) rr.frames);
        for (int i = 0; i < rr.frames; i++) rr.pcm[(size_t) i] = (int16_t) (24000.0 * i / rr.frames);
        ramp->regions.push_back(std::move(rr));
        /* where the scan is at about 1.05 s, read off the ramp: a fraction of the 4 s file */
        auto posAt1s = [&](auto setup) {
            Engine e; e.pots[TB_P_WT1_LEVEL] = 0; e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127; e.pots[TB_P_SMP_LEVEL] = 127; e.setSample(ramp);
            e.pots[TB_P_SMP_KIND] = 1; e.pots[TB_P_SMP_GDENS] = 100; e.pots[TB_P_SMP_GLEN] = 40; e.pots[TB_P_SMP_GWINP] = 100;       /* many short grains */
            e.setHostBpm(120.0f);
            setup(e); note(e, 60);
            auto y = render(e, 4 * 172);                                                 /* 1.0 s ... */
            const int f0 = 44100, f1 = 44100 + 4410;
            double acc = 0.0, ref = 0.0;
            for (int i = f0; i < f1; i++) acc += y[(size_t) i * 2];
            return acc / (f1 - f0);
        };
        const double base = posAt1s([](Engine &) {});
        CHECK(base > 100.0, "the ramp read at Scan 100 %% gives a level (%.0f)", base);
        const double x2 = posAt1s([](Engine &e) { e.pots[TB_P_SMP_GSCX] = 1; });
        CHECK(std::fabs(x2 / base - 2.0) < 0.15, "ScanRange 1 runs the scan at twice the speed (%.2f x)", x2 / base);
        const double lock = posAt1s([](Engine &e) { e.pots[TB_P_SMP_GSCM] = 1; e.pots[TB_P_SMP_GBASE] = 60; });
        CHECK(std::fabs(lock / base - 2.0) < 0.15, "Tempo lock at base 60 under 120 BPM: twice the speed (%.2f x)", lock / base);
        const double sync = posAt1s([](Engine &e) { e.pots[TB_P_SMP_GSCM] = 2; e.pots[TB_P_SMP_GSBR] = 2; });
        CHECK(std::fabs(sync / base - 1.0) < 0.15, "Tempo sync step 2 on a 4 s file at 120 BPM: 1 s of file per second (%.2f x)", sync / base);
        const double sync3 = posAt1s([](Engine &e) { e.pots[TB_P_SMP_GSCM] = 2; e.pots[TB_P_SMP_GSBR] = 3; });
        CHECK(std::fabs(sync3 / base - 2.0) < 0.15, "step 3: twice that (%.2f x)", sync3 / base);
    }
    {   /* BPM density: births per second, whatever the grain length; BPM length: the grain lasts 1/128 s x 2^i */
        auto dc = std::make_shared<SampleSet>();
        SampleRegion dr; dr.rate = 44100.0f; dr.root = 60; dr.frames = 44100 * 8; dr.pcm.assign((size_t) dr.frames, (int16_t) 12000);
        dc->regions.push_back(std::move(dr));
        auto duty = [&](float kLen, float kDen, bool lenBpm, bool denBpm, float bpm) {
            Engine e; e.pots[TB_P_WT1_LEVEL] = 0; e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127; e.pots[TB_P_SMP_LEVEL] = 127; e.setSample(dc);
            e.pots[TB_P_SMP_KIND] = 1; e.pots[TB_P_SMP_GSCAN] = 0; e.pots[TB_P_SMP_GWINP] = 100; e.setHostBpm(bpm);
            e.pots[TB_P_SMP_GDENS] = 127.0f * kDen; e.pots[TB_P_SMP_GLEN] = 127.0f * kLen; e.pots[TB_P_SMP_GLENM] = lenBpm ? 1.0f : 0.0f; e.pots[TB_P_SMP_GDENM] = denBpm ? 1.0f : 0.0f;
            note(e, 60);
            auto y = render(e, 8 * 172);
            int on = 0; const int frames = (int) y.size() / 2;
            for (int i = 0; i < frames; i++) on += std::abs(y[(size_t) i * 2]) > 20 ? 1 : 0;
            return (double) on / frames;
        };
        /* knob 0.3 -> rate 2^-2 = 0.25 births/s; length knob 0.4 -> i = 5 -> 0.25 s: 1/16 of the time is sounding */
        const double d120 = duty(0.4f, 0.3f, true, true, 120.0f);
        CHECK(std::fabs(d120 - 0.0625) < 0.012, "BPM density 0.25 births/s x BPM length 0.25 s: %.3f of the time sounding (1/16)", d120);
        const double d60 = duty(0.4f, 0.3f, true, true, 60.0f);
        CHECK(std::fabs(d60 - 0.125) < 0.015, "at 60 BPM the grain lasts twice as long, 0.5 s of the 4 s heard (births are half as fast, so still one): %.3f", d60);
        const double dfree = duty(0.4f, 0.3f, false, false, 120.0f);
        CHECK(dfree > 0.5, "the free modes at the same knobs are much denser (%.2f)", dfree);
    }
    {   /* Unison: a copy of every grain, detuned across 4 d^1.25 semitones, the first voice to the left, the last to the right, 1/sqrt(n) in level */
        auto click = std::make_shared<SampleSet>();
        SampleRegion cr; cr.rate = 44100.0f; cr.root = 60; cr.frames = 44100 * 4; cr.pcm.assign((size_t) cr.frames, (int16_t) 0); cr.pcm[(size_t) (0.05 * 44100)] = 29000;
        click->regions.push_back(std::move(cr));
        Engine e; e.pots[TB_P_WT1_LEVEL] = 0; e.pots[TB_P_VCA_A] = 0; e.pots[TB_P_VCA_S] = 127; e.pots[TB_P_SMP_LEVEL] = 127; e.setSample(click);
        e.pots[TB_P_SMP_KIND] = 1; e.pots[TB_P_SMP_GSCAN] = 0; e.pots[TB_P_SMP_GDENS] = 30; e.pots[TB_P_SMP_GLEN] = 40; e.pots[TB_P_SMP_GWINP] = 0;
        e.pots[TB_P_SMP_UNI] = 2; e.pots[TB_P_SMP_DET] = 127.0f * 0.5f; e.pots[TB_P_SMP_SPREAD] = 127; e.pots[TB_P_UNI_CAP] = 8;
        note(e, 60);
        auto y = render(e, 40);
        int il = -1, ir = -1, pl = 0, pr = 0;
        for (int i = 1764; i < 2646; i++) { if (std::abs(y[(size_t) i * 2]) > pl) { pl = std::abs(y[(size_t) i * 2]); il = i; } if (std::abs(y[(size_t) i * 2 + 1]) > pr) { pr = std::abs(y[(size_t) i * 2 + 1]); ir = i; } }
        const double ms = 1000.0 * std::abs(ir - il) / 44100.0;          /* the lower voice (left) arrives later */
        CHECK(pl > 0 && pr > 0 && std::fabs(ms - 4.85) < 0.35, "unison 2, detune 0.5: the right voice's click comes %.2f ms after the left's (Serum 4.85)", ms);
        int lOther = std::abs(y[(size_t) ir * 2]), rOther = std::abs(y[(size_t) il * 2 + 1]);
        CHECK(lOther < pl / 20 && rOther < pr / 20, "at full spread each voice is hard to its own side (leak %d / %d of %d / %d)", lOther, rOther, pl, pr);
    }
}

/* Macros: a macro pot moves its routed parameters by travel x change / 127 and leaves the rest; the preset's own values are where the macro sat when it loaded. */
static void testMacros()
{
    Engine e;
    e.pots[TB_P_FLT_FREQ] = 60;
    e.pots[TB_P_REV_MIX] = 10;
    e.pots[TB_P_MAC1] = 32; e.pots[TB_P_MAC2] = 100;
    e.macroSync();
    const Engine::MacroRoute r[] = { { 0, TB_P_FLT_FREQ, 40.0f }, { 0, TB_P_REV_MIX, -127.0f }, { 1, TB_P_FLT_FREQ, -20.0f } };
    e.setMacroRoutes(r, 3);
    CHECK(e.macroRouteCount() == 3, "three routes are taken");
    e.macroMoved(0, 32.0f);
    CHECK(e.pots[TB_P_FLT_FREQ] == 60.0f && e.pots[TB_P_REV_MIX] == 10.0f, "a macro that has not moved moves nothing");
    e.pots[TB_P_MAC1] = 127; e.macroMoved(0, 127.0f);
    CHECK(std::fabs(e.pots[TB_P_FLT_FREQ] - (60.0f + 40.0f * 95.0f / 127.0f)) < 1e-3f, "macro 1 up: the cutoff moves by travel x change / 127 (%.2f)", e.pots[TB_P_FLT_FREQ]);
    CHECK(e.pots[TB_P_REV_MIX] == 0.0f, "and a pot stops at its own floor");
    e.pots[TB_P_MAC1] = 32; e.macroMoved(0, 32.0f);
    CHECK(std::fabs(e.pots[TB_P_FLT_FREQ] - 60.0f) < 1e-3f, "back where it was, the cutoff is back (%.3f)", e.pots[TB_P_FLT_FREQ]);
    e.pots[TB_P_MAC2] = 0; e.macroMoved(1, 0.0f);
    CHECK(std::fabs(e.pots[TB_P_FLT_FREQ] - (60.0f + 20.0f * 100.0f / 127.0f)) < 1e-3f, "macro 2 moves the same cutoff on its own (%.2f)", e.pots[TB_P_FLT_FREQ]);
    e.pots[TB_P_FLT_FREQ] = 5; e.pots[TB_P_MAC1] = 0; e.macroSync();
    e.pots[TB_P_MAC1] = 127; e.macroMoved(0, 127.0f);
    CHECK(std::fabs(e.pots[TB_P_FLT_FREQ] - 45.0f) < 1e-3f, "a macro turned from a synced state moves from there (%.2f)", e.pots[TB_P_FLT_FREQ]);
    e.setMacroRoutes(nullptr, 0);
    e.pots[TB_P_MAC1] = 0; e.macroMoved(0, 0.0f);
    CHECK(std::fabs(e.pots[TB_P_FLT_FREQ] - 45.0f) < 1e-3f, "no routes: nothing moves");
}

int main()
{
    testMacros();
    testThirdOscillator();
    testSampleVoicePlaysThrough();
    testGranularSlot();
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
    testFxFilterLfo();
    testUtilOutLfo();
    testNoteOnset();
    testMonoTakeover();
    testClipPlayer();
    testSample2();
    testSamplePan();
    testArp();
    printf("\n%s (%d failures)\n", g_fail ? "ENGINE TESTS FAILED" : "ENGINE TESTS PASSED", g_fail);
    return g_fail ? 1 : 0;
}
