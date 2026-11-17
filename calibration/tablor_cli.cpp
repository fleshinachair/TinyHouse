/* Render one note or chord through the real Tablor engine, for the calibration
 * harness. Runs the engine directly, so it needs no Schwung host.
 *
 *   tablor_cli <move_root> <blob_file> <out.raw> <dur_s> <hold_s> <velocity> <n,n,...> <seed>
 *
 * Writes interleaved 32-bit float stereo at 44.1 kHz.
 */
#include "dsp/engine.h"
#include "ported/wav.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <algorithm>
#include <cmath>
#include <sstream>
#include <string>
#include <vector>

using namespace tb;

static std::shared_ptr<Wavetable> loadTable(const std::string &path)
{
    WavData w;
    if (!wavLoad(path.c_str(), w)) return nullptr;
    int frame = w.clmFrameSize > 0 ? w.clmFrameSize : wavFrameSizeFromName(path.c_str());
    if (!frame) frame = wavInferFrameSize(w);
    if (w.clmFrameSize <= 0 && !wavFrameSizeFromName(path.c_str()))
        frame = wavRefineFrameSize(w.samples, frame);
    if (!frame) frame = (int) w.samples.size();
    const int nf = (int) w.samples.size() / frame;
    /* As the module's loader does (dsp/wt/loader.h): every table is normalised to a 0.9 peak, and a table whose clm header says "no
     * interpolation" is stepped through a frame at a time, as Serum does. */
    float peak = 0.0f;
    for (float v : w.samples) peak = std::max(peak, std::fabs(v));
    if (w.normPeak > 0.0f) peak = w.normPeak;                   /* a "tbpk" chunk: the importer rewrote the table (Even/Odd) and says which peak to normalise by */
    if (peak > 0.0f) { const float g = 0.9f / peak; for (float &v : w.samples) v *= g; }
    auto table = std::make_shared<Wavetable>();
    if (nf <= 0 || !wtBuild(*table, w.samples.data(), nf, frame, 44100.0f, 44100.0f)) return nullptr;
    table->stepped = w.clmStepped;
    return table;
}

