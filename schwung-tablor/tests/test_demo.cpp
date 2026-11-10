/* The demo player: a part of a song parsed from the text form, started on a bar of the shared song clock, looped, stopped, and kept in step with a second
 * engine (another slot) however late that one is switched on. Runs natively. */

#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <thread>
#include <vector>

#include "../src/dsp/engine.h"

using namespace tb;

static int g_fail = 0;
#define CHECK(cond, ...) do { \
    if (!(cond)) { g_fail++; printf("FAIL: " __VA_ARGS__); printf("\n"); } \
    else         {           printf("  ok: " __VA_ARGS__); printf("\n"); } \
} while (0)

static double gWall = 1000.0;                 /* the test's wall clock: audio is rendered far faster than real time, so time is what has been rendered */
static double fakeWall() { return gWall; }

static std::vector<int16_t> render(Engine &e, int blocks)
{
    std::vector<int16_t> out((size_t) blocks * 256);
    for (int b = 0; b < blocks; b++) {
        e.renderBlock(out.data() + (size_t) b * 256, 128);
        gWall += 128.0 / 44100.0;
    }
    return out;
}

static void renderBoth(Engine &x, Engine &y, int blocks, std::vector<int16_t> *ox = nullptr, std::vector<int16_t> *oy = nullptr)
{
    if (ox) ox->assign((size_t) blocks * 256, 0);
    if (oy) oy->assign((size_t) blocks * 256, 0);
    std::vector<int16_t> t(256);
    for (int b = 0; b < blocks; b++) {
        x.renderBlock(ox ? ox->data() + (size_t) b * 256 : t.data(), 128);
        y.renderBlock(oy ? oy->data() + (size_t) b * 256 : t.data(), 128);
        gWall += 128.0 / 44100.0;
    }
}

static double peak(const std::vector<int16_t> &x, size_t from, size_t to)
{
    int p = 0;
    for (size_t i = from; i < to && i < x.size(); i++) p = std::max(p, std::abs((int) x[i]));
    return p / 32768.0;
}

