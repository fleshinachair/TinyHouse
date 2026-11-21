/* The render thread (src/dsp/async_render.h) against the callback render, through the real dsp.so.
 *
 *   1. Same sound: one instance renders on the callback (TB_RENDER_SYNC), one on its thread; both get the same preset and the same
 *      MIDI at the same blocks. The threaded one must play the other's block k as its block k + 1, sample for sample.
 *   2. Callback time: N instances (one preset each) played in real time, a block every 2.9 ms, first all on the callback, then
 *      all threaded; prints the time the "callback" (this loop) spends per block, mean / p99 / max.
 *
 * Run on the Move:  ./tablor_asynccheck ./dsp.so preset1.json [preset2.json ...]
 * TB_ASYNC_ALLOW_OTHER=1 lets the thread run at SCHED_OTHER where FIFO is refused (an unprivileged shell). */
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <cstdint>
#include <cmath>
#include <string>
#include <vector>
#include <algorithm>
#include <dlfcn.h>
#include <time.h>

#include "../src/host/plugin_api_v1.h"

static void host_log(const char *) {}
static double g_bpm = 124.0, g_beat = 0.0;
static float host_bpm() { return (float) g_bpm; }
static double host_beat() { return g_beat; }

static std::string slurp(const std::string &p)
{
    std::string s;
    FILE *f = fopen(p.c_str(), "rb");
    if (!f) return s;
    char b[8192];
    size_t n;
    while ((n = fread(b, 1, sizeof b, f)) > 0) s.append(b, n);
    fclose(f);
    return s;
}
static std::string blobOf(const std::string &file)
{
    const std::string raw = slurp(file);
    size_t s0 = raw.find("\"state\":");
    if (s0 == std::string::npos) return "";
    s0 = raw.find('"', s0 + 8) + 1;
    return raw.substr(s0, raw.find('"', s0) - s0);
}
static long long nowNs()
{
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long) ts.tv_sec * 1000000000LL + ts.tv_nsec;
}
static void sleepMs(int ms) { timespec ts = { ms / 1000, (long) (ms % 1000) * 1000000L }; nanosleep(&ts, nullptr); }

static plugin_api_v2_t *api;

static void waitIdle(void *inst)
{
    char b[16];
    for (int i = 0; i < 2000; i++) {
        api->get_param(inst, "ready", b, sizeof b);
        const bool ready = b[0] == '1';
        api->get_param(inst, "busy", b, sizeof b);
        if (ready && b[0] == '0') return;
        sleepMs(5);
    }
    printf("  (instance never went idle)\n");
}
static void *make(bool sync)
{
    if (sync) setenv("TB_RENDER_SYNC", "1", 1); else unsetenv("TB_RENDER_SYNC");
    void *inst = api->create_instance(".", nullptr);
    unsetenv("TB_RENDER_SYNC");
    if (!inst) { printf("create_instance failed\n"); exit(1); }
    return inst;
}
static void stats(void *inst, const char *tag)
{
    char b[200];
    api->get_param(inst, "async", b, sizeof b);
    printf("  %s: %s\n", tag, b);
}

/* The same MIDI for every instance: chords that change every 160 blocks, a few velocities, a pitch bend now and then. */
static void midiAt(void *inst, int blk, int seed)
{
    if (blk % 160 == 0) {
        const int root = 36 + ((blk / 160 + seed) * 5) % 24;
        const int iv[4] = { 0, 7, 12, 16 };
        for (int i = 0; i < 4; i++) {
            if (blk > 0) { const uint8_t off[3] = { 0x80, (uint8_t) (root - 5 + iv[i]), 0 }; api->on_midi(inst, off, 3, 0); }
            const uint8_t on[3] = { 0x90, (uint8_t) (root + iv[i]), (uint8_t) (60 + 15 * i) };
            api->on_midi(inst, on, 3, 0);
        }
    }
    if (blk % 97 == 50) { const uint8_t pb[3] = { 0xE0, 0, (uint8_t) (64 + (blk % 40) - 20) }; api->on_midi(inst, pb, 3, 0); }
}

