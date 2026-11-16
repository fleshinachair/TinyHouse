/* Glitch harness: renders several TinyHouse slots one after another on one thread, as the Move's audio callback does, and times every 128-sample frame.
 * A glitch is a frame that takes longer than the budget (2667 us, minus what the host itself spends), so the numbers that matter are the worst frames, not the average.
 *
 *   glitch_harness <dur_s> <scenario> <preset>[@n,n,n] [<preset>[@n,n,n] ...]
 *
 * <preset> is a TinyHouse preset .json (its "state" blob) or a raw "TBLR2;..." blob file; paths inside it are read as they are (absolute, on the Move).
 * Scenarios (played into every slot at once):
 *   steady   the chord is held the whole time
 *   retrig   the chord is struck every 0.5 s and held 0.4 s (attacks, releases, tails on top of each other)
 *   stack    a note is added every 0.25 s up to the whole list and held (the polyphony ceiling), released at 2/3 of the time
 *   arp      a note every 115 ms held 0.4 s, round the list (overlapping releases)
 *   split    steady, but each frame is rendered as three short calls (40 + 50 + 38 samples: the demo mode's block splitting at note starts)
 *   idle     nothing played (the effects' tails and the bus alone)
 * Output: one line per scenario, microseconds per frame of ALL slots together: mean, p50, p99, p99.9, max, and the number of frames over 1500 / 2000 / 2400 us.
 */
#include "dsp/engine.h"
#include "ported/wav.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <memory>
#include <sstream>
#include <string>
#include <time.h>
#include <vector>

using namespace tb;

static double nowUs()
{
    timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double) ts.tv_sec * 1e6 + (double) ts.tv_nsec / 1e3;
}

static std::shared_ptr<Wavetable> loadTable(const std::string &path)
{
    WavData w;
    if (!wavLoad(path.c_str(), w)) return nullptr;
    int frame = w.clmFrameSize > 0 ? w.clmFrameSize : wavFrameSizeFromName(path.c_str());
    if (!frame) frame = wavInferFrameSize(w);
    if (w.clmFrameSize <= 0 && !wavFrameSizeFromName(path.c_str())) frame = wavRefineFrameSize(w.samples, frame);
    if (!frame) frame = (int) w.samples.size();
    const int nf = (int) w.samples.size() / frame;
    float peak = 0.0f;
    for (float v : w.samples) peak = std::max(peak, std::fabs(v));
    if (w.normPeak > 0.0f) peak = w.normPeak;
    if (peak > 0.0f) { const float g = 0.9f / peak; for (float &v : w.samples) v *= g; }
    auto table = std::make_shared<Wavetable>();
    if (nf <= 0 || !wtBuild(*table, w.samples.data(), nf, frame, 44100.0f, 44100.0f)) return nullptr;
    table->stepped = w.clmStepped;
    return table;
}

