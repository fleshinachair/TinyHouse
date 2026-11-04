/* How long does a block take while a note rings out? Run on the Move:  ./tailbench ./dsp.so
 *
 * Loads the module the way the chain host does, plays a held chord on each of several effect-heavy
 * presets, releases it, and times every render_block call through the release and a long silent tail,
 * in three phases: the held note, the first two seconds after release, and the deep tail (3 to 9 s after).
 * A block has 128 frames at 44.1 kHz, so 2.9 ms is the budget. Denormal slowdowns show as a deep-tail
 * mean or maximum that is HIGHER than the held note's, when the signal is getting quieter.
 */
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <dlfcn.h>
#include <ctime>
#include <algorithm>
#include <vector>
#include "../src/host/plugin_api_v1.h"

static void host_log(const char *) {}
static double now_ms()
{
    timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1000.0 + t.tv_nsec / 1e6;
}

int main(int argc, char **argv)
{
    const char *so = argc > 1 ? argv[1] : "./dsp.so";
    void *dl = dlopen(so, RTLD_NOW | RTLD_LOCAL);
    if (!dl) { printf("dlopen: %s\n", dlerror()); return 1; }
    auto init = (move_plugin_init_v2_fn) dlsym(dl, MOVE_PLUGIN_INIT_V2_SYMBOL);
    host_api_v1_t host = {};
    host.api_version = 1; host.sample_rate = MOVE_SAMPLE_RATE; host.frames_per_block = MOVE_FRAMES_PER_BLOCK; host.log = host_log;
    plugin_api_v2_t *api = init(&host);
    void *inst = api->create_instance(".", nullptr);
    struct timespec ts = { 0, 10 * 1000000 };
    for (int i = 0; i < 1500; i++) {
        char rb[8] = {};
        api->get_param(inst, "ready", rb, sizeof rb);
        if (rb[0] == '1') break;
        nanosleep(&ts, nullptr);
    }
    char cb[32] = {}, nb[256] = {};
    api->get_param(inst, "preset_count", cb, sizeof cb);
    const int count = atoi(cb);
    const char *want[] = { "Pad - MOBY", "Pad - Reiny", "Lead - Space & Time", "Keys - Loop", "Bass - Gravity", "Pluck - NOTSOBAD" };
    static int16_t out[MOVE_FRAMES_PER_BLOCK * 2];
    const int perSec = MOVE_SAMPLE_RATE / MOVE_FRAMES_PER_BLOCK;
    printf("%-22s %24s %24s %24s\n", "preset", "held (mean/max ms)", "0-2 s after (mean/max)", "3-9 s after (mean/max)");
    for (const char *w : want) {
        int idx = -1;
        for (int i = 0; i < count && idx < 0; i++) {
            char ib[16]; snprintf(ib, sizeof ib, "%d", i);
            api->set_param(inst, "preset", ib);
            api->get_param(inst, "preset_name", nb, sizeof nb);
            if (!strcmp(nb, w)) idx = i;
        }
        if (idx < 0) { printf("%-22s (not found)\n", w); continue; }
        for (int b = 0; b < 40; b++) api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
        const uint8_t on[4][3] = { { 0x90, 48, 100 }, { 0x90, 55, 100 }, { 0x90, 60, 100 }, { 0x90, 63, 100 } };
        for (auto &m : on) api->on_midi(inst, m, 3, 0);
        std::vector<double> held, near_, deep;
        for (int b = 0; b < perSec; b++) { double t = now_ms(); api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK); held.push_back(now_ms() - t); }
        for (auto &m : on) { uint8_t off[3] = { 0x80, m[1], 0 }; api->on_midi(inst, off, 3, 0); }
        for (int b = 0; b < 9 * perSec; b++) {
            double t = now_ms();
            api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
            const double d = now_ms() - t;
            if (b < 2 * perSec) near_.push_back(d); else if (b >= 3 * perSec) deep.push_back(d);
        }
        auto stat = [](const std::vector<double> &v, char *s) {
            double m = 0, x = 0;
            for (double d : v) { m += d; x = std::max(x, d); }
            snprintf(s, 40, "%.3f / %.3f", m / (double) v.size(), x);
        };
        char a[40], bb[40], c[40];
        stat(held, a); stat(near_, bb); stat(deep, c);
        printf("%-22s %24s %24s %24s\n", w, a, bb, c);
    }
    api->destroy_instance(inst);
    return 0;
}