int main(int argc, char **argv)
{
    setenv("TB_PRESET_SETTLE_MS", "0", 0);
    if (argc < 3) { printf("usage: %s dsp.so preset.json [preset.json ...]\n", argv[0]); return 2; }
    void *dl = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!dl) { printf("dlopen: %s\n", dlerror()); return 1; }
    auto init = (move_plugin_init_v2_fn) dlsym(dl, MOVE_PLUGIN_INIT_V2_SYMBOL);
    static host_api_v1_t host = {};
    host.api_version = 1; host.sample_rate = MOVE_SAMPLE_RATE; host.frames_per_block = MOVE_FRAMES_PER_BLOCK; host.log = host_log;
    host.get_bpm = host_bpm; host.get_beat_position = host_beat;
    api = init(&host);
    std::vector<std::string> blobs;
    for (int i = 2; i < argc; i++) { blobs.push_back(blobOf(argv[i])); if (blobs.back().empty()) { printf("no state in %s\n", argv[i]); return 1; } }
    const int F = MOVE_FRAMES_PER_BLOCK;
    int fails = 0;

    /* 1. same sound, for each preset */
    for (size_t p = 0; p < blobs.size(); p++) {
        /* c is a second callback instance: a preset whose two callback renders already differ (random phases, noise) cannot be compared. The callback
         * instances get the beat one block on, which is the beat the threaded one renders at (its block plays a block later). */
        void *a = make(true), *b = make(false), *c = make(true);
        waitIdle(a); waitIdle(b); waitIdle(c);
        api->set_param(a, "state", blobs[p].c_str());
        api->set_param(b, "state", blobs[p].c_str());
        api->set_param(c, "state", blobs[p].c_str());
        waitIdle(a); waitIdle(b); waitIdle(c);
        const int N = 1200;
        std::vector<int16_t> oa((size_t) N * F * 2), ob((size_t) N * F * 2), oc((size_t) N * F * 2);
        auto beatAt = [&](int k) { return (double) k * F / MOVE_SAMPLE_RATE * g_bpm / 60.0; };
        for (int k = 0; k < N; k++) {
            midiAt(a, k, 0); midiAt(b, k, 0); midiAt(c, k, 0);
            g_beat = beatAt(k + 1);
            api->render_block(a, &oa[(size_t) k * F * 2], F);
            api->render_block(c, &oc[(size_t) k * F * 2], F);
            g_beat = beatAt(k);
            api->render_block(b, &ob[(size_t) k * F * 2], F);
        }
        bool det = true;
        for (size_t i = 0; i < oa.size(); i++) if (oa[i] != oc[i]) { det = false; break; }
        if (!det) {
            printf("skip: same sound, preset %zu: two callback renders already differ (the preset is random), not comparable\n", p + 1);
            api->destroy_instance(a); api->destroy_instance(b); api->destroy_instance(c);
            continue;
        }
        long maxd = 0, peak = 0, badBlocks = 0;
        for (int k = 0; k + 1 < N; k++) {
            long bd = 0;
            for (int i = 0; i < F * 2; i++) {
                const long x = oa[(size_t) k * F * 2 + i], y = ob[(size_t) (k + 1) * F * 2 + i];
                bd = std::max(bd, labs(x - y)); peak = std::max(peak, labs(x));
            }
            if (bd) badBlocks++;
            maxd = std::max(maxd, bd);
        }
        long first = 0;
        for (int i = 0; i < F * 2; i++) first = std::max(first, labs((long) ob[(size_t) i]));
        const bool ok = maxd == 0 && first == 0 && peak > 0;
        printf("%s same sound, preset %zu: peak %ld, blocks that differ %ld of %d, max diff %ld, threaded block 0 %s\n", ok ? "ok:  " : "FAIL:", p + 1, peak, badBlocks, N - 1, maxd, first ? "NOT silent" : "silent");
        if (!ok) fails++;
        stats(b, "threaded");
        api->destroy_instance(a);
        api->destroy_instance(b);
        api->destroy_instance(c);
    }

    /* 2. callback time, real time, all presets at once */
    for (int mode = 0; mode < 2; mode++) {
        std::vector<void *> in;
        for (size_t p = 0; p < blobs.size(); p++) in.push_back(make(mode == 0));
        for (void *i : in) waitIdle(i);
        for (size_t p = 0; p < blobs.size(); p++) api->set_param(in[p], "state", blobs[p].c_str());
        for (void *i : in) waitIdle(i);
        const int N = 3000;                                      /* 8.7 s */
        std::vector<long long> t((size_t) N);
        static int16_t out[MOVE_FRAMES_PER_BLOCK * 2];
        const long long period = (long long) F * 1000000000LL / MOVE_SAMPLE_RATE;
        long long next = nowNs();
        for (int k = 0; k < N; k++) {
            g_beat = (double) k * F / MOVE_SAMPLE_RATE * g_bpm / 60.0;
            const long long t0 = nowNs();
            for (size_t p = 0; p < in.size(); p++) { midiAt(in[p], k, (int) p); api->render_block(in[p], out, F); }
            t[(size_t) k] = nowNs() - t0;
            next += period;
            timespec ts = { (time_t) (next / 1000000000LL), (long) (next % 1000000000LL) };
            clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, nullptr);
        }
        std::vector<long long> s(t.begin() + 200, t.end());       /* skip the first notes' start-up */
        std::sort(s.begin(), s.end());
        double mean = 0;
        for (long long v : s) mean += (double) v;
        mean /= (double) s.size();
        long over = 0;
        for (long long v : s) if (v > period) over++;
        printf("%s, %zu instances: callback per block mean %.0f us, p99 %lld us, max %lld us, over the %lld us block %ld times\n", mode ? "threaded" : "callback", in.size(),
               mean / 1000.0, s[(size_t) (s.size() * 0.99)] / 1000, s.back() / 1000, period / 1000, over);
        if (mode) for (size_t p = 0; p < in.size(); p++) stats(in[p], ("instance " + std::to_string(p + 1)).c_str());
        for (void *i : in) api->destroy_instance(i);
    }
    printf(fails ? "ASYNCCHECK FAILED (%d)\n" : "ASYNCCHECK PASSED\n", fails);
    return fails ? 1 : 0;
}