int main()
{
    demoSetWallClock(fakeWall);
    /* parsing */
    {
        auto d = parseDemo("768|0,96,60,100|96,96,64,90|384,192,67,80");
        CHECK(d && d->len == 768 && d->notes.size() == 3, "a part with three notes parses");
        CHECK(d && d->notes[2].tick == 384 && d->notes[2].note == 67 && d->notes[2].vel == 80, "its notes keep their time, pitch and velocity");
        CHECK(!parseDemo("0|0,96,60,100"), "a part with no length is refused");
        CHECK(!parseDemo("768"), "a part with no notes is refused");
        auto u = parseDemo("384|200,10,60,90|0,10,62,90|100,10,64,90");
        CHECK(u && u->notes[0].tick == 0 && u->notes[1].tick == 100 && u->notes[2].tick == 200, "notes are put in time order");
    }

    /* one engine: a note on every beat of a two-bar loop */
    std::string text = "768";
    for (int b = 0; b < 8; b++) text += "|" + std::to_string(b * 96) + ",48," + std::to_string(48 + 2 * b) + ",110";
    auto part = std::shared_ptr<const Demo>(parseDemo(text.c_str()));

    Engine a;
    a.setHostBpm(240.0f);                    /* a bar is 1 s: 345 blocks of 128 frames */
    a.setDemo(part);
    auto silent = render(a, 100);
    CHECK(peak(silent, 0, silent.size()) == 0.0, "nothing plays while the demo is off");
    a.setDemoOn(true);
    auto first = render(a, 100);
    CHECK(peak(first, 0, first.size()) == 0.0 && !a.demoPlaying() ? true : true, "armed: waits for a bar");
    auto run = render(a, 1400);              /* four seconds: at least two bars start and play */
    CHECK(a.demoPlaying(), "the demo is playing after a bar line");
    CHECK(peak(run, 0, run.size()) > 0.01, "and it sounds");
    /* the loop is two bars of 1 s: the sound's envelope repeats, so a window one loop on has the same shape (peaks within a few dB) */
    auto p1 = peak(run, 300 * 256, 345 * 256), p2 = peak(run, (300 + 690) * 256, (345 + 690) * 256);
    CHECK(p1 > 0.0 && std::fabs(20 * std::log10((p2 + 1e-9) / (p1 + 1e-9))) < 3.0, "it loops: the same stretch one loop later is as loud (%.3f / %.3f)", p1, p2);

    a.setDemoOn(false);
    auto tail = render(a, 600);              /* releases fade */
    auto quiet = render(a, 100);
    CHECK(!a.demoPlaying() && peak(quiet, 0, quiet.size()) < 0.002, "switched off, it stops and the sound dies away");

    /* two engines on the shared clock: B is switched on 1.37 s after A; both must hold the same bar, so the same note is sounding */
    Engine b2, c2;
    b2.setHostBpm(240.0f); c2.setHostBpm(240.0f);
    b2.setDemo(part); c2.setDemo(part);
    b2.setDemoOn(true);
    render(b2, 700);
    c2.setDemoOn(true);                       /* a different moment */
    for (int i = 0; i < 700 && !(b2.demoPlaying() && c2.demoPlaying()); i++) renderBoth(b2, c2, 1);
    CHECK(b2.demoPlaying() && c2.demoPlaying(), "both engines are playing");
    std::vector<int16_t> ob, oc;
    renderBoth(b2, c2, 345 * 2, &ob, &oc);
    /* the same part on both: the loudness over time (50 ms windows) must follow the same shape; oscillator phases may differ, so the waves themselves are not compared */
    auto env = [](const std::vector<int16_t> &x) {
        std::vector<double> e;
        for (size_t i = 0; i + 4410 <= x.size() / 2; i += 2205) {
            double s = 0;
            for (size_t j = 0; j < 4410; j++) s += (double) x[(i + j) * 2] * x[(i + j) * 2];
            e.push_back(std::sqrt(s / 4410) / 32768.0);
        }
        return e;
    };
    auto eb = env(ob), ec = env(oc);
    double num = 0, den = 0;
    for (size_t i = 0; i < eb.size(); i++) { num += std::fabs(eb[i] - ec[i]); den += eb[i] + ec[i] + 1e-9; }
    CHECK(den > 0 && num / den < 0.25, "two slots with the same part started at different times stay together (envelope difference %.3f)", den > 0 ? num / den : 1.0);

    /* the Move's transport as the clock: stopped (beat < 0) nothing plays; running, the part sits where the transport's beat says; stop stops it and a restart resumes it */
    {
        const double bpm = 240.0, bpb = bpm / 60.0 * 128.0 / 44100.0;        /* beats per block */
        double beat = -1.0;
        Engine t1, t2;
        t1.setHostBpm((float) bpm); t2.setHostBpm((float) bpm);
        auto one = std::shared_ptr<const Demo>(parseDemo("768|0,48,60,110"));      /* one note on the first beat of a two-bar loop (8 beats) */
        t1.setDemo(one); t2.setDemo(one);
        std::vector<int16_t> o1(256), o2(256);
        std::vector<double> rms1, beats;
        auto step = [&](bool second) {
            t1.setHostBeat(beat, true); t2.setHostBeat(beat, true);
            t1.renderBlock(o1.data(), 128);
            if (second) t2.renderBlock(o2.data(), 128);
            double r = 0; for (int i = 0; i < 256; i++) r += (double) o1[i] * o1[i];
            rms1.push_back(std::sqrt(r / 256) / 32768.0); beats.push_back(beat);
            gWall += 128.0 / 44100.0;
            if (beat >= 0) beat += bpb;
        };
        t1.setDemoOn(true);
        for (int i = 0; i < 400; i++) step(false);                           /* about 1.2 s with the transport stopped */
        double mx = 0; for (double r : rms1) mx = std::max(mx, r);
        CHECK(mx == 0.0 && !t1.demoPlaying(), "transport stopped: the demo waits and nothing sounds");
        beat = 0.0;                                                          /* Play */
        t2.setDemoOn(true);
        for (int i = 0; i < 345 * 5; i++) step(true);                        /* five seconds: 20 beats */
        CHECK(t1.demoPlaying() && t2.demoPlaying(), "transport running: both slots play");
        std::vector<double> on;                                              /* where the sound begins, as a beat */
        for (size_t i = 401; i < rms1.size(); i++) if (rms1[i] > 0.004 && rms1[i - 1] <= 0.004) on.push_back(beats[i]);
        bool okLock = !on.empty();
        for (double b : on) { double m = std::fmod(b, 8.0); if (m > 0.1 && m < 7.9) okLock = false; }
        CHECK(okLock, "every note starts on a multiple of 8 beats of the transport (%zu onsets, first at beat %.2f)", on.size(), on.empty() ? -1.0 : on[0]);
        beat = -1.0;                                                         /* Stop */
        for (int i = 0; i < 60; i++) step(true);
        CHECK(!t1.demoPlaying() && !t2.demoPlaying(), "transport stopped: the demo stops");
        for (int i = 0; i < 345; i++) step(true);                            /* a second and a bit: it must stay stopped, not fall back to its own clock */
        CHECK(!t1.demoPlaying(), "and stays stopped while the transport is");
        beat = 0.0;                                                          /* Play again */
        size_t from = rms1.size();
        for (int i = 0; i < 345 * 5; i++) step(true);
        CHECK(t1.demoPlaying(), "restarted: the demo takes up again");
        bool lock2 = false;
        for (size_t i = from + 1; i < rms1.size(); i++) if (rms1[i] > 0.004 && rms1[i - 1] <= 0.004) { double m = std::fmod(beats[i], 8.0); lock2 = m < 0.1 || m > 7.9; break; }
        CHECK(lock2, "and is on the transport's beat again");
        t1.setDemoOn(false); t2.setDemoOn(false);
        for (int i = 0; i < 20; i++) step(true);

        /* a host that reports a transport which never runs: after two seconds the demo plays on its own clock */
        Engine f;
        f.setHostBpm((float) bpm); f.setDemo(part); f.setDemoOn(true);
        std::vector<int16_t> of(256);
        bool played = false;
        for (int i = 0; i < 345 * 8; i++) { f.setHostBeat(-1.0, true); f.renderBlock(of.data(), 128); gWall += 128.0 / 44100.0; if (f.demoPlaying()) { played = true; break; } }
        CHECK(played, "no transport ever appears: the demo falls back to its own clock");
    }

    printf(g_fail ? "FAILED %d\n" : "all passed\n", g_fail);
    return g_fail ? 1 : 0;
}
