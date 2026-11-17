/* Tablor loader test — dlopens dsp.so exactly like Schwung's chain host does,
 * then verifies the phase 0 contract:
 *   - init + create_instance succeed
 *   - chain_params serves valid-looking JSON with every key
 *   - set_param/get_param round-trip (int, enum-by-name, enum-by-index)
 *   - state round-trips, and a stale/garbage blob is IGNORED (not half-applied)
 *   - render_block fills silence and doesn't crash across many blocks
 *
 * Params apply-testing matters: an ER-99 stress test once passed while every
 * control was dead. This test asserts VALUES, not just survival.
 *
 * Run on the Move:  ./tablor_loadtest ./dsp.so
 */

#include <cstdio>
#include <string>
#include <cstring>
#include <cmath>
#include <cstdlib>
#include <cstdint>
#include <dlfcn.h>
#include <ctime>
#include <sys/stat.h>
#include <initializer_list>
#include <vector>
#include "../src/dsp/params.h"      /* tb_device_keys */
#include <dirent.h>
#include <unistd.h>
#include <sched.h>

#include "../src/host/plugin_api_v1.h"

static int g_fail = 0;
#define CHECK(cond, ...) do { \
    if (!(cond)) { g_fail++; printf("FAIL: " __VA_ARGS__); printf("\n"); } \
    else         {           printf("  ok: " __VA_ARGS__); printf("\n"); } \
} while (0)

/* The stock Tablor factory sounds (Glass Bells, Neu Bass...) are not shipped in TinyHouse, so the checks that
 * recall one are skipped when they are not in the store, and run as before where they are. */
static bool haveStockSounds()
{
    return access("/data/UserData/schwung/presets/tinyhouse/Glass Bells.json", F_OK) == 0;
}
#define CHECK_STOCK(cond, ...) do { \
    if (!haveStockSounds()) { printf("  skip (no stock sounds in TinyHouse): " __VA_ARGS__); printf("\n"); } \
    else CHECK(cond, __VA_ARGS__); \
} while (0)

static void host_log(const char *msg) { printf("  [host log] %s\n", msg); }

/* Preset writes are queued to the worker (file I/O may not happen on the
 * SPI callback), so a test that reads straight back has to let it land. */
static void settle(plugin_api_v2_t *api, void *inst)
{
    char b[8] = {};
    struct timespec ts = { 0, 5 * 1000000 };
    for (int i = 0; i < 400; i++) {
        api->get_param(inst, "busy", b, sizeof b);
        if (b[0] == '0') return;
        nanosleep(&ts, nullptr);
    }
}

