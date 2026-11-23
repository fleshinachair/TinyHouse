/* Convert on load, on the Move: dlopens dsp.so as the chain host does and checks the upgrade path (src/dsp/reconvert.h, converter/reconvert.py).
 *
 *   ./tablor_upgradetest <dsp.so> <module dir holding converter/> <test bank>
 *
 * The test bank is a folder of the preset store (/data/UserData/schwung/presets/tinyhouse/<bank>) whose presets carry "src" (their originals under
 * /data/UserData/UserLibrary/TinyHouse Sources/<bank>) and a "conv" stamp other than the module's converter. It checks:
 *   1. the background run converts every one of them (the stamp becomes the module's),
 *   2. the helper runs at SCHED_OTHER, nice 19, off core 3 (the SPI callback's), though the instance was made on a SCHED_FIFO thread,
 *   3. render blocks keep their time meanwhile (the longest block, and how many took longer than a block lasts),
 *   4. a stale preset chosen from the page reads "Upgrade v..." in the header, then its name again, and its file is upgraded,
 *   5. destroying the instance while a conversion runs returns at once and leaves no helper behind.
 * The test makes its presets stale again itself (it rewrites their "conv"); it touches no other bank. */

#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <dirent.h>
#include <dlfcn.h>
#include <sched.h>
#include <string>
#include <sys/resource.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>
#include <chrono>
#include <algorithm>

#include "../src/host/plugin_api_v1.h"

static int g_fail = 0;
#define CHECK(cond, ...) do { if (!(cond)) { g_fail++; printf("FAIL: " __VA_ARGS__); printf("\n"); } else { printf("  ok: " __VA_ARGS__); printf("\n"); } } while (0)

static const char *kStore = "/data/UserData/schwung/presets/tinyhouse";
static void host_log(const char *msg) { printf("  [host log] %s\n", msg); }

static std::string slurp(const std::string &p)
{
    std::string s;
    if (FILE *f = fopen(p.c_str(), "r")) { char b[4096]; size_t n; while ((n = fread(b, 1, sizeof b, f)) > 0) s.append(b, n); fclose(f); }
    return s;
}
static std::string field(const std::string &raw, const char *key)
{
    const std::string k = std::string("\"") + key + "\"";
    size_t i = raw.find(k);
    if (i == std::string::npos) return "";
    i = raw.find('"', raw.find(':', i + k.size()));
    if (i == std::string::npos) return "";
    const size_t e = raw.find('"', i + 1);
    return e == std::string::npos ? "" : raw.substr(i + 1, e - i - 1);
}
static std::vector<std::string> bank_files(const std::string &bank)
{
    std::vector<std::string> v;
    const std::string d = std::string(kStore) + "/" + bank;
    if (DIR *dd = opendir(d.c_str())) {
        while (dirent *e = readdir(dd)) {
            const char *dot = strrchr(e->d_name, '.');
            if (e->d_name[0] != '.' && dot && !strcmp(dot, ".json")) v.push_back(d + "/" + e->d_name);
        }
        closedir(dd);
    }
    std::sort(v.begin(), v.end());
    return v;
}
/* Rewrite a preset's "conv" stamp (the test's way of making it stale). */
static void restamp(const std::string &p, const std::string &conv)
{
    std::string s = slurp(p);
    const size_t i = s.find("\"conv\"");
    if (i == std::string::npos) return;
    const size_t a = s.find('"', s.find(':', i + 6)), b = s.find('"', a + 1);
    s.replace(a + 1, b - a - 1, conv);
    if (FILE *f = fopen(p.c_str(), "w")) { fwrite(s.data(), 1, s.size(), f); fclose(f); }
}
static int count_current(const std::vector<std::string> &files, const std::string &hash)
{
    int n = 0;
    for (const auto &f : files) n += field(slurp(f), "conv") == hash;
    return n;
}
/* The helper process, if one runs: its pid (from /proc, by its command line). */
static int helper_pid()
{
    if (DIR *d = opendir("/proc")) {
        while (dirent *e = readdir(d)) {
            const int pid = atoi(e->d_name);
            if (pid <= 0) continue;
            std::string cl = slurp("/proc/" + std::to_string(pid) + "/cmdline");
            for (auto &c : cl) if (!c) c = ' ';
            if (cl.find("reconvert.py --serve") != std::string::npos) { closedir(d); return pid; }
        }
        closedir(d);
    }
    return -1;
}
static long long now_us() { return std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count(); }