int main(int argc, char **argv)
{
    if (argc < 9) { fprintf(stderr, "usage: see source\n"); return 2; }
    const std::string root = argv[1];
    const double dur = atof(argv[4]), hold = atof(argv[5]);
    const int vel = atoi(argv[6]);
    srand((unsigned) atoi(argv[8]));

    std::ifstream in(argv[2]);
    std::string blob((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    if (blob.rfind("TBLR2;", 0) != 0) {                          /* a preset .json: its "state" string is the blob */
        const size_t b0 = blob.find("TBLR2;");
        if (b0 != std::string::npos) {
            std::string o;
            for (size_t i = b0; i < blob.size(); i++) {
                if (blob[i] == '\\' && i + 1 < blob.size()) { i++; o += blob[i] == 'n' ? '\n' : blob[i]; continue; }
                if (blob[i] == '"') break;
                o += blob[i];
            }
            blob = o;
        }
    }
    if (blob.rfind("TBLR2;", 0) != 0) { fprintf(stderr, "bad blob\n"); return 2; }

    Engine e;
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
            if (!sampleLoad((root + v).c_str(), *set)) { fprintf(stderr, "sample set failed: %s\n", v.c_str()); return 3; }
            if (k == "smp3_set") e.setSample3(set); else if (k == "smp2_set") e.setSample2(set); else e.setSample(set);
            continue;
        }
        if (k == "conv_set") {                                       /* the convolution reverb's impulse response (a TBS file) */
            if (v.empty()) continue;
            SampleSet ir;
            if (!sampleLoad((root + v).c_str(), ir)) { fprintf(stderr, "conv set failed: %s\n", v.c_str()); return 3; }
            e.setConv(convBuild(ir, 44100.0f));
            continue;
        }
        if (k == "wt1_table" || k == "wt2_table" || k == "wt3_table") {
            if (v != "Init") {
                auto t = loadTable(root + v);
                if (!t) { fprintf(stderr, "table failed: %s\n", v.c_str()); return 3; }
                e.setTable(k[2] - '1', t);
            }
            continue;
        }
        bool found = false;
        const bool onBus = k.size() > 3 && k[0] == 'b' && (k[1] == '1' || k[1] == '2') && k[2] == '.';      /* "b1.key": the FX bus racks' pots */
        const std::string pk = onBus ? k.substr(3) : k;
        for (int i = 0; i < TB_PARAM_COUNT; i++)
            if (pk == tb_params[i].key) { (onBus ? e.busPots[k[1] - '1'][i] : e.pots[i]) = std::stof(v); found = true; break; }
        if (!found) { fprintf(stderr, "unknown key %s\n", k.c_str()); return 3; }
    }

    /* TB_SET="uni_cap=4,voices=3": parameters set over the preset's (by key), for the performance sweeps (calibration/dev/perf_sweep.py). */
    if (const char *ov = getenv("TB_SET")) {
        std::stringstream os(ov);
        for (std::string t; std::getline(os, t, ',');) {
            const size_t q = t.find('=');
            if (q == std::string::npos) continue;
            const std::string k = t.substr(0, q);
            bool found = false;
            for (int i = 0; i < TB_PARAM_COUNT; i++) if (k == tb_params[i].key) { e.pots[i] = std::stof(t.substr(q + 1)); found = true; break; }
            if (!found) { fprintf(stderr, "TB_SET: unknown key %s\n", k.c_str()); return 3; }
        }
    }

    /* TB_MACRO="1=127,3=0": the macros are turned to these positions (0..127) before the notes: their routed parameters move by the change. */
    e.macroSync();
    if (const char *mc = getenv("TB_MACRO")) {
        std::stringstream ms(mc);
        for (std::string t; std::getline(ms, t, ',');) {
            const size_t q = t.find('=');
            if (q == std::string::npos) continue;
            const int i = atoi(t.c_str()) - 1;
            if (i < 0 || i > 7) continue;
            e.pots[TB_P_MAC1 + i] = std::stof(t.substr(q + 1));
            e.macroMoved(i, e.pots[TB_P_MAC1 + i]);
        }
    }

    std::vector<int> notes;
    std::stringstream ss(argv[7]);
    for (std::string n; std::getline(ss, n, ',');) notes.push_back(atoi(n.c_str()));
    /* TB_SEQ=gap: the notes are played one after another, each held `hold` seconds and `gap` seconds apart (a first note against the next). */
    const char *seqEnv = getenv("TB_SEQ");
    const double gap = seqEnv ? atof(seqEnv) : 0.0;
    /* TB_EVENTS=file: a performance, one note per line "on_s off_s note velocity" (a MIDI file's notes, see calibration/dev/midi_ab.py); the note
     * list and hold above are then ignored. */
    struct Ev { int onAt, offAt, note, vel; };
    std::vector<Ev> events;
    if (const char *evFile = getenv("TB_EVENTS")) {
        std::ifstream ef(evFile);
        double on, off; int n, v;
        while (ef >> on >> off >> n >> v)
            events.push_back({ (int) (on * 44100.0 / 128.0), (int) (off * 44100.0 / 128.0), n, v });
    }
    if (!seqEnv && events.empty())
        for (int n : notes) { const uint8_t on[] = { 0x90, (uint8_t) n, (uint8_t) vel }; e.onMidi(on, 3); }

    const int blocks = (int) (dur * 44100.0 / 128.0), offAt = (int) (hold * 44100.0 / 128.0);
    std::vector<float> out((size_t) blocks * 256);
    int16_t b[256];
    std::vector<double> blockUs;
    for (int i = 0; i < blocks; i++) {
        if (!events.empty()) {
            for (const Ev &ev : events) {
                if (i == ev.offAt) { const uint8_t off[] = { 0x80, (uint8_t) ev.note, 0 }; e.onMidi(off, 3); }
                if (i == ev.onAt) { const uint8_t on[] = { 0x90, (uint8_t) ev.note, (uint8_t) ev.vel }; e.onMidi(on, 3); }
            }
        } else if (seqEnv) {
            for (size_t j = 0; j < notes.size(); j++) {
                const int onAt = (int) (j * (hold + gap) * 44100.0 / 128.0);
                if (i == onAt) { const uint8_t on[] = { 0x90, (uint8_t) notes[j], (uint8_t) vel }; e.onMidi(on, 3); }
                if (i == onAt + offAt) { const uint8_t off[] = { 0x80, (uint8_t) notes[j], 0 }; e.onMidi(off, 3); }
            }
        } else if (i == offAt)
            for (int n : notes) { const uint8_t off[] = { 0x80, (uint8_t) n, 0 }; e.onMidi(off, 3); }
        const auto t0 = std::chrono::steady_clock::now();
        e.renderBlock(b, 128);
        { const double us = std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - t0).count(); blockUs.push_back(us); }
        for (int s = 0; s < 256; s++) out[(size_t) i * 256 + s] = (float) b[s] / 32768.0f;
    }
    if (getenv("TB_TIME")) {                                    /* mean and 99th percentile of one block's render, microseconds (a 128-frame block is 2902 us of audio) */
        std::vector<double> sorted(blockUs); std::sort(sorted.begin(), sorted.end());
        double sum = 0; for (double u : sorted) sum += u;
        fprintf(stderr, "TIME mean %.1f p99 %.1f max %.1f\n", sum / std::max<size_t>(1, sorted.size()), sorted.empty() ? 0.0 : sorted[(size_t) (sorted.size() * 0.99)], sorted.empty() ? 0.0 : sorted.back());
    }
    FILE *f = fopen(argv[3], "wb");
    if (!f) return 4;
    fwrite(out.data(), sizeof(float), out.size(), f);
    fclose(f);
    return 0;
}
