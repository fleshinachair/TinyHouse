/* How long does the module hold the Move UI off after each preset change? Walks one bank, selects every preset and times the wait until is_loading
 * reads 0 (the host shows the preset name only after that). Run it on the device from the module folder:   ./presettime ./dsp.so <bank index>
 * Prints the slowest presets and the median. */
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <cstdint>
#include <dlfcn.h>
#include <ctime>
#include <string>
#include <vector>
#include <algorithm>
#include "../src/host/plugin_api_v1.h"

static void host_log(const char *) {}
static double now_ms() { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1e3 + t.tv_nsec / 1e6; }

int main(int argc, char **argv)
{
    const char *so = argc > 1 ? argv[1] : "./dsp.so";
    const char *bank = argc > 2 ? argv[2] : "0";
    void *dl = dlopen(so, RTLD_NOW | RTLD_LOCAL);
    if (!dl) { printf("dlopen: %s\n", dlerror()); return 1; }
    auto init = (move_plugin_init_v2_fn) dlsym(dl, MOVE_PLUGIN_INIT_V2_SYMBOL);
    host_api_v1_t host = {};
    host.api_version = 1; host.sample_rate = MOVE_SAMPLE_RATE; host.frames_per_block = MOVE_FRAMES_PER_BLOCK; host.log = host_log;
    plugin_api_v2_t *api = init(&host);
    void *inst = api->create_instance(".", nullptr);
    api->set_param(inst, "state", "TBLR2;");
    char b[64] = {};
    struct timespec ts = { 0, 2 * 1000000 };
    for (int i = 0; i < 5000; i++) { api->get_param(inst, "ready", b, sizeof b); if (b[0] == '1') break; nanosleep(&ts, nullptr); }
    api->set_param(inst, "bank", bank);
    char bn[128] = {}, pc[32] = {};
    api->get_param(inst, "bank_name", bn, sizeof bn);
    api->get_param(inst, "preset_count", pc, sizeof pc);
    const int n = atoi(pc);
    printf("bank %s: %d presets\n", bn, n);
    struct R { double ms; int flips; std::string name; };
    std::vector<R> rs;
    for (int i = 0; i < n; i++) {
        char idx[16]; snprintf(idx, sizeof idx, "%d", i);
        const double t0 = now_ms();
        api->set_param(inst, "preset", idx);
        int flips = 0; char last = 'x';
        double t1 = t0;
        for (;;) {
            api->get_param(inst, "is_loading", b, sizeof b);
            if (b[0] != last) { flips++; last = b[0]; }
            t1 = now_ms();
            if (b[0] == '0' && t1 - t0 > 0) break;
            if (t1 - t0 > 20000) break;
            nanosleep(&ts, nullptr);
        }
        char nm[256] = {};
        api->get_param(inst, "preset_name", nm, sizeof nm);
        rs.push_back({ t1 - t0, flips, nm });
    }
    std::vector<R> s = rs;
    std::sort(s.begin(), s.end(), [](const R &a, const R &c) { return a.ms > c.ms; });
    std::vector<double> m; for (auto &r : rs) m.push_back(r.ms);
    std::sort(m.begin(), m.end());
    printf("median %.0f ms, p90 %.0f ms, max %.0f ms\n", m[m.size() / 2], m[m.size() * 9 / 10], m.back());
    for (int i = 0; i < 25 && i < (int) s.size(); i++) printf("  %6.0f ms  %s\n", s[i].ms, s[i].name.c_str());
    api->destroy_instance(inst);
    return 0;
}
