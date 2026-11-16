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
    const bool scrollMode = argc > 3 && !strcmp(argv[3], "scroll");
    if (!scrollMode) setenv("TB_PRESET_SETTLE_MS", "0", 0);       /* the one-by-one walk times the apply itself; the scroll runs as the Move does */
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
    struct R { double ms; double setms; int reload; double reloadAt; std::string name; };
    /* "scroll <ms>": turn the knob through the whole bank, one preset every <ms> milliseconds, and report how long is_loading stays at 1 after the last turn */
    if (argc > 3 && !strcmp(argv[3], "scroll")) {
        const double gap = argc > 4 ? atof(argv[4]) : 40;
        static int16_t blk[256];
        const double t0 = now_ms();
        for (int i = 0; i < n; i++) {
            char idx[16]; snprintf(idx, sizeof idx, "%d", i);
            api->set_param(inst, "preset", idx);
            char nm0[128] = {};
            api->get_param(inst, "preset_name", nm0, sizeof nm0);          /* the name must already be this preset's */
            if (i % 64 == 0) printf("  turn %d -> name read back: %s\n", i, nm0);
            for (double w = 0; w < gap; w += 2.9) { api->render_block(inst, blk, 128); struct timespec g = { 0, 2900000 }; nanosleep(&g, nullptr); }
        }
        const double tspin = now_ms() - t0, tlast = now_ms();
        for (;;) {
            api->render_block(inst, blk, 128);
            api->get_param(inst, "is_loading", b, sizeof b);
            if (b[0] == '0' || now_ms() - tlast > 120000) break;
            struct timespec g = { 0, 2900000 }; nanosleep(&g, nullptr);
        }
        printf("spun %d presets in %.0f ms (%.0f ms apart); is_loading stayed 1 for %.0f ms after the last turn\n", n, tspin, gap, now_ms() - tlast);
        static char st[16384], nmEnd[128];
        api->get_param(inst, "preset_name", nmEnd, sizeof nmEnd);
        api->get_param(inst, "state", st, sizeof st);
        const char *pk = strstr(st, "pk=");
        printf("  last name %s ; state names %.60s\n", nmEnd, pk ? pk : "(no pk)");
        api->destroy_instance(inst);
        return 0;
    }
    /* "contract": how many different chain_params strings does the bank produce? The host re-plans every page when the bytes it reads move. */
    if (argc > 3 && !strcmp(argv[3], "contract")) {
        static char big[65536], prev[65536];
        static int16_t blk[256];
        int changed = 0; size_t firstDiff = 0, lastLen = 0;
        for (int i = 0; i < n; i++) {
            char idx[16]; snprintf(idx, sizeof idx, "%d", i);
            api->set_param(inst, "preset", idx);
            for (int k = 0; k < 400; k++) { api->render_block(inst, blk, 128); api->get_param(inst, "is_loading", b, sizeof b); if (b[0] == '0' && k > 2) break; struct timespec g = { 0, 1000000 }; nanosleep(&g, nullptr); }
            api->get_param(inst, "chain_params", big, sizeof big);
            if (i && strcmp(big, prev)) {
                changed++;
                size_t d = 0; while (big[d] && big[d] == prev[d]) d++;
                if (changed <= 3) printf("  preset %d differs at byte %zu: ...%.50s\n", i, d, big + (d > 20 ? d - 20 : 0));
            }
            memcpy(prev, big, sizeof prev); lastLen = strlen(big); (void) firstDiff;
        }
        printf("chain_params: %d of %d presets changed it from the one before (%zu bytes)\n", changed, n, lastLen);
        api->destroy_instance(inst);
        return 0;
    }
    std::vector<R> rs;
    for (int i = 0; i < n; i++) {
        char idx[16]; snprintf(idx, sizeof idx, "%d", i);
        const double t0 = now_ms();
        api->set_param(inst, "preset", idx);                    /* this runs on the host's callback thread: how long does it hold it? */
        const double tset = now_ms() - t0;
        double t1 = t0;
        for (;;) {
            api->get_param(inst, "is_loading", b, sizeof b);
            t1 = now_ms();
            if (b[0] == '0') break;
            if (t1 - t0 > 20000) break;
            nanosleep(&ts, nullptr);
        }
        /* the host shows the name once is_loading reads 0: does it go back to 1 afterwards (the name would flash)? */
        int reload = 0; double reloadAt = 0; char last = '0';
        for (const double tend = t1 + 900; now_ms() < tend; nanosleep(&ts, nullptr)) {
            api->get_param(inst, "is_loading", b, sizeof b);
            if (b[0] == '1' && last == '0') { reload++; if (reload == 1) reloadAt = now_ms() - t1; }
            last = b[0];
        }
        char nm[256] = {};
        api->get_param(inst, "preset_name", nm, sizeof nm);
        rs.push_back({ t1 - t0, tset, reload, reloadAt, nm });
    }
    std::vector<R> s = rs;
    std::sort(s.begin(), s.end(), [](const R &a, const R &c) { return a.ms > c.ms; });
    std::vector<double> m; for (auto &r : rs) m.push_back(r.ms);
    std::sort(m.begin(), m.end());
    std::vector<double> sm; for (auto &r : rs) sm.push_back(r.setms);
    std::sort(sm.begin(), sm.end());
    int nre = 0; for (auto &r : rs) nre += r.reload > 0;
    printf("until name shown: median %.0f ms, p90 %.0f ms, max %.0f ms\n", m[m.size() / 2], m[m.size() * 9 / 10], m.back());
    printf("set_param itself (host callback thread): median %.1f ms, p90 %.1f ms, max %.1f ms\n", sm[sm.size() / 2], sm[sm.size() * 9 / 10], sm.back());
    printf("presets where is_loading went back to 1 after it read 0 (name would flash): %d of %d\n", nre, (int) rs.size());
    for (int i = 0; i < 25 && i < (int) s.size(); i++) printf("  %6.0f ms  set %5.1f ms  %s\n", s[i].ms, s[i].setms, s[i].name.c_str());
    int shown = 0;
    for (auto &r : rs) if (r.reload && shown++ < 15) printf("  FLASH x%d  back to 1 %.0f ms after reading 0:  %s\n", r.reload, r.reloadAt, r.name.c_str());
    api->destroy_instance(inst);
    return 0;
}