struct Renderer {
    plugin_api_v2_t *api; void *inst;
    long long worst = 0; int late = 0, blocks = 0;
    int16_t out[MOVE_FRAMES_PER_BLOCK * 2];
    /* One block, timed; then sleep out the rest of the block's time, as the host's period does. */
    void block()
    {
        const long long t = now_us();
        api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
        const long long d = now_us() - t;
        worst = std::max(worst, d);
        late += d > 2902;
        blocks++;
        const long long rest = 2902 - d;
        if (rest > 0) { struct timespec ts = { 0, (long) rest * 1000 }; nanosleep(&ts, nullptr); }
    }
    void for_ms(int ms) { const long long end = now_us() + (long long) ms * 1000; while (now_us() < end) block(); }
};

int main(int argc, char **argv)
{
    if (argc < 4) { printf("usage: %s <dsp.so> <module dir> <test bank>\n", argv[0]); return 2; }
    setenv("TB_PRESET_SETTLE_MS", "0", 0);
    setvbuf(stdout, nullptr, _IOLBF, 0);                 /* every line out at once: a run the kernel stops still shows how far it got */
    const std::string so = argv[1], mod = argv[2], bank = argv[3];
    const std::string hash = field(slurp(mod + "/converter/conv_version.json"), "hash");
    const std::string ver = field(slurp(mod + "/converter/conv_version.json"), "version");
    CHECK(!hash.empty(), "module converter stamp %s (v%s)", hash.c_str(), ver.c_str());
    auto files = bank_files(bank);
    CHECK(files.size() >= 2, "%d presets in the test bank %s", (int) files.size(), bank.c_str());
    if (files.size() < 2 || hash.empty()) return 1;
    for (const auto &f : files) restamp(f, "test-stale");
    CHECK(count_current(files, hash) == 0, "every test preset made stale");
    /* A preset an import left out, waiting in the bank's waiting.json (rule "any": it is added once it converts) */
    const std::string srcdir = std::string("/data/UserData/UserLibrary/TinyHouse Sources/") + bank, waitdir = std::string(kStore) + "/" + bank + "_waiting";
    const std::string orig = field(slurp(files[0]), "src");
    const std::string origName = orig.substr(orig.rfind('/') + 1);
    if (FILE *f = fopen((srcdir + "/waiting.json").c_str(), "w")) {
        fprintf(f, "{\"presets\": {\"%s\": {\"name\": \"Waiting Test\", \"dir\": \"%s\", \"rule\": \"any\", \"tried\": \"\"}}}\n", origName.c_str(), waitdir.c_str());
        fclose(f);
    }
    CHECK(helper_pid() < 0, "no helper running before the test");

    void *dl = dlopen(so.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (!dl) { printf("FAIL: dlopen: %s\n", dlerror()); return 1; }
    auto init = (move_plugin_init_v2_fn) dlsym(dl, MOVE_PLUGIN_INIT_V2_SYMBOL);
    host_api_v1_t host = {};
    host.api_version = 1; host.sample_rate = MOVE_SAMPLE_RATE; host.frames_per_block = MOVE_FRAMES_PER_BLOCK; host.log = host_log;
    plugin_api_v2_t *api = init(&host);
    struct sched_param rp = {}; rp.sched_priority = 90;
    const bool rt = sched_setscheduler(0, SCHED_FIFO, &rp) == 0;          /* made on a real-time thread, as Schwung makes it */
    printf("  (instance made on a %s thread)\n", rt ? "SCHED_FIFO" : "normal (no RT permission)");
    void *inst = api->create_instance(mod.c_str(), nullptr);
    api->set_param(inst, "state", "TBLR2;");
    Renderer R{ api, inst };
    char b[256];
    for (int i = 0; i < 3000; i++) { api->get_param(inst, "ready", b, sizeof b); if (b[0] == '1') break; R.block(); }
    CHECK(b[0] == '1', "instance ready");

    /* 1-3: the background run */
    const long long t0 = now_us();
    int pid = -1, cur = 0;
    bool checked_sched = false;
    long long seen = 0;
    while (now_us() - t0 < 180000000LL) {
        R.for_ms(200);
        if (pid < 0) { pid = helper_pid(); if (pid > 0) seen = now_us(); }
        if (pid > 0 && !checked_sched && now_us() - seen > 2000000) {          /* once Python is up: it sets its own priority and core first thing */
            const std::string st = slurp("/proc/" + std::to_string(pid) + "/stat");
            int policy = -1, nice = 99; unsigned long long dummy;
            /* fields after the command (which ends with ')'): state is field 3; nice is 19; policy is 41 */
            const char *p = strrchr(st.c_str(), ')');
            if (p) {
                std::vector<long long> f; char *q = (char *) p + 2;
                while (*q) {
                    if (isspace((unsigned char) *q)) { q++; continue; }
                    if (f.empty()) { f.push_back(0); while (*q && !isspace((unsigned char) *q)) q++; continue; }     /* the state letter */
                    char *e = q;
                    f.push_back(strtoll(q, &e, 10));
                    if (e == q) break;                       /* not a number: stop (a newline at the end used to loop here forever) */
                    q = e;
                }
                if (f.size() > 39) { nice = (int) f[16]; policy = (int) f[38]; }
            }
            (void) dummy;
            cpu_set_t cs; CPU_ZERO(&cs);
            const bool aff = sched_getaffinity(pid, sizeof cs, &cs) == 0;
            CHECK(policy == SCHED_OTHER, "helper scheduling policy %d (SCHED_OTHER is %d)", policy, SCHED_OTHER);
            CHECK(nice == 19, "helper nice %d", nice);
            CHECK(aff && !CPU_ISSET(3, &cs) && CPU_COUNT(&cs) > 0, "helper kept off core 3 (the SPI callback's, src/dsp/rt.h)");
            checked_sched = true;
        }
        cur = count_current(files, hash);
        if (cur == (int) files.size()) break;
    }
    CHECK(pid > 0, "the helper started");
    CHECK(cur == (int) files.size(), "background run upgraded %d of %d presets in %.1f s", cur, (int) files.size(), (now_us() - t0) / 1e6);
    for (int i = 0; i < 100 && access((waitdir + "/Waiting Test.json").c_str(), F_OK) != 0; i++) R.for_ms(200);
    CHECK(access((waitdir + "/Waiting Test.json").c_str(), F_OK) == 0, "the waiting preset was added to its bank (%s)", waitdir.c_str());
    CHECK(slurp(srcdir + "/waiting.json").find("Waiting Test") == std::string::npos, "and taken off the waiting list");
    printf("  render while converting: %d blocks, longest %lld us, %d over a block's 2902 us\n", R.blocks, R.worst, R.late);
    CHECK(R.late == 0, "no render block ran late while the helper worked");

    /* 4: a stale preset chosen from the page. Every test preset is made stale and the store scanned again, which queues them all in the background in name
     * order; the LAST one is then chosen, so it has to go ahead of the others (an urgent request) for the header to show it upgrading. */
    int bi = -1;
    {   /* the bank's index from the page's preset list ({"b":[{"n":"<bank>","p":[...]},...]}), without loading any other bank's sounds */
        static char dig[1 << 18];
        api->get_param(inst, "preset_list", dig, sizeof dig);
        const std::string d(dig), key = "{\"n\":\"";
        const size_t at = d.find(key + bank + "\"");
        if (at != std::string::npos) { bi = 0; for (size_t p = d.find(key); p < at; p = d.find(key, p + 1)) bi++; }
    }
    if (bi >= 0) api->set_param(inst, "bank", std::to_string(bi).c_str());
    api->get_param(inst, "bank_name", b, sizeof b);
    CHECK(bi >= 0 && bank == b, "test bank found in the page's list (bank %d, \"%s\")", bi, b);
    R.for_ms(300);
    for (const auto &f : files) restamp(f, "test-stale");
    api->set_param(inst, "rescan", "1");
    for (int i = 0; i < 2000; i++) { R.block(); api->get_param(inst, "is_loading", b, sizeof b); if (b[0] != '1') break; }
    const std::string last = files.back();
    const long long t1 = now_us();
    api->set_param(inst, "preset", std::to_string(files.size() - 1).c_str());
    bool saw = false; long long t_up = 0;
    std::string name;
    while (now_us() - t1 < 60000000LL) {
        R.block();
        api->get_param(inst, "preset_name", b, sizeof b);
        if (!strncmp(b, "Upgrade v", 9)) saw = true;
        else if (saw) { t_up = now_us() - t1; name = b; break; }
    }
    CHECK(saw, "the header read \"Upgrade v%s\" while it converted", ver.c_str());
    CHECK(t_up > 0, "then the preset's name again (\"%s\") after %.2f s", name.c_str(), t_up / 1e6);
    CHECK(field(slurp(last), "conv") == hash, "the chosen preset's file was upgraded");
    printf("  (%d of %d test presets upgraded when the chosen one landed: it went first)\n", count_current(files, hash), (int) files.size());

    /* 5: going away mid-conversion */
    for (int i = 0; i < 600 && count_current(files, hash) < (int) files.size(); i++) R.for_ms(100);
    for (const auto &f : files) restamp(f, "test-stale");
    api->set_param(inst, "rescan", "1");
    R.for_ms(400);
    const long long t2 = now_us();
    api->destroy_instance(inst);
    const long long td = now_us() - t2;
    CHECK(td < 3000000, "destroy returned in %.2f s with a conversion under way", td / 1e6);
    int left = -1;
    for (int i = 0; i < 100; i++) { left = helper_pid(); if (left < 0) break; usleep(50000); }
    CHECK(left < 0, "no helper left behind");
    printf("%s: %d failed\n", g_fail ? "FAILED" : "PASSED", g_fail);
    return g_fail ? 1 : 0;
}