/* The blob out of a preset .json (the "state" string) or a raw blob file. */
static std::string readBlob(const std::string &path)
{
    std::ifstream in(path, std::ios::binary);
    std::string t((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    const size_t b = t.find("TBLR2;");
    if (b == std::string::npos) return "";
    if (t.rfind("TBLR2;", 0) == 0) return t;
    std::string o;
    for (size_t i = b; i < t.size(); i++) {
        if (t[i] == '\\' && i + 1 < t.size()) { i++; o += t[i] == 'n' ? '\n' : t[i]; continue; }
        if (t[i] == '"') break;
        o += t[i];
    }
    return o;
}

static bool loadSlot(Engine &e, const std::string &blob)
{
    size_t p = 6;
    while (p < blob.size()) {
        const size_t eq = blob.find('=', p);
        if (eq == std::string::npos) break;
        size_t semi = blob.find(';', eq);
        if (semi == std::string::npos) semi = blob.size();
        const std::string k = blob.substr(p, eq - p), v = blob.substr(eq + 1, semi - eq - 1);
        p = semi + 1;
        if (k == "clip") { e.setClip(parseClip(v.c_str())); continue; }
        if (k == "arp") { e.setArp(parseArp(v.c_str())); continue; }
        if (k == "macn") continue;
        if (k == "mac") { MacroRouteSpec r[Engine::kMaxMacroRoutes]; e.setMacroRoutes(r, parseMacroRoutes(v.c_str(), r, Engine::kMaxMacroRoutes)); continue; }
        if (k == "smp_set" || k == "smp2_set" || k == "smp3_set") {
            if (v.empty()) continue;
            auto set = std::make_shared<SampleSet>();
            if (!sampleLoad(v.c_str(), *set)) { fprintf(stderr, "sample set failed: %s\n", v.c_str()); return false; }
            if (k == "smp3_set") e.setSample3(set); else if (k == "smp2_set") e.setSample2(set); else e.setSample(set);
            continue;
        }
        if (k == "conv_set") {
            if (v.empty()) continue;
            SampleSet ir;
            if (!sampleLoad(v.c_str(), ir)) { fprintf(stderr, "conv set failed: %s\n", v.c_str()); return false; }
            e.setConv(convBuild(ir, 44100.0f));
            continue;
        }
        if (k == "wt1_table" || k == "wt2_table" || k == "wt3_table") {
            if (v != "Init") {
                auto t = loadTable(v);
                if (!t) { fprintf(stderr, "table failed: %s\n", v.c_str()); return false; }
                e.setTable(k[2] - '1', t);
            }
            continue;
        }
        bool found = false;
        const bool onBus = k.size() > 3 && k[0] == 'b' && (k[1] == '1' || k[1] == '2') && k[2] == '.';
        const std::string pk = onBus ? k.substr(3) : k;
        for (int i = 0; i < TB_PARAM_COUNT; i++)
            if (pk == tb_params[i].key) { (onBus ? e.busPots[k[1] - '1'][i] : e.pots[i]) = std::stof(v); found = true; break; }
        if (!found) fprintf(stderr, "unknown key %s (ignored)\n", k.c_str());
    }
    e.macroSync();
    return true;
}

struct Slot { std::unique_ptr<Engine> e; std::vector<int> notes; std::string name; };

static void on(Engine &e, int n, int v) { const uint8_t m[] = { 0x90, (uint8_t) n, (uint8_t) v }; e.onMidi(m, 3); }
static void off(Engine &e, int n) { const uint8_t m[] = { 0x80, (uint8_t) n, 0 }; e.onMidi(m, 3); }

/* Events of one scenario for one slot at frame f (a frame is 128 samples). */
static void events(const std::string &scn, Slot &s, int f, int frames)
{
    Engine &e = *s.e; const std::vector<int> &N = s.notes;
    if (scn == "steady" || scn == "split") { if (f == 0) for (int n : N) on(e, n, 100); return; }
    if (scn == "retrig") {
        const int per = 172, hold = 138;
        if (f % per == 0) for (int n : N) on(e, n, 100);
        if (f % per == hold) for (int n : N) off(e, n);
        return;
    }
    if (scn == "stack") {
        const int step = 86;
        if (f % step == 0 && f / step < (int) N.size()) on(e, N[(size_t) (f / step)], 100);
        if (f == frames * 2 / 3) for (int n : N) off(e, n);
        return;
    }
    if (scn == "arp") {
        const int step = 40, hold = 140;
        if (f % step == 0) on(e, N[(size_t) ((f / step) % (int) N.size())], 90 + (f / step) % 3 * 10);
        if (f >= hold && (f - hold) % step == 0) off(e, N[(size_t) (((f - hold) / step) % (int) N.size())]);
        return;
    }
}

int main(int argc, char **argv)
{
    if (argc < 4) { fprintf(stderr, "usage: glitch_harness <dur_s> <scenario|all> <preset[@n,n,n]>...\n"); return 2; }
#if defined(__aarch64__)
    { uint64_t fpcr; asm volatile("mrs %0, fpcr" : "=r"(fpcr)); fpcr |= (1ull << 24); asm volatile("msr fpcr, %0" :: "r"(fpcr)); }          /* flush-to-zero, as the plugin sets it for its callback */
#endif
    const double dur = atof(argv[1]);
    const std::string scnArg = argv[2];
    std::vector<Slot> slots;
    for (int a = 3; a < argc; a++) {
        std::string spec = argv[a], notes = "48,55,60,64,67,72";
        const size_t at = spec.rfind('@');
        if (at != std::string::npos) { notes = spec.substr(at + 1); spec = spec.substr(0, at); }
        Slot s; s.name = spec; s.e = std::make_unique<Engine>();
        const std::string blob = readBlob(spec);
        if (blob.empty()) { fprintf(stderr, "no blob in %s\n", spec.c_str()); return 3; }
        if (!loadSlot(*s.e, blob)) return 3;
        std::stringstream ss(notes);
        for (std::string n; std::getline(ss, n, ',');) s.notes.push_back(atoi(n.c_str()));
        slots.push_back(std::move(s));
    }
    std::vector<std::string> scns;
    if (scnArg == "all") scns = { "idle", "steady", "retrig", "stack", "arp", "split" }; else scns = { scnArg };
    const int frames = (int) (dur * 44100.0 / 128.0), warm = 100;
    const double traceUs = getenv("GH_TRACE") ? atof(getenv("GH_TRACE")) : 0.0;                 /* GH_TRACE=us: list the frames slower than this */
    printf("%-8s %7s %7s %7s %7s %7s | %6s %6s %6s | per-slot mean us\n", "scenario", "mean", "p50", "p99", "p99.9", "max", ">1500", ">2000", ">2400");
    for (const std::string &scn : scns) {
        for (Slot &s : slots) { for (int n = 0; n < 128; n++) off(*s.e, n); int16_t b[256]; for (int i = 0; i < 400; i++) s.e->renderBlock(b, 128); }     /* settle: let tails of the last scenario die */
        std::vector<double> t; t.reserve((size_t) frames);
        std::vector<double> perSlot(slots.size(), 0.0);
        int16_t b[256];
        for (int f = 0; f < frames + warm; f++) {
            const double t0 = nowUs();
            for (size_t si = 0; si < slots.size(); si++) {
                Slot &s = slots[si];
                const double s0 = nowUs();
                if (scn != "idle") events(scn, s, f, frames + warm);
                if (scn == "split") { s.e->renderBlock(b, 40); s.e->renderBlock(b, 50); s.e->renderBlock(b, 38); }
                else s.e->renderBlock(b, 128);
                if (f >= warm) perSlot[si] += nowUs() - s0;
            }
            if (f >= warm) {
                t.push_back(nowUs() - t0);
                if (traceUs > 0.0 && t.back() > traceUs) printf("  slow frame %d: %.0f us\n", f, t.back());
            }
        }
        std::vector<double> q = t; std::sort(q.begin(), q.end());
        double mean = 0; for (double v : t) mean += v; mean /= (double) t.size();
        auto pct = [&](double p) { return q[std::min(q.size() - 1, (size_t) (p * (double) q.size()))]; };
        int o1 = 0, o2 = 0, o3 = 0;
        for (double v : t) { o1 += v > 1500; o2 += v > 2000; o3 += v > 2400; }
        printf("%-8s %7.0f %7.0f %7.0f %7.0f %7.0f | %6d %6d %6d |", scn.c_str(), mean, pct(0.5), pct(0.99), pct(0.999), q.back(), o1, o2, o3);
        for (double v : perSlot) printf(" %.0f", v / (double) t.size());
        printf("\n"); fflush(stdout);
    }
    return 0;
}