int main(int argc, char **argv)
{
    setenv("TB_PRESET_SETTLE_MS", "0", 0);        /* a preset set here is applied at once, as these tools expect */
    const char *so_path = (argc > 1) ? argv[1] : "./dsp.so";

    void *dl = dlopen(so_path, RTLD_NOW | RTLD_LOCAL);
    if (!dl) { printf("FAIL: dlopen: %s\n", dlerror()); return 1; }
    printf("  ok: dlopen %s\n", so_path);

    auto init = (move_plugin_init_v2_fn) dlsym(dl, MOVE_PLUGIN_INIT_V2_SYMBOL);
    CHECK(init != nullptr, "dlsym " MOVE_PLUGIN_INIT_V2_SYMBOL);
    if (!init) return 1;

    host_api_v1_t host = {};
    host.api_version = 1;
    host.sample_rate = MOVE_SAMPLE_RATE;
    host.frames_per_block = MOVE_FRAMES_PER_BLOCK;
    host.log = host_log;

    plugin_api_v2_t *api = init(&host);
    CHECK(api && api->api_version == 2, "init returns v2 api");
    if (!api) return 1;

    /* Create the instance from a SCHED_FIFO thread, the way Schwung does.
     * Without this the checks below would pass even with inheritance left on,
     * because the test's own thread is SCHED_OTHER and there would be nothing
     * hot to inherit. Needs RLIMIT_RTPRIO or root; if it is refused the
     * scheduling checks still run but prove less, and say so. */
    int rt_ok = 0;
    {
        struct sched_param rp = { };
        rp.sched_priority = 90;
        rt_ok = (sched_setscheduler(0, SCHED_FIFO, &rp) == 0);
    }

    void *inst = api->create_instance(".", nullptr);
    CHECK(inst != nullptr, "create_instance");
    if (!inst) return 1;
    /* A host that sends no sound at load gets the first preset once the list is scanned (the first-load fix). These checks are about the
     * engine's defaults, so act like a host that restored a state: an empty one, which sets nothing and says a sound has been chosen. */
    api->set_param(inst, "state", "TBLR2;");

    /* create_instance runs on the SPI callback, so it must NOT scan, read
     * files or build tables inline — it hands all that to the worker. Wait
     * for the worker instead of assuming it already happened. */
    {
        char rb[8] = {};
        struct timespec ts10 = { 0, 10 * 1000000 };
        int waited = 0;
        for (; waited < 1500; waited++) {
            api->get_param(inst, "ready", rb, sizeof rb);
            if (rb[0] == '1') break;
            nanosleep(&ts10, nullptr);
        }
        CHECK(rb[0] == '1', "worker finished init off the audio thread (%d ms)",
              waited * 10);
    }

    /* The host asks for a preset as the module loads and then restores the slot's own state, both before the worker has scanned the
     * presets. The restored state has to win, or a reboot puts the first sound back on every slot. */
    {
        void *inst3 = api->create_instance(".", nullptr);
        CHECK(inst3 != nullptr, "an instance boots for the restore-order test");
        if (inst3) {
            api->set_param(inst3, "preset", "0");
            api->set_param(inst3, "state", "TBLR2;vca_r=77;");
            char rb[8] = {}, v[32] = {};
            struct timespec ts = { 0, 10 * 1000000 };
            for (int i = 0; i < 1500; i++) {
                api->get_param(inst3, "ready", rb, sizeof rb);
                if (rb[0] == '1') break;
                nanosleep(&ts, nullptr);
            }
            api->get_param(inst3, "vca_r", v, sizeof v);
            CHECK(!strcmp(v, "77"), "a restored state is not overwritten by the preset asked for at load (vca_r=\"%s\")", v);
            api->destroy_instance(inst3);
        }
    }

    static char big[64 * 1024];
    static char saved[64 * 1024];   /* a state blob held across a round-trip */

    /* Every key chain_params declares must read back: the Schwung manager's initial fetch sends values in batches of 8 and, when the
     * LAST declared key fails to read, skips the final flush, so the tail of the list never reaches the remote UI (the preset browser
     * vanished that way). */
    {
        api->get_param(inst, "chain_params", big, sizeof big);
        std::string cp(big);
        int n = 0, bad = 0;
        std::string badkeys;
        std::vector<std::string> declared;
        for (size_t p = 0; (p = cp.find("\"key\":\"", p)) != std::string::npos;) {
            p += 7;
            std::string k = cp.substr(p, cp.find('"', p) - p);
            declared.push_back(k);
            static char vb[64 * 1024];
            n++;
            if (api->get_param(inst, k.c_str(), vb, sizeof vb) < 0) { bad++; badkeys += " " + k; }
        }
        { std::string tail; size_t q = cp.size(); for (int i = 0; i < 5; i++) { q = cp.rfind("\"key\":\"", q - 1); tail = cp.substr(q + 7, cp.find('"', q + 7) - q - 7) + " " + tail; } printf("  [last declared keys: %s]%c", tail.c_str(), 10); }
        CHECK(n > 300 && bad == 0, "every declared key reads back (%d keys, %d unreadable:%s)", n, bad, badkeys.c_str());

        /* The host (Schwung's chain host) caches only the first 256 declared keys. A set of a key past that is a cache miss, and a miss
         * re-fetches and re-parses this whole list on the SPI callback: a 23 ms dropout, four times a second while a knob is turned (the macro
         * knobs sat at 281..288). Every key a hand can turn on the Move must be inside it. */
        std::string late;
        for (const char *dk : tb_device_keys) {
            size_t at = 0;
            while (at < declared.size() && declared[at] != dk) at++;
            if (at >= 256) late += std::string(" ") + dk + (at >= declared.size() ? "(missing)" : "");
        }
        CHECK(late.empty(), "every key the Move turns is inside the host's 256-key cache (%d declared; outside:%s)", n, late.c_str());
        /* the keys the Move's widgets READ on every redraw (extra_keys) belong inside it too: a read of a key past it re-parses the whole list */
        for (const char *rk : { "macro_names", "macro_routes", "smp_shape", "smp2_shape", "smp3_shape", "wt_on", "smp_gv", "smp2_gv", "smp3_gv" }) {
            size_t at = 0;
            while (at < declared.size() && declared[at] != rk) at++;
            CHECK(at < 256, "the widget key %s is inside the 256-key cache (position %d)", rk, (int) at);
        }
    }

    /* The macro knobs carry the preset's macro names as their unit: the cell label stays "Macro 1" and touching the knob shows the value
     * with the name beside it. A preset's names republish chain_params on the worker; a macro it does not use has no unit. */
    {
        auto unitOf = [&](int m) -> std::string {
            api->get_param(inst, "chain_params", big, sizeof big);
            std::string cp(big), key = "\"key\":\"mac" + std::to_string(m) + "\"";
            size_t k = cp.find(key);
            if (k == std::string::npos) return "<no entry>";
            size_t e = cp.find('}', k);                       /* the entry ends at its first closing brace (its fields are flat) */
            size_t u = cp.find("\"unit\":\"", k);
            if (u == std::string::npos || (e != std::string::npos && u > e)) return "";
            u += 8;
            return cp.substr(u, cp.find('"', u) - u);
        };
        api->set_param(inst, "state", "TBLR2;mac=1:flt_freq:20.0,3:dly_mix:30.0;macn=More Delay||Cutoff|||||;");
        settle(api, inst);
        char lb[8] = {};
        for (int i = 0; i < 400; i++) {                         /* is_loading holds while the republish is owed */
            api->get_param(inst, "is_loading", lb, sizeof lb);
            if (lb[0] == '0') break;
            struct timespec ts = { 0, 5 * 1000000 };
            nanosleep(&ts, nullptr);
        }
        CHECK(lb[0] == '0', "is_loading falls once the macro names are published");
        /* The names are NOT written into chain_params as units: that made the contract differ per preset (332 of 384 Factory presets) and the host
         * re-planned every page after each landing. They are on macro_names. */
        CHECK(unitOf(1) == "" && unitOf(2) == "" && unitOf(3) == "", "chain_params carries no macro units (\"%s\"/\"%s\"/\"%s\")", unitOf(1).c_str(), unitOf(2).c_str(), unitOf(3).c_str());
        { char mn[256] = {}; api->get_param(inst, "macro_names", mn, sizeof mn); CHECK(!strcmp(mn, "More Delay||Cutoff|||||"), "macro_names carries the names (\"%s\")", mn); }
        api->set_param(inst, "state", "TBLR2;");
        for (int i = 0; i < 400; i++) {
            api->get_param(inst, "is_loading", lb, sizeof lb);
            if (lb[0] == '0') break;
            struct timespec ts = { 0, 5 * 1000000 };
            nanosleep(&ts, nullptr);
        }
        CHECK(unitOf(1) == "" && unitOf(3) == "", "a preset without macros clears the units (\"%s\"/\"%s\")", unitOf(1).c_str(), unitOf(3).c_str());

        /* The remote UI's macro editing: a knob added to a macro (right-click), its travel changed, removed. The routes and names read back; the name is the macro's own if it has none. */
        auto settleMacros = [&]() { settle(api, inst); for (int i = 0; i < 400; i++) { char l[8] = {}; api->get_param(inst, "is_loading", l, sizeof l); if (l[0] == '0') break; struct timespec ts = { 0, 5 * 1000000 }; nanosleep(&ts, nullptr); } };
        auto read = [&](const char *key) { char b[3072] = {}; api->get_param(inst, key, b, sizeof b); return std::string(b); };
        api->set_param(inst, "macro_edit", "add|2|wt1_level|40|Level");
        api->set_param(inst, "macro_edit", "add|2|flt_freq|-30|Cutoff");
        api->set_param(inst, "macro_edit", "add|5|no_such_key|10|X");
        api->set_param(inst, "macro_edit", "add|9|wt1_level|10|X");
        settleMacros();
        CHECK(read("macro_routes") == "2:wt1_level:40,2:flt_freq:-30", "macro_edit add: routes read back (\"%s\")", read("macro_routes").c_str());
        {   /* and the macro must MOVE what it holds: macro 2 up by 127 takes Level 40 up, Cutoff 30 down */
            api->set_param(inst, "mac2", "0");                        /* the first control put the knob where it sits; start from the bottom */
            const float l0 = (float) atof(read("wt1_level").c_str()), c0 = (float) atof(read("flt_freq").c_str());
            api->set_param(inst, "mac2", "127");
            const float l1 = (float) atof(read("wt1_level").c_str()), c1 = (float) atof(read("flt_freq").c_str());
            CHECK(std::fabs((l1 - l0) - 40.0f) < 1.5f || l1 >= 127.0f, "turning macro 2 moves Level (%g -> %g)", (double) l0, (double) l1);
            CHECK(std::fabs((c1 - c0) + 30.0f) < 1.5f || c1 <= 0.0f, "... and Cutoff (%g -> %g)", (double) c0, (double) c1);
            api->set_param(inst, "mac2", "0");
        }
        {   /* the remote UI's spelling ("synth:" in front of every key), a travel that is not a number, and a macro that was already part-way up when the knob was added */
            api->set_param(inst, "synth:macro_edit", "add|4|wt2_level|NaN|Bad");
            api->set_param(inst, "synth:mac4", "50");
            api->set_param(inst, "synth:macro_edit", "add|4|wt3_level|30|Ok");
            settleMacros();
            const float m0 = (float) atof(read("mac4").c_str());               /* the first control moved the knob to where it sits (travel 30: 127 x level / 30, held to 0..127) */
            api->set_param(inst, "synth:mac4", "0");
            const float a0 = (float) atof(read("wt3_level").c_str()), n0 = (float) atof(read("wt2_level").c_str());
            api->set_param(inst, "synth:mac4", "100");
            const float a1 = (float) atof(read("wt3_level").c_str()), n1 = (float) atof(read("wt2_level").c_str());
            CHECK(std::fabs((a1 - a0) - 30.0f * 100.0f / 127.0f) < 1.5f || a1 >= 127.0f, "prefixed keys: the macro from 0 to 100 moves Level by 30 x 100/127 (%g -> %g; the knob had been put at %g)", (double) a0, (double) a1, (double) m0);
            CHECK(!std::isnan(n1) && std::fabs(n1 - n0) < 0.01f, "a NaN travel moves nothing and breaks nothing (%g -> %g, routes \"%s\")", (double) n0, (double) n1, read("macro_routes").c_str());
            api->set_param(inst, "synth:macro_edit", "clear|4");
            settleMacros();
        }
        CHECK(read("macro_names") == "|Level||||||", "the first knob names the macro (\"%s\")", read("macro_names").c_str());
        CHECK(unitOf(2) == "", "chain_params still carries no unit for macro 2 (\"%s\")", unitOf(2).c_str());
        api->set_param(inst, "macro_edit", "travel|2|wt1_level|-17.5");
        api->set_param(inst, "macro_edit", "name|2|Brightness");
        settleMacros();
        CHECK(read("macro_routes") == "2:wt1_level:-17.5,2:flt_freq:-30" && read("macro_names") == "|Brightness||||||", "travel and name edited (\"%s\" / \"%s\")", read("macro_routes").c_str(), read("macro_names").c_str());
        { char st[8192] = {}; api->get_param(inst, "state", st, sizeof st); CHECK(strstr(st, "mac=2:wt1_level:-17.5,2:flt_freq:-30;") && strstr(st, "macn=|Brightness||||||;"), "the slot's state carries the edited macros"); }
        api->set_param(inst, "macro_edit", "del|2|wt1_level");
        settleMacros();
        CHECK(read("macro_routes") == "2:flt_freq:-30" && read("macro_names") == "|Brightness||||||", "a route removed (\"%s\")", read("macro_routes").c_str());
        api->set_param(inst, "macro_edit", "del|2|flt_freq");
        settleMacros();
        CHECK(read("macro_routes").empty() && read("macro_names").empty() && unitOf(2) == "", "the last route removed: the macro has no name and no unit (\"%s\"/\"%s\")", read("macro_routes").c_str(), read("macro_names").c_str());

        /* Where the macro's knob sits against the controls it holds. A macro moves a control by the macro's CHANGE (travel x change / 127, held to the control's range), so a knob and
         * a control agree only if the knob starts where the control is: the first control put on a macro sets the knob to its position; later ones keep their own offset. */
        auto num = [&](const char *key) { return (float) atof(read(key).c_str()); };
        auto near = [](float a, float b, float tol = 1.01f) { return std::fabs(a - b) <= tol; };
        {   /* one control, full range up: the knob equals the control, now and after turning the knob */
            api->set_param(inst, "wt2_level", "64");
            api->set_param(inst, "macro_edit", "add|6|wt2_level|127|Lvl");
            settleMacros();
            CHECK(near(num("mac6"), 64.0f), "one control, full range up: the knob is put at the control's position (knob %g, control %g)", (double) num("mac6"), (double) num("wt2_level"));
            api->set_param(inst, "mac6", "100");
            CHECK(near(num("wt2_level"), 100.0f), "... and turned to 100 the control reads 100 (%g)", (double) num("wt2_level"));
            api->set_param(inst, "mac6", "0");
            CHECK(near(num("wt2_level"), 0.0f), "... and to 0 it reads 0 (%g)", (double) num("wt2_level"));
            CHECK(read("live_vals").find("wt2_level=0;") != std::string::npos || read("live_vals").find("wt2_level=0") != std::string::npos, "live_vals names what the host wrote lately, with its value now (\"%s\")", read("live_vals").c_str());
            api->set_param(inst, "macro_edit", "clear|6"); settleMacros();
        }
        {   /* a control that runs the other way (negative travel): the knob is at the control's distance from its top, so control = 127 - knob */
            api->set_param(inst, "wt3_level", "30");
            api->set_param(inst, "macro_edit", "add|7|wt3_level|-127|Neg");
            settleMacros();
            CHECK(near(num("mac7"), 97.0f), "negative full travel: the knob reads 127 - control (knob %g, control %g)", (double) num("mac7"), (double) num("wt3_level"));
            api->set_param(inst, "mac7", "20");
            CHECK(near(num("wt3_level"), 107.0f), "... and at 20 the control reads 107 (%g)", (double) num("wt3_level"));
            api->set_param(inst, "macro_edit", "clear|7"); settleMacros();
        }
        {   /* two controls on one knob: the first sets the knob, the second keeps its own offset and moves in step; they meet again when the knob reaches an end */
            api->set_param(inst, "wt1_pos", "40"); api->set_param(inst, "wt2_pos", "100");
            api->set_param(inst, "macro_edit", "add|3|wt1_pos|127|A");
            api->set_param(inst, "macro_edit", "add|3|wt2_pos|127|A");
            settleMacros();
            CHECK(near(num("mac3"), 40.0f), "two controls: the knob took the FIRST one's position (knob %g, first %g, second %g)", (double) num("mac3"), (double) num("wt1_pos"), (double) num("wt2_pos"));
            CHECK(near(num("wt1_pos"), 40.0f) && near(num("wt2_pos"), 100.0f), "... and adding them moved neither control (%g, %g)", (double) num("wt1_pos"), (double) num("wt2_pos"));
            api->set_param(inst, "mac3", "60");
            CHECK(near(num("wt1_pos"), 60.0f) && near(num("wt2_pos"), 120.0f), "knob +20: both controls move by 20 (%g, %g)", (double) num("wt1_pos"), (double) num("wt2_pos"));
            api->set_param(inst, "mac3", "127");
            CHECK(near(num("wt1_pos"), 127.0f) && near(num("wt2_pos"), 127.0f), "knob at the top: both at their top (%g, %g)", (double) num("wt1_pos"), (double) num("wt2_pos"));
            api->set_param(inst, "mac3", "0");
            CHECK(near(num("wt1_pos"), 0.0f) && near(num("wt2_pos"), 0.0f), "knob at the bottom: both at their bottom, in step again (%g, %g)", (double) num("wt1_pos"), (double) num("wt2_pos"));
            api->set_param(inst, "wt1_pos", "90");                                   /* the first control is moved by hand: the knob no longer matches it */
            api->set_param(inst, "macro_edit", "align|3"); settleMacros();
            CHECK(near(num("mac3"), 90.0f) && near(num("wt1_pos"), 90.0f) && near(num("wt2_pos"), 0.0f), "Match knob puts the knob on the first control and moves nothing (knob %g, controls %g, %g)", (double) num("mac3"), (double) num("wt1_pos"), (double) num("wt2_pos"));
            api->set_param(inst, "macro_edit", "clear|3"); settleMacros();
        }
        {   /* partial travel cannot be matched exactly: the knob goes as near as it can (held to 127), and nothing breaks */
            api->set_param(inst, "wt1_level", "100");
            api->set_param(inst, "macro_edit", "add|8|wt1_level|50|Part");
            settleMacros();
            CHECK(near(num("mac8"), 127.0f) && near(num("wt1_level"), 100.0f), "travel 50 on a control at 100: the knob is held to 127 and the control stays (knob %g, control %g)", (double) num("mac8"), (double) num("wt1_level"));
            api->set_param(inst, "macro_edit", "clear|8"); settleMacros();
        }
    }

    /* The slot remembers its demo: pack and part by name, restored from the state once the demo store has been scanned. Two parts of a throwaway pack
     * are written to the demo store, the slot is restored onto the second, and the state it then writes must name it again. */
    {
        const char *root = "/data/UserData/schwung/midi/tinyhouse", *dir = "/data/UserData/schwung/midi/tinyhouse/ZZ LoadTest";
        mkdir("/data/UserData/schwung/midi", 0755); mkdir(root, 0755); mkdir(dir, 0755);
        for (const char *f : { "/data/UserData/schwung/midi/tinyhouse/ZZ LoadTest/A Part.tdm", "/data/UserData/schwung/midi/tinyhouse/ZZ LoadTest/B Part.tdm" })
            if (FILE *fp = fopen(f, "w")) { fputs("tdm1\n", fp); fclose(fp); }
        api->set_param(inst, "rescan", "1");
        settle(api, inst);
        struct timespec ts = { 0, 5 * 1000000 };
        for (int i = 0; i < 400; i++) { char lb[8] = {}; api->get_param(inst, "is_loading", lb, sizeof lb); if (lb[0] == '0') break; nanosleep(&ts, nullptr); }
        api->set_param(inst, "state", "TBLR2;dmpk=ZZ LoadTest;dmpt=B Part;dmon=0;");
        settle(api, inst);
        for (int i = 0; i < 400; i++) { char lb[8] = {}; api->get_param(inst, "is_loading", lb, sizeof lb); if (lb[0] == '0') break; nanosleep(&ts, nullptr); }
        char dn[64] = {};
        api->get_param(inst, "demo_part", dn, sizeof dn);
        CHECK(!strcmp(dn, "1"), "a restored state puts the demo back on its part (demo_part=\"%s\")", dn);
        api->get_param(inst, "state", big, sizeof big);
        CHECK(strstr(big, "dmpk=ZZ LoadTest;dmpt=B Part;dmon=0;") != nullptr, "and the state it writes names the pack and the part again");
        api->set_param(inst, "state", "TBLR2;");                /* a restore without a demo leaves the slot's demo where it was */
        settle(api, inst);
        api->get_param(inst, "demo_part", dn, sizeof dn);
        CHECK(!strcmp(dn, "1"), "a state without a demo does not reset it (demo_part=\"%s\")", dn);
        unlink("/data/UserData/schwung/midi/tinyhouse/ZZ LoadTest/A Part.tdm");
        unlink("/data/UserData/schwung/midi/tinyhouse/ZZ LoadTest/B Part.tdm");
        rmdir(dir);
        api->set_param(inst, "rescan", "1");
        settle(api, inst);
    }

    /* ---- the worker must not be born on the audio thread's scheduling ----
     * Every entry point runs on the SPI callback at SCHED_FIFO 90, and POSIX
     * defaults to PTHREAD_INHERIT_SCHED, so a thread spawned from one takes
     * that priority AND that name -- invisible in an audit, and starving
     * Move's own Link publisher (FIFO 35) for the length of a table build.
     * Raised by Schwung upstream against this module. Asserted here against
     * /proc so the fix cannot quietly regress. */
    {
        int found = 0, policy = -1, rtprio = -1;
        DIR *d = opendir("/proc/self/task");
        struct dirent *e;
        while (d && (e = readdir(d))) {
            if (e->d_name[0] == '.') continue;
            char path[256], nm[64] = {};
            snprintf(path, sizeof path, "/proc/self/task/%s/comm", e->d_name);
            FILE *f = fopen(path, "r");
            if (!f) continue;
            if (fgets(nm, sizeof nm, f)) nm[strcspn(nm, "\n")] = 0;
            fclose(f);
            if (strcmp(nm, "tablor-wtload") != 0) continue;
            found = 1;
            snprintf(path, sizeof path, "/proc/self/task/%s/stat", e->d_name);
            f = fopen(path, "r");
            if (f) {
                static char st[2048];
                if (fgets(st, sizeof st, f)) {
                    /* comm is parenthesised and may contain spaces: fields
                     * resume after the LAST ')'. rt_priority is 40, policy 41,
                     * and field 3 is the first token after it. */
                    char *p2 = strrchr(st, ')');
                    if (p2) {
                        int idx = 3;
                        for (char *tok = strtok(p2 + 2, " "); tok;
                             tok = strtok(nullptr, " "), idx++) {
                            if (idx == 40) rtprio = atoi(tok);
                            if (idx == 41) { policy = atoi(tok); break; }
                        }
                    }
                }
                fclose(f);
            }
            break;
        }
        if (d) closedir(d);
        CHECK(found, "the loader thread is named (tablor-wtload), not left "
              "wearing the audio thread's name");
        CHECK(policy == SCHED_OTHER, "loader thread policy is SCHED_OTHER "
              "(got %d, SCHED_FIFO is %d)", policy, SCHED_FIFO);
        CHECK(rtprio == 0, "loader thread has no realtime priority (got %d)",
              rtprio);
    }

    if (rt_ok) {
        struct sched_param np = { };
        np.sched_priority = 0;
        sched_setscheduler(0, SCHED_OTHER, &np);   /* the rest is not RT */
    }

    /* ---- chain_params ---- */
    int n = api->get_param(inst, "chain_params", big, sizeof big);
    CHECK(n > 100, "chain_params returns %d bytes", n);
    CHECK(big[0] == '[' && big[n - 1] == ']', "chain_params looks like a JSON array");
    CHECK(strstr(big, "\"wt1_table\"") && strstr(big, "\"volume\"") &&
          strstr(big, "\"flt_freq\""), "chain_params contains first/mid/last keys");
    /* The two LFOs and the four mod slots were cut in 1.1.0: four pages of
     * clutter for modulation Schwung already provides through a slot LFO.
     * Asserted here because a stale generator would put them back silently. */
    CHECK(!strstr(big, "\"lfo1_shape\"") && !strstr(big, "\"lfo2_rate\"") &&
          !strstr(big, "\"m1_src\"") && !strstr(big, "\"m4_on\"") &&
          !strstr(big, "\"u1\"") && !strstr(big, "\"u8_target\"") &&
          !strstr(big, "\"rt_wt1\"") && !strstr(big, "\"wt1_fine\""),
          "the LFOs, the mod slots, the macros and the older trims are really gone");
    CHECK(strstr(big, "%s") == nullptr, "no unfilled %%s slots leaked");

    /* ---- int param round-trip ---- */
    char buf[256];
    api->set_param(inst, "flt_freq", "93");
    n = api->get_param(inst, "flt_freq", buf, sizeof buf);
    CHECK(n > 0 && !strcmp(buf, "93"), "flt_freq set 93 -> get \"%s\"", buf);

    api->set_param(inst, "flt_freq", "999");            /* clamps to 127 */
    api->get_param(inst, "flt_freq", buf, sizeof buf);
    CHECK(!strcmp(buf, "127"), "flt_freq clamps 999 -> \"%s\"", buf);

    /* ---- enum by name and by index ---- */
    api->set_param(inst, "flt_type", "HP 24");
    api->get_param(inst, "flt_type", buf, sizeof buf);
    CHECK(!strcmp(buf, "HP 24"), "flt_type by name -> \"%s\"", buf);

    api->set_param(inst, "flt_type", "4");              /* index of BP 12 */
    api->get_param(inst, "flt_type", buf, sizeof buf);
    CHECK(!strcmp(buf, "BP 12"), "flt_type by index 4 -> \"%s\"", buf);

    api->set_param(inst, "flt_type", "NoSuchType");     /* must be ignored */
    api->get_param(inst, "flt_type", buf, sizeof buf);
    CHECK(!strcmp(buf, "BP 12"), "unknown enum name ignored -> \"%s\"", buf);

    /* ---- wavetable enum: default is the built-in Init, index 0 ---- */
    n = api->get_param(inst, "wt1_table", buf, sizeof buf);
    CHECK(n == 0 && buf[0] == 0, "wt1_table default empty (built-in Init)");

    /* ---- state round-trip ---- */
    api->set_param(inst, "vca_r", "77");
    n = api->get_param(inst, "state", big, sizeof big);
    CHECK(n > 6 && (!strncmp(big, "TBLR2;", 6) || !strncmp(big, "{\"tblr\":\"TBLR2;", 15)), "state is version-tagged (%d bytes)", n);
    /* The state is a JSON object for a reader whose buffer holds it: the blob plus every parameter's value, so the remote UI's manager reads a slot in one call
     * (it read 434 keys one at a time on every preset change, which stalled the Move's own screen). A small buffer gets the plain blob. */
    CHECK(big[0] == '{' && big[n - 1] == '}' && strstr(big, ",\"vca_r\":77,") != nullptr && strstr(big, "\"flt_type\":\"") != nullptr && strstr(big, "\"macro_names\":\"") != nullptr && strstr(big, "\"wt1_select\":\"") != nullptr,
          "the state is a JSON object with every value (numbers bare, names quoted)");
    {
        char small[4096] = {};
        const int sn = api->get_param(inst, "state", small, sizeof small);
        CHECK(sn > 6 && !strncmp(small, "TBLR2;", 6), "a small buffer gets the plain blob (%d bytes)", sn);
        api->set_param(inst, "vca_r", "12");
        api->set_param(inst, "state", big);                      /* the JSON form restores the sound too */
        api->get_param(inst, "vca_r", buf, sizeof buf);
        CHECK(!strcmp(buf, "77"), "restoring the JSON state sets the values (vca_r=\"%s\")", buf);
        api->set_param(inst, "vca_r", "77");
    }

    /* prefixed spelling */
    api->get_param(inst, "vca_r", buf, sizeof buf);
    CHECK(!strcmp(buf, "77"), "state restore via synth:state -> vca_r=\"%s\"", buf);

    api->set_param(inst, "vca_r", "10");
    api->set_param(inst, "state", "GARBAGE;vca_r=99;"); /* stale blob: ignore */
    api->get_param(inst, "vca_r", buf, sizeof buf);
    CHECK(!strcmp(buf, "10"), "untagged blob fully ignored -> vca_r=\"%s\"", buf);

    /* ---- render: silence before any note ---- */
    int16_t out[MOVE_FRAMES_PER_BLOCK * 2];
    memset(out, 0x55, sizeof out);
    long acc = 0;
    for (int b = 0; b < 200; b++) {
        api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
        for (size_t i = 0; i < MOVE_FRAMES_PER_BLOCK * 2; i++)
            acc += out[i] < 0 ? -out[i] : out[i];
    }
    CHECK(acc == 0, "silence before any note (acc=%ld)", acc);

    /* ---- note on -> SOUND (the engine gate), note off -> decay to zero */
    const uint8_t note_on[]  = { 0x90, 60, 100 };
    const uint8_t note_off[] = { 0x80, 60, 0 };
    api->on_midi(inst, note_on, 3, 0);
    long peak = 0;
    for (int b = 0; b < 200; b++) {                     /* ~0.6 s held */
        api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
        for (size_t i = 0; i < MOVE_FRAMES_PER_BLOCK * 2; i++) {
            long v = out[i] < 0 ? -out[i] : out[i];
            if (v > peak) peak = v;
        }
    }
    CHECK(peak > 2000, "note 60 makes sound (peak %ld of 32767)", peak);
    CHECK(peak < 32767, "not clipping at defaults (peak %ld)", peak);

    /* chord: 4 more notes, still sane */
    const uint8_t chordNotes[4] = { 48, 64, 67, 72 };
    for (int ci = 0; ci < 4; ci++) {
        const uint8_t on[] = { 0x90, chordNotes[ci], 100 };
        api->on_midi(inst, on, 3, 0);
    }
    long chordPeak = 0;
    for (int b = 0; b < 200; b++) {
        api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
        for (size_t i = 0; i < MOVE_FRAMES_PER_BLOCK * 2; i++) {
            long v = out[i] < 0 ? -out[i] : out[i];
            if (v > chordPeak) chordPeak = v;
        }
    }
    CHECK(chordPeak > peak, "chord is louder than one note (%ld > %ld)", chordPeak, peak);

    api->on_midi(inst, note_off, 3, 0);
    const uint8_t all_off[] = { 0xB0, 123, 0 };
    api->on_midi(inst, all_off, 3, 0);
    long tail = 0;
    for (int b = 0; b < 600; b++) {                     /* ~1.7 s of release */
        api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
        if (b > 500) {
            for (size_t i = 0; i < MOVE_FRAMES_PER_BLOCK * 2; i++)
                tail += out[i] < 0 ? -out[i] : out[i];
        }
    }
    CHECK(tail == 0, "all-notes-off decays to true silence (tail acc=%ld)", tail);

    /* filter audibly filters: closed lowpass must be quieter than open */
    api->set_param(inst, "flt_type", "LP 24");          /* enum tests left BP 12 */
    api->set_param(inst, "flt_freq", "127");
    api->on_midi(inst, note_on, 3, 0);
    long openPeak = 0;
    for (int b = 0; b < 120; b++) {
        api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
        for (size_t i = 0; i < MOVE_FRAMES_PER_BLOCK * 2; i++) {
            long v = out[i] < 0 ? -out[i] : out[i];
            if (v > openPeak) openPeak = v;
        }
    }
    api->set_param(inst, "flt_freq", "20");             /* slam it shut */
    for (int b = 0; b < 30; b++)                        /* let the SVF settle */
        api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
    long closedPeak = 0;
    for (int b = 0; b < 120; b++) {
        api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
        for (size_t i = 0; i < MOVE_FRAMES_PER_BLOCK * 2; i++) {
            long v = out[i] < 0 ? -out[i] : out[i];
            if (v > closedPeak) closedPeak = v;
        }
    }
    CHECK(closedPeak < openPeak / 3,
          "closing the filter quiets the note (%ld -> %ld)", openPeak, closedPeak);
    api->on_midi(inst, all_off, 3, 0);
    api->set_param(inst, "flt_freq", "127");

    /* ---- ui_hierarchy: the stock 0.12+ editor is the UI ---- */
    n = api->get_param(inst, "ui_hierarchy", big, sizeof big);
    CHECK(n > 1000 && strstr(big, "\"levels\"") && strstr(big, "\"root\""),
          "ui_hierarchy served (%d bytes)", n);
    /* The fullscreen browser is deliberately gone -- the Preset page's enum
     * cell is the loader. Assert the inverse so it cannot creep back. */
    /* The data channels the web panel reads must declare what they ARE:
     * read-only strings. They were an int with min==max==0, an invented way
     * to say "no knob turns this" from before `access` existed -- which
     * Schwung's own contract checker flags as an empty numeric range
     * (athousanddetails/schwung-tablor#2). They stay on no page on purpose. */
    n = api->get_param(inst, "chain_params", big, sizeof big);
    for (const char *k : { "wt1_shape", "wt2_shape", "data_feed" }) {
        char pat[64]; snprintf(pat, sizeof pat, "\"key\":\"%s\"", k);
        const char *at = strstr(big, pat);
        std::string ent = at ? std::string(at, strchr(at, '}') ? (size_t) (strchr(at, '}') - at) : strlen(at)) : std::string();   /* this entry only */
        CHECK(at && ent.find("\"type\":\"string\"") != std::string::npos && ent.find("\"access\":\"read\"") != std::string::npos &&
              ent.find("\"min\"") == std::string::npos,
              "%s is a read-only string, not an int with an empty range", k);
    }
    /* put the hierarchy back in `big` -- the checks below read it from there */
    n = api->get_param(inst, "ui_hierarchy", big, sizeof big);

    /* TinyHouse's root is the preset browser, the way TinyK's is: list_param / count_param / name_param,
     * the pages (Main, Tone, Effects, Mod FX) as levels under it, and no knobs of its own. */
    {
        const char *r = strstr(big, "\"root\"");
        const char *end = r ? strstr(r, "\"knobs\"") : NULL;
        CHECK(r && strstr(r, "\"list_param\":\"preset\"") && strstr(r, "\"count_param\":\"preset_count\"") &&
              strstr(r, "\"name_param\":\"preset_name\""),
              "the root is a preset browser (list_param, count_param, name_param)");
        int levels = 0, keys = 0;
        for (const char *c = r; c && end && c < end; c++) {
            if (!strncmp(c, "\"level\":", 8)) levels++;
            if (!strncmp(c, "\"key\":", 6)) keys++;
        }
        CHECK(levels == 10 && keys == 0 && end && !strncmp(end, "\"knobs\":[]", 10),
              "and holds the ten pages (Main, Tone, Macros, Effects, Mod FX, Voice, Grain 1-3, Demo) as levels, with no knobs of its own (%d levels, %d params)", levels, keys);
    }
    n = api->get_param(inst, "chain_params", big, sizeof big);
    CHECK(strstr(big, "\"viz\"") && strstr(big, "\"group\":\"amp\"") &&
          strstr(big, "\"kind\":\"fader\""),
          "chain_params declares viz groups");


    /* ---- filepath selection + mid-note switching ---- */
    n = api->get_param(inst, "chain_params", big, sizeof big);
    CHECK(n > 1000 && strstr(big, "\"filepath\"") &&
          strstr(big, "/data/UserData/UserLibrary/Wavetables"),
          "chain_params exposes the filepath browser (%d bytes)", n);
    /* The host reads this into a SHADOW_PARAM_VALUE_LEN (64 KB) buffer and
     * REFUSES to load the synth if it does not fit (chain_host.c:481). The
     * dynamic option lists live in here now, so watch the headroom. */
    CHECK(n < 60000, "chain_params fits the host's 64 KB buffer (%d bytes)", n);

    /* ---- turnable selection: pack list + per-osc enum ---- */
    CHECK(strstr(big, "\"wt_pack\"") && strstr(big, "\"wt1_select\"") &&
          strstr(big, "\"wt2_select\""),
          "chain_params publishes the turnable selection enums");
    CHECK(strstr(big, "\"wt1_shape\"") && strstr(big, "\"wt2_shape\"") && strstr(big, "\"key\":\"data_feed\"") &&
          strstr(big, "\"extra_keys\":[\"preset_list\",\"demo_list\",\"wt_paths\",\"live_vals\"]"),
          "chain_params declares the shape digests and the data feed that names the big lists as extra keys (the manager pushes those, and skips an extra key that is also declared)");
    CHECK(!strstr(big, "\"key\":\"preset_list\"") && !strstr(big, "\"key\":\"demo_list\"") && !strstr(big, "\"key\":\"wt_paths\""),
          "the big lists are NOT declared keys (a declared one is never pushed to the web panel)");
    n = api->get_param(inst, "wt_paths", big, sizeof big);
    CHECK(n > 10 && big[0] == '[' && strstr(big, "/data/UserData"),
          "wt_paths lists the file behind each wtN_select index (%d bytes)", n);

    n = api->get_param(inst, "wt_pack_list", big, sizeof big);
    CHECK(n > 10 && big[0] == '[' && strstr(big, "\"label\":\"All\""),
          "wt_pack_list serves the items rows (%d bytes)", n);

    /* is_loading must answer exactly "1" or "0": anything else and the host
     * marks it unsupported FOREVER for this component (isLoadingSays). */
    api->get_param(inst, "is_loading", buf, sizeof buf);
    CHECK((buf[0] == '0' || buf[0] == '1') && buf[1] == 0,
          "is_loading answers \"%s\" (must be exactly 1 or 0)", buf);

    /* The keys the Move's pictures read: which oscillators sound, and the look of a sample slot's sample. */
    api->get_param(inst, "wt_on", buf, sizeof buf);
    CHECK(strlen(buf) == 5 && (buf[0] == '0' || buf[0] == '1') && buf[1] == ',' && buf[3] == ',', "wt_on reads like \"1,0,1\" -> \"%s\"", buf);
    api->get_param(inst, "chain_params", big, sizeof big);
    CHECK(strstr(big, "\"key\":\"wt_on\"") && strstr(big, "\"key\":\"smp_shape\"") && strstr(big, "\"key\":\"smp3_shape\"") && strstr(big, "\"key\":\"smp_gdens\"") && strstr(big, "\"key\":\"smp_kind\""),
          "chain_params declares wt_on, the sample shapes and the grain keys");
    {
        char sp[512] = {};
        if (FILE *pf = popen("find /data/UserData/UserLibrary/Samples -name '*.tbs' 2>/dev/null | head -n 1", "r")) { if (fgets(sp, sizeof sp, pf)) sp[strcspn(sp, "\r\n")] = 0; pclose(pf); }
        if (sp[0]) {
            api->get_param(inst, "smp_shape", buf, sizeof buf);
            std::string st = std::string("TBLR2;smp_set=") + sp + ";smp_level=100;";
            api->set_param(inst, "state", st.c_str());
            settle(api, inst);
            for (int i = 0; i < 400; i++) { api->get_param(inst, "smp_shape", buf, sizeof buf); if (buf[0]) break; struct timespec ts = { 0, 5 * 1000000 }; nanosleep(&ts, nullptr); }
            CHECK(!strncmp(buf, "96,", 3) && strlen(buf) == 3 + 96, "smp_shape of a loaded sample is \"96,\" and 96 characters (%zu bytes) for %s", strlen(buf), sp);
            { std::string stj; { char sb[65536] = {}; api->get_param(inst, "state", sb, sizeof sb); stj = sb; }
              CHECK(stj.find(std::string("\"smp_shape\":\"") + buf + "\"") != std::string::npos, "the state JSON (what the remote UI's manager reads) carries the sample shape"); }
            api->set_param(inst, "state", "TBLR2;smp_set=;");
            settle(api, inst);
            for (int i = 0; i < 400; i++) { api->get_param(inst, "smp_shape", buf, sizeof buf); if (!buf[0]) break; struct timespec ts = { 0, 5 * 1000000 }; nanosleep(&ts, nullptr); }
            CHECK(buf[0] == 0, "smp_shape is empty again once the slot has no sample");
        } else printf("  (no .tbs sample on this machine: the smp_shape check is skipped)\n");
    }

    /* a pot turn on wt1_select must actually change the loaded table */
    api->set_param(inst, "wt1_select", "0");
    api->get_param(inst, "wt1_select", buf, sizeof buf);
    CHECK(!strcmp(buf, "0"), "wt1_select index 0 = Init -> \"%s\"", buf);
    api->set_param(inst, "wt1_select", "3");
    settle(api, inst);
    api->get_param(inst, "wt1_select", buf, sizeof buf);
    CHECK(!strcmp(buf, "3"), "wt1_select round-trips an index -> \"%s\"", buf);
    api->get_param(inst, "wt1_table", buf, sizeof buf);
    CHECK(buf[0] == '/', "turning wt1_select loaded a real file -> \"%s\"", buf);

    /* choosing a pack republishes wt1_select's options — off the audio
     * thread, so the test has to wait for the worker like the host waits
     * for is_loading */
    int allOpts = 0;
    api->get_param(inst, "chain_params", big, sizeof big);
    for (const char *c = strstr(big, "\"wt1_select\""); c && *c && *c != ']'; c++)
        if (*c == ',') allOpts++;
    api->set_param(inst, "wt_pack", "1");
    settle(api, inst);
    api->get_param(inst, "wt_pack", buf, sizeof buf);
    CHECK(!strcmp(buf, "1"), "wt_pack round-trips -> \"%s\"", buf);
    n = api->get_param(inst, "chain_params", big, sizeof big);
    int packOpts = 0;
    for (const char *c = strstr(big, "\"wt1_select\""); c && *c && *c != ']'; c++)
        if (*c == ',') packOpts++;
    CHECK(packOpts > 0 && packOpts < allOpts,
          "picking a pack narrows wt1_select (%d -> %d options)",
          allOpts, packOpts);
    api->set_param(inst, "wt_pack", "0");
    settle(api, inst);

    #define WTDIR "/data/UserData/UserLibrary/Wavetables/"
    api->set_param(inst, "wt1_table", WTDIR "Adventure Kid/AKWP 0001.wt2048");
    api->get_param(inst, "wt1_table", buf, sizeof buf);
    CHECK(strstr(buf, "AKWP 0001.wt2048") != nullptr,
          "wt1 select by path (what the browser writes) -> \"%s\"", buf);

    api->on_midi(inst, note_on, 3, 0);
    struct timespec ts = { 0, 50 * 1000000 };
    long swPeak = 0, swMin = 1 << 30;
    const char *cycle[4] = { WTDIR "Neu KatalYst/NK - ACTIVE.wt2048", "",
                             WTDIR "Adventure Kid/AKWP 0003.wt2048",
                             WTDIR "Neu KatalYst/NK - AGE.wt2048" };
    for (int s = 0; s < 4; s++) {                       /* switch WHILE held */
        api->set_param(inst, "wt1_table", cycle[s]);
        for (int b = 0; b < 60; b++) {                  /* ~175 ms per table */
            api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
            long p = 0;
            for (size_t i = 0; i < MOVE_FRAMES_PER_BLOCK * 2; i++) {
                long v = out[i] < 0 ? -out[i] : out[i];
                if (v > p) p = v;
            }
            if (p > swPeak) swPeak = p;
            if (b > 20 && p < swMin) swMin = p;         /* after load settles */
        }
        nanosleep(&ts, nullptr);
    }
    CHECK(swPeak > 1500 && swMin > 200,
          "sound continuous across 4 mid-note table switches (peak %ld, min %ld)",
          swPeak, swMin);
    api->get_param(inst, "wt1_table", buf, sizeof buf);
    CHECK(strstr(buf, "NK - AGE.wt2048") != nullptr, "landed on \"%s\"", buf);

    /* state stores the table PATH and restores it */
    n = api->get_param(inst, "state", big, sizeof big);
    CHECK(strstr(big, "wt1_table=" WTDIR "Neu KatalYst/NK - AGE.wt2048;") != nullptr,
          "state carries table path");
    memcpy(saved, big, sizeof big);
    api->set_param(inst, "wt1_table", "");
    api->set_param(inst, "synth:state", saved);
    api->get_param(inst, "wt1_table", buf, sizeof buf);
    CHECK(strstr(buf, "NK - AGE.wt2048") != nullptr, "state restores the table");

    api->on_midi(inst, all_off, 3, 0);
    api->set_param(inst, "wt1_table", "");

    /* ---- the drawable digest of the loaded table ----
     * A UI that draws a wavetable needs the real samples; this is the only
     * way they leave the module. Tables are chosen through wt1_select, whose
     * options ARE the files present, rather than by naming a path: this test
     * first hardcoded one that does not exist on the device, and passed
     * anyway because a failed load silently keeps the previous table. */
    api->set_param(inst, "wt1_select", "0");            /* Init */
    settle(api, inst);
    n = api->get_param(inst, "wt1_shape", big, sizeof big);
    CHECK(n > 100, "wt1_shape serves a digest for the Init table (%d bytes)", n);
    {
        int df = 0, ds = 0;
        int got = sscanf(big, "%d,%d,", &df, &ds);
        const char *body = strchr(big, ',');
        if (body) body = strchr(body + 1, ',');
        int blen = body ? (int) strlen(body + 1) : 0;
        CHECK(got == 2 && df > 0 && ds > 0 && blen == df * ds,
              "digest header matches its payload (%d frames x %d = %d chars)",
              df, ds, blen);
        memcpy(saved, big, sizeof big);

        api->set_param(inst, "wt1_select", "1");         /* first real file */
        settle(api, inst);
        for (int i = 0; i < 200 && !strcmp(big, saved); i++) {
            struct timespec t5 = { 0, 5 * 1000000 };
            nanosleep(&t5, nullptr);
            api->get_param(inst, "wt1_shape", big, sizeof big);
        }
        api->get_param(inst, "wt1_table", buf, sizeof buf);
        CHECK(buf[0] == '/', "wt1_select 1 loaded a real file -> \"%s\"", buf);
        CHECK(strcmp(big, saved) != 0,
              "the digest CHANGES when a different table is loaded");
        api->get_param(inst, "wt2_shape", buf, sizeof buf);
        CHECK(buf[0] != 0, "wt2_shape is served independently");
        api->set_param(inst, "wt1_select", "0");
        settle(api, inst);
    }

    /* clear first: get_param leaves the buffer untouched for a key it
         * does not know, so an unzeroed buf would read as the LAST answer */
        memset(buf, 0, sizeof buf);
        /* "lfo1_rate" used to be the example here, from when the LFOs were cut; they are back (hidden), so a mod slot's key is. */
        api->get_param(inst, "mod1_amount", buf, sizeof buf);
        CHECK(buf[0] == 0, "a removed key answers nothing -> \"%s\"", buf);

    /* ---- a note-off must release its voice whatever mode we are in NOW ----
     * Reported from hardware as a note that keeps sounding until you play
     * more notes (which is voice STEALING, not a release). Flipping to Mono
     * with a chord held sent every note-off down the mono arm, which only
     * looks at voices[0]. Regression cover, since one encoder turn or any
     * preset recall that selects Mono gets you there. */
    api->set_param(inst, "voice_mode", "Poly");
    api->set_param(inst, "vca_r", "5");
    api->set_param(inst, "vca_s", "100");
    {
        const int chord[4] = { 55, 59, 62, 67 };
        for (int i = 0; i < 4; i++) {
            uint8_t on[3] = { 0x90, (uint8_t) chord[i], 100 };
            api->on_midi(inst, on, 3, 0);
        }
        for (int b = 0; b < 40; b++) api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
        api->set_param(inst, "voice_mode", "Mono");          /* mid-chord */
        for (int i = 0; i < 4; i++) {
            uint8_t off[3] = { 0x80, (uint8_t) chord[i], 0 };
            api->on_midi(inst, off, 3, 0);
        }
        for (int b = 0; b < 200; b++) api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
        long tail = 0;
        for (int b = 0; b < 400; b++) {
            api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
            for (size_t i = 0; i < MOVE_FRAMES_PER_BLOCK * 2; i++) {
                long v = out[i] < 0 ? -out[i] : out[i];
                if (v > tail) tail = v;
            }
        }
        CHECK(tail < 40, "poly chord released after a mid-chord flip to Mono "
              "(tail peak %ld)", tail);
        api->set_param(inst, "voice_mode", "Poly");
        api->on_midi(inst, all_off, 3, 0);
    }

    /* ---- the .tblr migration: convert, verify, delete ----
     *
     * Runs on every start, and is self-completing because it removes what it
     * converts: an empty folder is a finished migration, so nothing has to
     * remember that it ran and a preset deleted in Schwung's browser has no
     * .tblr left to come back from.
     *
     * Three cases, and the last two are the ones that bite:
     *   - a plain preset moves and the .tblr goes
     *   - a .tblr whose .json ALREADY holds it is dropped, NOT duplicated
     *     (that is the upgrade path from the release that copied without
     *     deleting -- getting it wrong doubles everyone's library)
     *   - a name already owned by a DIFFERENT sound takes the next free one,
     *     so a user preset called "Neu Bass" cannot overwrite the factory
     *     sound of that name, nor be overwritten by it
     */
    {
        const char *lib = "/data/UserData/UserLibrary/Tablor DH Presets";
        const char *store = "/data/UserData/schwung/presets/tinyhouse";
        ::mkdir(lib, 0755);
        struct stat st;

        auto put = [&](const char *file, const char *body) {
            char pth[512]; snprintf(pth, sizeof pth, "%s/%s", lib, file);
            FILE *f = fopen(pth, "w"); if (f) { fputs(body, f); fputc('\n', f); fclose(f); }
        };
        auto gone = [&](const char *file) {
            char pth[512]; snprintf(pth, sizeof pth, "%s/%s", lib, file);
            return ::stat(pth, &st) != 0;
        };
        auto inStore = [&](const char *file) {
            char pth[512]; snprintf(pth, sizeof pth, "%s/%s", store, file);
            return ::stat(pth, &st) == 0;
        };

        put("LT Move.tblr", "TBLR2;flt_res=71;");
        put("Neu Bass.tblr", "TBLR2;flt_freq=7;");   /* collides with a factory name */

        /* a .tblr that its .json already holds, exactly */
        char dup[512]; snprintf(dup, sizeof dup, "%s/LT Dup.json", store);
        FILE *df = fopen(dup, "w");
        if (df) { fputs("{\"name\":\"LT Dup\",\"module\":\"tinyhouse\",\"version\":1,"
                        "\"state\":\"TBLR2;vca_a=12;\"}\n", df); fclose(df); }
        put("LT Dup.tblr", "TBLR2;vca_a=12;");

        /* Drive the REAL path: clear the version stamp and boot a second
         * instance. That exercises the gate as well as the move, and needs no
         * test-only symbol exported from the shipping module.
         *
         * The stamp is SAVED and put back afterwards. Leaving it rewritten is
         * not harmless: the second instance stamps the version of the .so
         * under test against whatever factory.tbl is already installed, so a
         * later deploy of that version finds the stamp already matching and
         * skips seeding. That is exactly how a new factory preset went
         * missing -- the test had told the device it was already done. */
        const char *stampPath = "/data/UserData/schwung/presets/tinyhouse/.installed";
        char stampWas[64] = {};
        bool hadStamp = false;
        if (FILE *sf = fopen(stampPath, "r")) {
            hadStamp = fgets(stampWas, sizeof stampWas, sf) != nullptr;
            fclose(sf);
        }
        ::remove(stampPath);
        void *inst2 = api->create_instance(".", nullptr);
        CHECK(inst2 != nullptr, "a second instance boots for the migration test");
        if (inst2) {
            for (int i = 0; i < 1500; i++) {
                char rb[8] = {};
                api->get_param(inst2, "ready", rb, sizeof rb);
                if (rb[0] == '1') break;
                struct timespec ts = { 0, 10 * 1000000 };
                nanosleep(&ts, nullptr);
            }
            api->destroy_instance(inst2);
        }

        CHECK(gone("LT Move.tblr") && inStore("LT Move.json"),
              "a .tblr is converted and the original removed");
        CHECK(gone("LT Dup.tblr") && !inStore("LT Dup 2.json"),
              "an already-migrated .tblr is dropped, not duplicated");
        CHECK_STOCK(gone("Neu Bass.tblr") && inStore("Neu Bass 2.json") && inStore("Neu Bass.json"),
              "a name owned by another sound takes the next free one");

        /* put the device back exactly as we found it */
        if (hadStamp) {
            if (FILE *sf = fopen(stampPath, "w")) { fputs(stampWas, sf); fclose(sf); }
        } else {
            ::remove(stampPath);
        }

        char rm[512];
        snprintf(rm, sizeof rm, "%s/LT Move.json", store);   ::remove(rm);
        snprintf(rm, sizeof rm, "%s/LT Dup.json", store);    ::remove(rm);
        snprintf(rm, sizeof rm, "%s/Neu Bass 2.json", store); ::remove(rm);
    }

    /* ---- a spare envelope must actually move its destination ----
     *
     * Asked for after the mod matrix went: an envelope on WT Position is how
     * a wavetable reads as a struck instrument -- bright at the attack,
     * gentler as it decays. An LFO cannot do that, it repeats. So the check
     * is the musical one: with the envelope pointed at position and decaying,
     * the tone must get DARKER over the note, and must not when the amount is
     * centred.
     */
    {
        api->set_param(inst, "voice_mode", "Poly");
        api->set_param(inst, "wt1_select", "0");
        api->set_param(inst, "wt1_pos", "0");
        api->set_param(inst, "wt1_level", "110");
        api->set_param(inst, "wt2_level", "0");
        api->set_param(inst, "flt_freq", "127");
        api->set_param(inst, "vca_a", "0");
        api->set_param(inst, "vca_s", "127");
        api->set_param(inst, "vca_r", "20");
        /* a percussive shape: full at the attack, gone by the end */
        api->set_param(inst, "me1_a", "0");
        api->set_param(inst, "me1_d", "75");   /* ~0.2 s: fallen well before the late window */
        api->set_param(inst, "me1_s", "0");
        api->set_param(inst, "me1_r", "40");
        api->set_param(inst, "me1_dst", "WT1 Pos");

        /* Render the SAME note twice -- envelope full, then centred -- and
         * compare block by block. Position modulation shows as the two runs
         * diverging while the envelope is up and converging once it has
         * fallen to a sustain of zero, which is exactly the "full at the
         * attack, gentle after" shape that was asked for.
         *
         * Measured this way rather than by counting zero crossings: with a
         * held note that proxy drifts on its own, and reported the same 3.6
         * whether the modulation was on or off. */
        const int kBlocks = 200;
        static double rms[2][kBlocks];
        for (int pass = 0; pass < 2; pass++) {
            api->set_param(inst, "me1_amt", pass ? "127" : "64");
            api->on_midi(inst, note_on, 3, 0);
            for (int b = 0; b < kBlocks; b++) {
                api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
                double e = 0;
                for (size_t i = 0; i < MOVE_FRAMES_PER_BLOCK; i++) {
                    double v = out[i * 2] / 32768.0;
                    e += v * v;
                }
                rms[pass][b] = sqrt(e / MOVE_FRAMES_PER_BLOCK);
            }
            api->on_midi(inst, all_off, 3, 0);
            for (int b = 0; b < 140; b++) api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
        }
        auto diverge = [&](int lo, int hi) {
            double d = 0, ref = 0;
            for (int b = lo; b < hi; b++) {
                d += fabs(rms[1][b] - rms[0][b]);
                ref += rms[0][b];
            }
            return ref > 1e-9 ? d / ref : 0.0;
        };
        const double early = diverge(3, 25);     /* envelope up */
        const double late  = diverge(150, 200);  /* long after it fell to 0 */
        printf("      [measured] divergence early %.3f, late %.3f\n", early, late);
        CHECK(early > 0.10,
              "an envelope on WT Pos changes the sound while it is up (%.3f)", early);
        CHECK(late < early * 0.5,
              "and the sound returns as it falls to zero (%.3f vs %.3f)", late, early);

        /* and it must reach a different destination when pointed at one */
        api->set_param(inst, "me1_dst", "Filter Freq");
        api->set_param(inst, "me1_amt", "127");
        api->set_param(inst, "flt_freq", "30");
        api->set_param(inst, "me1_s", "127");        /* hold it open */
        api->on_midi(inst, note_on, 3, 0);
        long openPk = 0;
        for (int b = 0; b < 160; b++) {
            api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
            if (b < 40) continue;
            for (size_t i = 0; i < MOVE_FRAMES_PER_BLOCK * 2; i++) {
                long v = out[i] < 0 ? -out[i] : out[i];
                if (v > openPk) openPk = v;
            }
        }
        api->on_midi(inst, all_off, 3, 0);
        for (int b = 0; b < 120; b++) api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);

        api->set_param(inst, "me1_amt", "64");       /* same patch, no modulation */
        api->on_midi(inst, note_on, 3, 0);
        long shutPk = 0;
        for (int b = 0; b < 160; b++) {
            api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
            if (b < 40) continue;
            for (size_t i = 0; i < MOVE_FRAMES_PER_BLOCK * 2; i++) {
                long v = out[i] < 0 ? -out[i] : out[i];
                if (v > shutPk) shutPk = v;
            }
        }
        api->on_midi(inst, all_off, 3, 0);
        for (int b = 0; b < 120; b++) api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
        CHECK(openPk > shutPk * 2,
              "the same envelope opens the filter when pointed there (%ld vs %ld)",
              openPk, shutPk);

        api->set_param(inst, "me1_dst", "None");
        api->set_param(inst, "me1_amt", "64");
        api->set_param(inst, "flt_freq", "127");
    }

    /* ---- presets are Schwung's: the module seeds its factory sounds into
     * that store and recalls through `state`, the same key the host's preset
     * browser writes (shadow_ui_presets.mjs applyStateBlob). Nothing here
     * touches the user's own library -- an earlier version of this test
     * renamed and overwrote real user presets on the device it ran on. */
    {
        const char *dir = "/data/UserData/schwung/presets/tinyhouse";
        struct stat st;
        CHECK(::stat(dir, &st) == 0, "the module seeded Schwung's preset folder");

        char pj[512];
        snprintf(pj, sizeof pj, "%s/Neu Bass.json", dir);
        FILE *jf = fopen(pj, "r");
        CHECK_STOCK(jf != nullptr, "a factory sound is there as a .json");
        if (jf) {
            static char raw[8192];
            size_t got = fread(raw, 1, sizeof raw - 1, jf);
            raw[got] = 0;
            fclose(jf);
            CHECK(strstr(raw, "\"module\":\"tinyhouse\"") && strstr(raw, "\"version\":1"),
                  "it carries Schwung's fields");
            /* the state must survive VERBATIM -- escaping the blob like a
             * name once turned /data/... into -data..., which loads nothing */
            CHECK(strstr(raw, "\"state\":\"TBLR2;") != nullptr, "state is our blob");
            const char *wp = strstr(raw, "wt1_table=");
            CHECK_STOCK(wp && !strncmp(wp + 10, "/data/UserData/", 15),
                  "the wavetable path is intact, not escaped into nonsense");

            /* recall it the way the host does, and hear it land */
            const char *st0 = strstr(raw, "\"state\":\"");
            if (st0) {
                static char blob[4096];
                const char *e = strchr(st0 + 9, '"');
                size_t len = e ? (size_t)(e - (st0 + 9)) : 0;
                if (len && len < sizeof blob) {
                    memcpy(blob, st0 + 9, len); blob[len] = 0;
                    api->set_param(inst, "voice_mode", "Poly");
                    api->set_param(inst, "state", blob);
                    settle(api, inst);
                    api->get_param(inst, "voice_mode", buf, sizeof buf);
                    CHECK_STOCK(!strcmp(buf, "Mono"), "recall applies it (Neu Bass is mono, got \"%s\")", buf);
                    api->get_param(inst, "wt1_table", buf, sizeof buf);
                    CHECK_STOCK(strstr(buf, "NK - ACTIVE") != nullptr,
                          "and loads its wavetable -> \"%s\"", buf);
                }
            }
        }
        api->set_param(inst, "voice_mode", "Poly");
    }

    /* a recalled preset audibly plays. Glass Bells, straight from Schwung's
     * store, through the same `state` write its browser performs. */
    {
        FILE *jf = fopen("/data/UserData/schwung/presets/tinyhouse/Glass Bells.json", "r");
        if (jf) {
            static char raw[8192];
            size_t got = fread(raw, 1, sizeof raw - 1, jf);
            raw[got] = 0; fclose(jf);
            const char *st0 = strstr(raw, "\"state\":\"");
            const char *e = st0 ? strchr(st0 + 9, '"') : nullptr;
            if (e) {
                static char blob[4096];
                size_t len = (size_t)(e - (st0 + 9));
                if (len < sizeof blob) {
                    memcpy(blob, st0 + 9, len); blob[len] = 0;
                    api->set_param(inst, "state", blob);
                    settle(api, inst);
                }
            }
        }
    }
    api->on_midi(inst, note_on, 3, 0);
    long pPeak = 0;
    for (int b = 0; b < 150; b++) {
        api->render_block(inst, out, MOVE_FRAMES_PER_BLOCK);
        for (size_t i = 0; i < MOVE_FRAMES_PER_BLOCK * 2; i++) {
            long v = out[i] < 0 ? -out[i] : out[i];
            if (v > pPeak) pPeak = v;
        }
    }
    CHECK_STOCK(pPeak > 1000, "Glass Bells makes sound (peak %ld)", pPeak);
    api->on_midi(inst, all_off, 3, 0);

    /* the preset page (list_param "preset"): the store's .json files, by index. The list is taken at
     * startup, so files this test wrote since are not in it: the count can be below the files on disk. */
    {
        char cb[32] = {}, nb[256] = {}, pb[32] = {};
        static char s0[8192], s1[8192];
        api->get_param(inst, "preset_count", cb, sizeof cb);
        const int count = atoi(cb);
        /* the files of one bank's folder ("User" is the loose files) */
        auto jsonIn = [](const std::string &dir) {
            int n = 0;
            if (DIR *d = opendir(dir.c_str())) {
                while (dirent *e = readdir(d)) {
                    const char *dot = strrchr(e->d_name, '.');
                    if (dot && e->d_name[0] != '.' && !strcmp(dot, ".json")) n++;
                }
                closedir(d);
            }
            return n;
        };
        char bn[128] = {}, bc[16] = {};
        api->get_param(inst, "bank_name", bn, sizeof bn);
        api->get_param(inst, "bank_count", bc, sizeof bc);
        const std::string store = "/data/UserData/schwung/presets/tinyhouse";
        const int files = jsonIn(!strcmp(bn, "User") ? store : store + "/" + bn);
        CHECK(atoi(bc) >= 1 && bn[0], "the store has banks (%s of %s)", bn, bc);
        CHECK(count > 0 && count <= files, "the preset page counts the bank \"%s\"'s presets (%d of %d files)", bn, count, files);
        /* A fresh instance given this state before its scan finishes, as the host does at boot: where does the preset page land? */
        auto pageAfterRestore = [&](const char *blob, char *bank, char *idx, char *name) {
            void *i2 = api->create_instance(".", nullptr);
            if (!i2) return false;
            api->set_param(i2, "state", blob);
            char rb[8] = {};
            struct timespec ts = { 0, 10 * 1000000 };
            for (int k = 0; k < 1500; k++) {
                api->get_param(i2, "ready", rb, sizeof rb);
                if (rb[0] == '1') break;
                nanosleep(&ts, nullptr);
            }
            api->get_param(i2, "bank_name", bank, 128);
            api->get_param(i2, "preset", idx, 32);
            api->get_param(i2, "preset_name", name, 256);
            api->destroy_instance(i2);
            return true;
        };
        if (count >= 2) {
            api->set_param(inst, "preset", "1");
            api->get_param(inst, "preset", pb, sizeof pb);
            api->get_param(inst, "preset_name", nb, sizeof nb);
            api->get_param(inst, "state", s1, sizeof s1);
            api->set_param(inst, "preset", "0");
            api->get_param(inst, "state", s0, sizeof s0);
            CHECK(atoi(pb) == 1 && nb[0], "preset 1 is named \"%s\"", nb);
            {
                char rbank[128] = {}, ridx[32] = {}, rname[256] = {};
                CHECK(pageAfterRestore(s1, rbank, ridx, rname) && !strcmp(rbank, bn) && atoi(ridx) == 1 && !strcmp(rname, nb),
                      "a restored state puts the preset page back on its preset (%s / %s \"%s\", wanted %s / 1 \"%s\")", rbank, ridx, rname, bn, nb);
            }
            CHECK(strcmp(s0, s1) != 0 && (!strncmp(s0, "TBLR2;", 6) || !strncmp(s0, "{\"tblr\":\"TBLR2;", 15)), "choosing a preset loads its sound (preset 0 and 1 differ)");
            {   /* A chosen preset that is still waiting for the knob to rest answers every read with what it will hold once it lands, and no read applies it early */
                setenv("TB_PRESET_SETTLE_MS", "400", 1);
                void *iw = api->create_instance(".", nullptr);
                setenv("TB_PRESET_SETTLE_MS", "0", 1);
                api->set_param(iw, "state", "TBLR2;");
                for (int w = 0; w < 1500; w++) { char rb[8] = {}; api->get_param(iw, "ready", rb, sizeof rb); if (rb[0] == '1') break; struct timespec t10 = { 0, 10 * 1000000 }; nanosleep(&t10, nullptr); }
                api->set_param(iw, "bank", "0");
                api->set_param(iw, "preset", "0");
                int16_t blk[256];
                for (int w = 0; w < 400; w++) { api->render_block(iw, blk, 128); struct timespec t3 = { 0, 3 * 1000000 }; nanosleep(&t3, nullptr); }
                static char wj[65536], lj[65536], w1[64], w2[64], w3[64];
                api->set_param(iw, "preset", "1");
                api->get_param(iw, "state", wj, sizeof wj);                       /* while it waits */
                api->get_param(iw, "flt_freq", w1, sizeof w1);
                api->get_param(iw, "wt1_level", w2, sizeof w2);
                api->get_param(iw, "state", w3, sizeof w3);
                for (int w = 0; w < 400; w++) { api->render_block(iw, blk, 128); struct timespec t3 = { 0, 3 * 1000000 }; nanosleep(&t3, nullptr); }   /* the rest passes: it lands */
                api->get_param(iw, "state", lj, sizeof lj);
                char l1[64] = {}, l2[64] = {};
                api->get_param(iw, "flt_freq", l1, sizeof l1);
                api->get_param(iw, "wt1_level", l2, sizeof l2);
                std::string wcut(wj), lcut(lj);
                for (std::string *c : { &wcut, &lcut }) { const size_t q = c->find(",\"smp_shape\":"); if (q != std::string::npos) c->resize(q); }       /* the sample shapes come last and land later, on the worker */
                const char *wa = strstr(wcut.c_str(), "\",\""), *la = strstr(lcut.c_str(), "\",\"");
                CHECK(wj[0] == '{' && wa && la && !strcmp(wa, la), "a waiting preset's state holds the same values as the preset once it has landed (%d and %d bytes)", (int) strlen(wj), (int) strlen(lj));
                CHECK(!strcmp(w1, l1) && !strcmp(w2, l2), "a read of a setting while it waits gives what it will hold (flt_freq %s / %s, wt1_level %s / %s)", w1, l1, w2, l2);
                api->destroy_instance(iw);
            }
            {   /* a preset is a whole sound: a setting it does not name is at its default, not where the last sound left it (granular settings leaked into every later preset) */
                char d0[64] = {}, d1[64] = {}, d2[64] = {};
                api->set_param(inst, "preset", "0");
                api->get_param(inst, "smp_gdens", d0, sizeof d0);
                api->set_param(inst, "smp_gdens", "77");
                api->get_param(inst, "smp_gdens", d1, sizeof d1);
                api->set_param(inst, "preset", "0");
                api->get_param(inst, "smp_gdens", d2, sizeof d2);
                CHECK(strcmp(d0, d1) != 0 && !strcmp(d0, d2), "choosing a preset puts a setting it does not name back at its default (%s, edited to %s, after the preset %s)", d0, d1, d2);
            }
            api->set_param(inst, "preset", "9999");
            api->get_param(inst, "preset", pb, sizeof pb);
            CHECK(atoi(pb) == count - 1, "an index past the end stops at the last preset (%s of %d)", pb, count);
        }
        if (atoi(bc) >= 2) {                        /* the Bank knob: another bank lists its own presets, and the sound changes */
            static char sb[8192];
            api->set_param(inst, "preset", "0");
            api->get_param(inst, "state", s0, sizeof s0);
            const int b0 = 0;
            api->set_param(inst, "bank", "1");
            char bn1[128] = {}, bpc[16] = {}, bpi[16] = {};
            api->get_param(inst, "bank_name", bn1, sizeof bn1);
            api->get_param(inst, "preset_count", bpc, sizeof bpc);
            api->get_param(inst, "preset", bpi, sizeof bpi);
            api->get_param(inst, "state", sb, sizeof sb);
            CHECK(strcmp(bn1, bn) != 0 && atoi(bpc) == jsonIn(!strcmp(bn1, "User") ? store : store + "/" + bn1),
                  "bank 1 (\"%s\") lists its own %s presets", bn1, bpc);
            CHECK(atoi(bpi) == 0 && strcmp(sb, s0) != 0, "switching bank starts at its first preset, which plays");
            {
                char rbank[128] = {}, ridx[32] = {}, rname[256] = {};
                CHECK(pageAfterRestore(sb, rbank, ridx, rname) && !strcmp(rbank, bn1) && atoi(ridx) == 0,
                      "a restored state from another bank puts the page on that bank (%s, wanted %s)", rbank, bn1);
            }
            api->set_param(inst, "bank", "9999");
            char bl[16] = {};
            api->get_param(inst, "bank", bl, sizeof bl);
            CHECK(atoi(bl) == atoi(bc) - 1, "a bank past the end stops at the last bank (%s)", bl);
            api->set_param(inst, "bank", std::to_string(b0).c_str());
        }
    }

    api->destroy_instance(inst);
    dlclose(dl);

    printf("\n%s (%d failures)\n", g_fail ? "LOADTEST FAILED" : "LOADTEST PASSED", g_fail);
    return g_fail ? 1 : 0;
}
