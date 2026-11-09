/* Preset soak: play every preset of a folder through the real dsp.so the way a player does -- chords, velocities, pedal, pitch bend, and a
 * preset switch while notes still ring -- looking for crashes. Prints each preset's name BEFORE playing it (flushed), so the last line
 * names the culprit when the process dies.
 *
 * Run on the Move:  ./tablor_soak ./dsp.so "/data/UserData/schwung/presets/tinyhouse/Factory" [rounds] [seed] [settle 0|1]
 */
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <cstdint>
#include <cmath>
#include <string>
#include <vector>
#include <algorithm>
#include <dlfcn.h>
#include <dirent.h>
#include <time.h>

#include "../src/host/plugin_api_v1.h"

static void host_log(const char *) {}

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

int main(int argc, char **argv)
{
    const char *so = argc > 1 ? argv[1] : "./dsp.so";
    const std::string dir = argc > 2 ? argv[2] : "/data/UserData/schwung/presets/tinyhouse/Factory";
    const int rounds = argc > 3 ? atoi(argv[3]) : 6;
    srand(argc > 4 ? (unsigned) atoi(argv[4]) : 1u);
    const bool doSettle = argc > 5 ? atoi(argv[5]) != 0 : false;

    void *dl = dlopen(so, RTLD_NOW | RTLD_LOCAL);
    if (!dl) { printf("dlopen: %s\n", dlerror()); return 1; }
    auto init = (move_plugin_init_v2_fn) dlsym(dl, MOVE_PLUGIN_INIT_V2_SYMBOL);
    host_api_v1_t host = {};
    host.api_version = 1; host.sample_rate = MOVE_SAMPLE_RATE; host.frames_per_block = MOVE_FRAMES_PER_BLOCK; host.log = host_log;
    plugin_api_v2_t *api = init(&host);
    void *inst = api->create_instance(".", nullptr);
    if (!inst) { printf("create_instance failed\n"); return 1; }

    std::vector<std::string> files;
    if (DIR *d = opendir(dir.c_str())) {
        while (dirent *e = readdir(d)) {
            const std::string n = e->d_name;
            if (n.size() > 5 && n.substr(n.size() - 5) == ".json") files.push_back(n);
        }
        closedir(d);
    }
    std::sort(files.begin(), files.end());
    printf("%zu presets in %s, %d rounds\n", files.size(), dir.c_str(), rounds);
    fflush(stdout);

    static int16_t out[MOVE_FRAMES_PER_BLOCK * 2];
    int bad = 0;
    auto run = [&](int blocks, long &peak) {
        for (int b = 0; b < blocks; b++) {
            api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
            for (int i = 0; i < MOVE_FRAMES_PER_BLOCK * 2; i++) { long v = labs(out[i]); if (v > peak) peak = v; }
        }
    };
    for (const std::string &f : files) {
        printf("%s\n", f.c_str());
        fflush(stdout);
        const std::string raw = slurp(dir + "/" + f);
        size_t s0 = raw.find("\"state\":");
        if (s0 == std::string::npos) { printf("  no state\n"); continue; }
        s0 = raw.find('"', s0 + 8) + 1;                     /* the opening quote, with or without a space after the colon */
        const size_t e0 = raw.find('"', s0);
        const std::string blob = raw.substr(s0, e0 - s0);
        /* the switch lands while the previous preset's notes may still ring */
        api->set_param(inst, "state", blob.c_str());
        if (doSettle) {
            char b[8];
            for (int i = 0; i < 400; i++) { api->get_param(inst, "busy", b, sizeof b); if (b[0] == '0') break; struct timespec ts = { 0, 5000000 }; nanosleep(&ts, nullptr); }
        }
        long peak = 0;
        for (int r = 0; r < rounds; r++) {
            const int nn = 1 + rand() % 6;
            uint8_t notes[6];
            const int root = 24 + rand() % 60;
            for (int i = 0; i < nn; i++) {
                notes[i] = (uint8_t) std::min(120, root + (i ? rand() % 24 : 0));
                const uint8_t m[3] = { 0x90, notes[i], (uint8_t) (20 + rand() % 108) };
                api->on_midi(inst, m, 3, 0);
                run(rand() % 3, peak);
            }
            if (rand() % 4 == 0) { const uint8_t pb[3] = { 0xE0, 0, (uint8_t) (rand() % 128) }; api->on_midi(inst, pb, 3, 0); }
            if (rand() % 4 == 0) { const uint8_t mw[3] = { 0xB0, 1, (uint8_t) (rand() % 128) }; api->on_midi(inst, mw, 3, 0); }
            if (rand() % 3 == 0) { const uint8_t pd[3] = { 0xB0, 64, 127 }; api->on_midi(inst, pd, 3, 0); }
            run(10 + rand() % 300, peak);
            if (rand() % 3) for (int i = 0; i < nn; i++) { const uint8_t m[3] = { 0x80, notes[i], 0 }; api->on_midi(inst, m, 3, 0); }
            const uint8_t pu[3] = { 0xB0, 64, 0 };
            if (rand() % 2) api->on_midi(inst, pu, 3, 0);
            run(rand() % 200, peak);
        }
        const uint8_t off[3] = { 0xB0, 123, 0 };
        if (rand() % 2) api->on_midi(inst, off, 3, 0);     /* sometimes the next preset loads over live notes */
        run(30, peak);
        if (peak == 0) { printf("  SILENT\n"); bad++; }
    }
    printf("soak done, %zu presets, %d silent\n", files.size(), bad);
    return 0;
}
