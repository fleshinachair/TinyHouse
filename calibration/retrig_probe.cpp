/* Play a scripted sequence of note events through a preset and write the stereo float output.
 *   retrig_probe <move_root> <blob_file> <out.raw> <dur_s> <t,on|off,note,vel> ...
 * For hunting clicks on quick note changes: the events are applied at the block boundary at or after t. */
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <fstream>
#include <sstream>
#include "engine.h"
#include "ported/wav.h"
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
    auto table = std::make_shared<Wavetable>();
    if (nf <= 0 || !wtBuild(*table, w.samples.data(), nf, frame, 44100.0f, 44100.0f)) return nullptr;
    return table;
}


int main(int argc, char **argv)
{
    if (argc < 6) { fprintf(stderr, "usage: see source\n"); return 2; }
    const std::string root = argv[1];
    const double dur = atof(argv[4]);
    std::ifstream in(argv[2]);
    std::string blob((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    Engine e;
    size_t p = 6;
    while (p < blob.size()) {
        const size_t eq = blob.find('=', p);
        if (eq == std::string::npos) break;
        size_t semi = blob.find(';', eq);
        if (semi == std::string::npos) semi = blob.size();
        const std::string k = blob.substr(p, eq - p), v = blob.substr(eq + 1, semi - eq - 1);
        p = semi + 1;
        if (k == "wt1_table" || k == "wt2_table" || k == "wt3_table") {
            if (v != "Init") { auto t = loadTable(root + v); if (t) e.setTable(k[2] - '1', t); }
            continue;
        }
        for (int i = 0; i < TB_PARAM_COUNT; i++)
            if (k == tb_params[i].key) { e.pots[i] = std::stof(v); break; }
    }
    struct Ev { double t; bool on; int note, vel; };
    std::vector<Ev> evs;
    for (int a = 5; a < argc; a++) {
        std::stringstream ss(argv[a]);
        std::string t, kind, n, v;
        std::getline(ss, t, ','); std::getline(ss, kind, ','); std::getline(ss, n, ','); std::getline(ss, v, ',');
        evs.push_back({ atof(t.c_str()), kind == "on", atoi(n.c_str()), atoi(v.c_str()) });
    }
    srand(1);
    const int blocks = (int) (dur * 44100.0 / 128.0);
    std::vector<float> out((size_t) blocks * 256);
    int16_t b[256];
    for (int i = 0; i < blocks; i++) {
        const double now = i * 128.0 / 44100.0;
        for (auto &ev : evs)
            if (ev.t >= now && ev.t < now + 128.0 / 44100.0) {
                const uint8_t m[3] = { (uint8_t) (ev.on ? 0x90 : 0x80), (uint8_t) ev.note, (uint8_t) ev.vel };
                e.onMidi(m, 3);
            }
        e.renderBlock(b, 128);
        for (int s = 0; s < 256; s++) out[(size_t) i * 256 + s] = (float) b[s] / 32768.0f;
    }
    FILE *f = fopen(argv[3], "wb");
    fwrite(out.data(), sizeof(float), out.size(), f);
    fclose(f);
    return 0;
}
