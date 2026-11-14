#include <chrono>
#include <complex>
/* Phase 2 DSP validation — numeric golden checks for every ported block.
 *
 * The checks assert MEASURED behaviour (frequency response in dB, envelope
 * timing, waveform values), not just "doesn't crash" — the ER-99 lesson.
 * Runs natively inside the build container AND cross-compiled on the Move.
 */

#include <cmath>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <vector>

#include "../src/ported/filter.h"
#include "../src/ported/adsr.h"
#include "../src/ported/analog_tables.h"
#include "../src/ported/wt_oscillator.h"
#include "../src/dsp/comb.h"
#include "../src/ported/wav.h"
#include "../src/dsp/sample.h"
#include "../src/dsp/preset_digest.h"

using namespace tb;

static int g_fail = 0;
#define CHECK(cond, ...) do { \
    if (!(cond)) { g_fail++; printf("FAIL: " __VA_ARGS__); printf("\n"); } \
    else         {           printf("  ok: " __VA_ARGS__); printf("\n"); } \
} while (0)

static double nowMs()
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double) ts.tv_sec * 1e3 + (double) ts.tv_nsec / 1e6;
}

/* Steady-state gain of `filter` at `hz`: feed a sine, RMS after settling. */
static float filterGainDb(Filter &f, float hz, float sr = 44100.0f)
{
    f.reset();
    const int settle = (int) (sr * 0.2f), measure = (int) (sr * 0.5f);
    std::vector<float> l((size_t) (settle + measure)), r(l.size());
    for (size_t i = 0; i < l.size(); i++)
        l[i] = r[i] = std::sin(2.0 * M_PI * hz * (double) i / sr);
    f.process(l.data(), r.data(), (int) l.size());
    double acc = 0.0;
    for (int i = settle; i < settle + measure; i++)
        acc += (double) l[(size_t) i] * l[(size_t) i];
    double rms = std::sqrt(acc / measure);
    double inRms = 1.0 / std::sqrt(2.0);
    return (float) (20.0 * std::log10(rms / inRms + 1e-12));
}

static void testFilter()
{
    printf("== filter ==\n");
    Filter f;
    f.setSampleRate(44100.0);

    /* LP24 @ 1 kHz: flat passband, ~-6 dB corner (2 cascaded -3 dB stages),
     * -24 dB/oct slope. */
    f.setType(Filter::lowpass);
    f.setSlope(Filter::db24);
    f.setParams(1000.0f, kFilterQ);
    float g100 = filterGainDb(f, 100.0f);
    float g1k  = filterGainDb(f, 1000.0f);
    float g2k  = filterGainDb(f, 2000.0f);
    float g4k  = filterGainDb(f, 4000.0f);
    CHECK(std::fabs(g100) < 0.5f, "LP24 passband flat (100 Hz: %+.2f dB)", g100);
    CHECK(g1k > -7.5f && g1k < -4.5f, "LP24 corner ~-6 dB (1 kHz: %+.2f dB)", g1k);
    float slope = g2k - g4k;
    CHECK(slope > 20.0f && slope < 28.0f, "LP24 slope ~24 dB/oct (2k->4k: %.1f dB)", slope);

    /* LP12: ~-3 dB corner, ~12 dB/oct. */
    f.setSlope(Filter::db12);
    f.setParams(1000.0f, kFilterQ);
    g1k = filterGainDb(f, 1000.0f);
    slope = filterGainDb(f, 2000.0f) - filterGainDb(f, 4000.0f);
    CHECK(g1k > -4.0f && g1k < -2.0f, "LP12 corner ~-3 dB (%+.2f dB)", g1k);
    CHECK(slope > 10.0f && slope < 14.0f, "LP12 slope ~12 dB/oct (%.1f dB)", slope);

    /* Resonance: the voice's q law, res=100% -> q = Q/(1-0.99) — expect a
     * tall peak at the corner. */
    f.setParams(1000.0f, kFilterQ / (1.0f - 0.99f * 0.9f));
    float peak = filterGainDb(f, 1000.0f);
    CHECK(peak > 6.0f, "resonance peaks at corner (%+.1f dB at 90%% res)", peak);

    /* HP24 mirrors LP24. */
    f.setType(Filter::highpass);
    f.setSlope(Filter::db24);
    f.setParams(1000.0f, kFilterQ);
    float hLo = filterGainDb(f, 250.0f), hHi = filterGainDb(f, 4000.0f);
    CHECK(hLo < -40.0f, "HP24 rejects below corner (250 Hz: %+.1f dB)", hLo);
    CHECK(std::fabs(hHi) < 1.0f, "HP24 passes above corner (4 kHz: %+.2f dB)", hHi);

    /* Bandpass peaks at center; notch nulls it. */
    f.setType(Filter::bandpass);
    f.setSlope(Filter::db12);
    f.setParams(1000.0f, kFilterQ);
    float bC = filterGainDb(f, 1000.0f), bLo = filterGainDb(f, 125.0f);
    CHECK(bC > -1.5f && bLo < -15.0f, "BP peaks at center (1k %+.1f, 125 %+.1f dB)", bC, bLo);

    f.setType(Filter::notch);
    f.setParams(1000.0f, kFilterQ);
    float nC = filterGainDb(f, 1000.0f), nLo = filterGainDb(f, 125.0f);
    CHECK(nC < -25.0f && std::fabs(nLo) < 1.0f, "notch nulls center (1k %+.1f, 125 %+.1f dB)", nC, nLo);
}

static void testADSR()
{
    printf("== adsr ==\n");
    AnalogADSR env;
    env.setSampleRate(44100.0);
    env.setAttack(0.1f);
    env.setDecay(0.2f);
    env.setSustainLevel(0.5f);
    env.setRelease(0.15f);

    env.noteOn();
    /* attack: reach ~1.0 around the set time (analog curve hits 1.0 at t_a) */
    int i = 0;
    while (env.getState() == AnalogADSR::State::attack && i < 44100) { env.process(); i++; }
    float tA = (float) i / 44100.0f;
    CHECK(tA > 0.07f && tA < 0.13f, "attack reaches peak in ~0.1 s (%.3f s)", tA);
    CHECK(env.getOutput() >= 0.999f, "peak is 1.0 (%.4f)", env.getOutput());

    /* decay to sustain */
    i = 0;
    while (env.getState() == AnalogADSR::State::decay && i < 44100) { env.process(); i++; }
    float tD = (float) i / 44100.0f;
    CHECK(tD > 0.1f && tD < 0.3f, "decay lands near 0.2 s (%.3f s)", tD);
    CHECK(std::fabs(env.getOutput() - 0.5f) < 0.02f, "sustain level 0.5 (%.3f)", env.getOutput());

    /* sustain holds */
    env.process(4410);
    CHECK(std::fabs(env.getOutput() - 0.5f) < 0.02f, "sustain holds (%.3f)", env.getOutput());

    /* release to idle */
    env.noteOff();
    i = 0;
    while (env.getState() == AnalogADSR::State::release && i < 44100) { env.process(); i++; }
    float tR = (float) i / 44100.0f;
    CHECK(tR > 0.05f && tR < 0.25f, "release lands near 0.15 s (%.3f s)", tR);
    CHECK(env.getState() == AnalogADSR::State::idle && env.getOutput() == 0.0f,
          "ends idle at 0");

    /* attack==0 fast path */
    AnalogADSR fast;
    fast.setSampleRate(44100.0);
    fast.setAttack(0.0f);
    fast.noteOn();
    CHECK(fast.getOutput() == 1.0f && fast.getState() == AnalogADSR::State::decay,
          "attack=0 jumps to peak");

    /* multiply path actually multiplies */
    AnalogADSR mul;
    mul.setSampleRate(44100.0);
    mul.setAttack(0.5f);
    mul.noteOn();
    float l[64], r[64];
    for (auto &v : l) v = 1.0f;
    for (auto &v : r) v = 1.0f;
    mul.processMultiplying(l, r, 64);
    CHECK(l[63] > l[0] && l[63] < 0.05f, "VCA multiply follows early attack (%.4f)", l[63]);
}

/* Short and odd-length wavetables must PLAY, and play at the right pitch.
 *
 * Reported as "shorter wavetable lengths aren't playing" and "the playback is
 * kind of buzzy - makes me wonder if there is a preferential length". Both came
 * from frame-size inference that stopped at 256: a short cycle inferred
 * nothing and the 2048 fallback made nFrames 0 (silent), and a file whose
 * LENGTH divided by 2048 loaded with each frame spanning many real cycles
 * (buzz). Adventure Kid's own AKWF format is 600 samples per cycle and hit the
 * first case exactly. */
static void testShortWavetables()
{
    printf("== short wavetables ==\n");

    /* the inference itself */
    WavData w;
    w.sampleRate = 44100.0f;
    w.samples.assign(600, 0.0f);
    CHECK(wavInferFrameSize(w) == 0,
          "a 600-sample AKWF cycle fits no power-of-two frame (got %d)",
          wavInferFrameSize(w));
    w.samples.assign(128, 0.0f);
    CHECK(wavInferFrameSize(w) == 128, "a 128-sample cycle is found (got %d)",
          wavInferFrameSize(w));
    w.samples.assign(4096, 0.0f);
    CHECK(wavInferFrameSize(w) == 2048,
          "4096 samples still reads as two 2048 frames (got %d)",
          wavInferFrameSize(w));

    /* a short file that REPEATS is several frames, not one long cycle --
     * reported as "the shorter wavetables now loop during playback" */
    {
        std::vector<float> eight(256);
        for (int f = 0; f < 8; f++)
            for (int i = 0; i < 32; i++)
                eight[(size_t)(f * 32 + i)] =
                    std::sin((float) i / 32.0f * 2.0f * (float) M_PI) * (1.0f - 0.05f * f);
        CHECK(wavDetectCycle(eight, 256) == 32,
              "eight 32-sample cycles are found as 32 (got %d)",
              wavDetectCycle(eight, 256));

        std::vector<float> one(256);
        for (int i = 0; i < 256; i++)
            one[(size_t) i] = std::sin((float) i / 256.0f * 2.0f * (float) M_PI);
        CHECK(wavDetectCycle(one, 256) == 0,
              "a genuine single cycle reports no repeat (got %d)",
              wavDetectCycle(one, 256));
    }

    /* a 600-sample sine cycle, resampled, must survive as a sine */
    std::vector<float> cyc(600);
    for (int i = 0; i < 600; i++)
        cyc[(size_t) i] = std::sin((float) i / 600.0f * 2.0f * (float) M_PI);
    std::vector<float> up = wavResampleFrames(cyc, 1, 600, 2048);
    float worst = 0.0f;
    for (int i = 0; i < 2048; i++) {
        float want = std::sin((float) i / 2048.0f * 2.0f * (float) M_PI);
        worst = std::max(worst, std::fabs(up[(size_t) i] - want));
    }
    CHECK(up.size() == 2048 && worst < 0.01f,
          "a 600-sample cycle stretches to 2048 without distorting it (worst %.4f)",
          worst);

    /* and it must build and play at the right pitch */
    Wavetable wt;
    bool built = wtBuild(wt, up.data(), 1, 2048, 44100.0f, 44100.0f);
    CHECK(built && wt.size() == 1, "the stretched cycle builds a table");

    if (built) {
        WTOscillator osc;
        osc.setSampleRate(44100.0);
        osc.setWavetable(&wt);
        WTOscillator::Params op;
        op.position = 0.0f; op.leftGain = 1.0f; op.rightGain = 1.0f;
        float l[8192] = { 0 }, r[8192] = { 0 };
        osc.processAdding(69.0f, op, l, r, 8192);   /* A4 = 440 Hz */
        int crossings = 0;
        for (int i = 1; i < 8192; i++)
            if (l[i - 1] <= 0.0f && l[i] > 0.0f) crossings++;
        float hz = (float) crossings * 44100.0f / 8192.0f;
        CHECK(hz > 420.0f && hz < 460.0f,
              "it sounds at the note it was asked for (%.0f Hz for A4)", hz);
    }
}

/* Short-frame wavetables must play at the pitch they were written at.
 *
 * wavInferFrameSize takes the largest power of two that divides the file, so
 * 16 frames of 256 (4096 samples) read as 2 frames of 2048 -- each holding 8
 * cycles -- and played 8x too high. Reported as "shorter wavetables just
 * sound wrong", with the conclusion that tables MUST be 2048. They must not:
 * they must be READ right. */
static void testShortFrameTables()
{
    printf("== short-frame wavetables ==\n");
    struct C { const char *what; int frame, frames; int morph; int want; };
    const C cases[] = {
        { "16 x 256",              256,  16, 0,  256 },
        { "32 x 512",              512,  32, 0,  512 },
        { "8 x 1024",             1024,   8, 0, 1024 },
        { "16 x 256 morphing",     256,  16, 1,  256 },
        /* the guard: a real 2048 table must NOT be mistaken for short frames,
         * and slow content must not read as a tiny period */
        { "64 x 2048 morphing",   2048,  64, 2, 2048 },
        { "8 x 2048 morphing",    2048,   8, 2, 2048 },
    };
    for (const auto &c : cases) {
        std::vector<float> s((size_t) c.frame * c.frames);
        for (int f = 0; f < c.frames; f++)
            for (int i = 0; i < c.frame; i++) {
                float u = (float) i / c.frame, v;
                if (c.morph == 2) { v = 0; int H = 1 + f * 2;
                    for (int h = 1; h <= H; h++) v += std::sin(2*(float)M_PI*h*u)/h; v *= 0.5f; }
                else if (c.morph == 1) { float m = (float) f / c.frames;
                    v = ((1-m)*std::sin(2*(float)M_PI*u)
                       + m*(std::sin(2*(float)M_PI*u)+0.5f*std::sin(6*(float)M_PI*u))) * 0.7f; }
                else v = std::sin(2*(float)M_PI*u) * 0.8f;
                s[(size_t) f * c.frame + i] = v;
            }
        int got = wavRefineFrameSize(s, wavInferFrameSizeForLength((int) s.size()));
        CHECK(got == c.want, "%s reads as frame %d (got %d)", c.what, c.want, got);
    }

    /* the author's own declaration outranks any guess from the content */
    CHECK(wavFrameSizeFromName("/x/ADD Low FM 001 2048.wav") == 2048, "name: trailing 2048");
    CHECK(wavFrameSizeFromName("/x/Growl 512.wav") == 512, "name: trailing 512");
    CHECK(wavFrameSizeFromName("/x/Pad_256.wav") == 256, "name: underscore 256");
    CHECK(wavFrameSizeFromName("/x/AKWP 0042.wav") == 0, "name: 0042 is an index, not a size");
    CHECK(wavFrameSizeFromName("/x/Bass 600.wav") == 0, "name: 600 is not a legal frame");
    CHECK(wavFrameSizeFromName("/x/render float32.wav") == 0,
          "name: the 32 in \"float32\" is not a frame size");
    CHECK(wavFrameSizeFromName("/x/Sweep-1024.wav") == 1024, "name: hyphen 1024");
}

static void put(FILE*f,const void*p,size_t n){fwrite(p,n,1,f);}
static void p32(FILE*f,uint32_t v){put(f,&v,4);}
static void p16(FILE*f,uint16_t v){put(f,&v,2);}

/* Every wav flavour a wavetable can arrive in must load AND play at the
 * pitch it was written at. A file rejected by the loader, or read at the
 * wrong frame size, is the same thing to a user: "this wavetable does not
 * work". WAVE_FORMAT_EXTENSIBLE is the one that used to be rejected outright
 * -- it is what every tool writing through the Windows API produces. */
/* tag: 1=pcm 3=float 0xFFFE=extensible(sub) ; bits: 8/16/24/32/64 */
static void writeWav(const char*path,const std::vector<float>&s,int tag,int bits,
                     int channels,int sub,const char*clm)
{
    FILE*f=fopen(path,"wb");
    const int bytesPer=bits/8;
    const uint32_t dataBytes=(uint32_t)s.size()*bytesPer*channels;
    const uint32_t fmtLen=(tag==0xFFFE)?40u:16u;
    uint32_t clmLen=clm?(uint32_t)strlen(clm):0; if(clmLen&1) clmLen++;
    fwrite("RIFF",1,4,f); p32(f,4+8+fmtLen+(clm?8+clmLen:0)+8+dataBytes); fwrite("WAVE",1,4,f);
    fwrite("fmt ",1,4,f); p32(f,fmtLen);
    p16(f,(uint16_t)tag); p16(f,(uint16_t)channels); p32(f,44100);
    p32(f,44100*bytesPer*channels); p16(f,(uint16_t)(bytesPer*channels)); p16(f,(uint16_t)bits);
    if(tag==0xFFFE){ p16(f,22); p16(f,(uint16_t)bits); p32(f,0);
        p16(f,(uint16_t)sub); p16(f,0); p32(f,0); p32(f,0); p32(f,0); }
    if(clm){ fwrite("clm ",1,4,f); p32(f,clmLen);
        fwrite(clm,1,strlen(clm),f); for(uint32_t i=(uint32_t)strlen(clm);i<clmLen;i++) fputc(0,f); }
    fwrite("data",1,4,f); p32(f,dataBytes);
    for(float v : s) for(int c=0;c<channels;c++){
        if(tag==3||sub==3){ if(bits==64){double d=v;put(f,&d,8);} else put(f,&v,4); }
        else if(bits==8){ uint8_t b=(uint8_t)(v*127.0f+128.0f); put(f,&b,1); }
        else if(bits==16){ int16_t x=(int16_t)(v*32767.0f); put(f,&x,2); }
        else if(bits==24){ int32_t x=(int32_t)(v*8388607.0f); put(f,&x,3); }
        else { int32_t x=(int32_t)(v*2147483000.0f); put(f,&x,4); }
    }
    fclose(f);
}
static float heardHz(const std::vector<float>&data,int fs,int nf){
    Wavetable wt; if(!wtBuild(wt,data.data(),nf,fs,44100.0f,44100.0f)) return -1;
    WTOscillator osc; osc.setSampleRate(44100.0); osc.setWavetable(&wt);
    WTOscillator::Params p; p.position=0; p.leftGain=1; p.rightGain=1;
    std::vector<float> l(44100,0),r(44100,0);
    osc.processAdding(57.0f,p,l.data(),r.data(),44100);
    int zc=0; for(size_t i=1;i<l.size();i++) if(l[i-1]<=0&&l[i]>0) zc++;
    return zc*44100.0f/l.size();
}

/* The importer rewrites a wavetable for Serum's Even/Odd warp (x + c x half a cycle later, up to twice the original peak) and says in a "tbpk" chunk which peak to normalise by */
static void testWavNormPeak()
{
    printf("== wav tbpk chunk ==\n");
    std::vector<float> s(2048 * 2);
    for (size_t i = 0; i < s.size(); i++) s[i] = 2.0f * std::sin(6.2831853f * (float) (i % 2048) / 2048.0f);
    const char *path = "tbpk_test.wav";
    writeWav(path, s, 3, 32, 1, 0, "<!>2048 01000000 wavetable");
    { FILE *f = fopen(path, "ab"); fwrite("tbpk", 1, 4, f); p32(f, 4); const float pk = 1.0f; fwrite(&pk, 1, 4, f); fclose(f); }
    WavData w;
    CHECK(wavLoad(path, w) && w.samples.size() == s.size(), "a wav with a tbpk chunk after the data still loads");
    CHECK(std::fabs(w.normPeak - 1.0f) < 1e-6f, "its tbpk peak is read (%.3f)", w.normPeak);
    writeWav(path, s, 3, 32, 1, 0, "<!>2048 01000000 wavetable");
    WavData w2;
    CHECK(wavLoad(path, w2) && w2.normPeak == 0.0f, "a wav without one has none (%.3f)", w2.normPeak);
    remove(path);
}

/* The comb / flanger voice filters (comb.h): the impulse response's spectrum is Serum's measured law a (1 + c z^-D) / (1 - g z^-D) */
static void testCombFilter()
{
    printf("== comb filter ==\n");
    const double sr = 44100.0, hz = 425.0, D = sr / hz;
    struct C { int kind, sign; float reso; const char *name; };
    for (const C &c : { C{ 0, 1, 0.5f, "Comb +" }, C{ 0, -1, 0.7f, "Comb -" }, C{ 1, 1, 0.3f, "Flange +" }, C{ 1, -1, 0.8f, "Flange -" } }) {
        CombFilter f; f.setSampleRate(sr);
        std::vector<float> l(16384, 0.0f), r(16384, 0.0f);
        l[0] = r[0] = 1.0f;
        for (int off = 0; off < 16384; off += 128) f.process(l.data() + off, r.data() + off, 128, c.kind, c.sign, c.reso, (float) hz);
        const double g = c.sign * std::min((double) c.reso, 0.99), cc = c.kind ? (double) c.sign : c.sign * g * g, a = 0.4965 / (1.0 + std::fabs(g));
        double worst = 0;
        for (double fr : { 200.0, 640.0, 1111.0, 2600.0 }) {
            double re = 0, im = 0;
            for (int i = 0; i < 16384; i++) { re += l[(size_t) i] * std::cos(6.283185307 * fr * i / sr); im -= l[(size_t) i] * std::sin(6.283185307 * fr * i / sr); }
            const double th = 6.283185307 * fr * D / sr;
            const double nr = 1.0 + cc * std::cos(th), ni = -cc * std::sin(th), dr = 1.0 - g * std::cos(th), di = g * std::sin(th);
            const double want = a * std::sqrt((nr * nr + ni * ni) / (dr * dr + di * di)), got = std::sqrt(re * re + im * im);
            worst = std::max(worst, std::fabs(20.0 * std::log10((got + 1e-12) / (want + 1e-12))));
        }
        CHECK(worst < 0.5, "%s at reso %.1f: impulse response within %.2f dB of the law", c.name, c.reso, worst);
    }
    /* the phaser chain: a (1 + A^N) / (1 - g A^N), A a first-order all-pass at 0.511 x the cutoff */
    for (int stages : { 2, 4, 8 }) {
        const float reso = 0.45f;
        PhaserFilter f; f.setSampleRate(sr);
        std::vector<float> l(16384, 0.0f), r(16384, 0.0f);
        l[0] = r[0] = 1.0f;
        for (int off = 0; off < 16384; off += 128) f.process(l.data() + off, r.data() + off, 128, stages, reso, 900.0f);
        const double t = std::tan(3.14159265358979 * 0.511 * 900.0 / sr), a1 = (t - 1.0) / (t + 1.0), g = reso, a = 0.4965 / (1.0 + g);
        double worst = 0;
        for (double fr : { 150.0, 400.0, 900.0, 2500.0, 7000.0 }) {
            double re = 0, im = 0;
            for (int i = 0; i < 16384; i++) { re += l[(size_t) i] * std::cos(6.283185307 * fr * i / sr); im -= l[(size_t) i] * std::sin(6.283185307 * fr * i / sr); }
            const double w = 6.283185307 * fr / sr;
            std::complex<double> z = std::polar(1.0, -w), ap = (a1 + z) / (1.0 + a1 * z), An = std::pow(ap, stages);
            const double want = a * std::abs((1.0 + An) / (1.0 - g * An)), got = std::sqrt(re * re + im * im);
            worst = std::max(worst, std::fabs(20.0 * std::log10((got + 1e-12) / (want + 1e-12))));
        }
        CHECK(worst < 0.6, "Phase %d at reso %.2f: impulse response within %.2f dB of the law", 6 * stages, reso, worst);
    }
}

static void testWavFormats()
{
    printf("== wav formats ==\n");

    struct F { const char*name; int tag,bits,ch,sub; const char*clm; int frame,frames; };
    const F fs[] = {
      {"float32 mono",            3,32,1,0,nullptr,2048,8},
      {"float64 mono",            3,64,1,0,nullptr,2048,8},
      {"pcm16 mono",              1,16,1,0,nullptr,2048,8},
      {"pcm24 mono",              1,24,1,0,nullptr,2048,8},
      {"pcm32 mono",              1,32,1,0,nullptr,2048,8},
      {"pcm8  mono",              1, 8,1,0,nullptr,2048,8},
      {"pcm16 STEREO",            1,16,2,0,nullptr,2048,8},
      {"EXTENSIBLE pcm16",   0xFFFE,16,1,1,nullptr,2048,8},
      {"EXTENSIBLE float32", 0xFFFE,32,1,3,nullptr,2048,8},
      {"EXTENSIBLE stereo24",0xFFFE,24,2,1,nullptr,2048,8},
      {"clm says 1024",           3,32,1,0,"<!>1024 00000000 wavetable",1024,16},
      {"clm says 256",            3,32,1,0,"<!>256 00000000 wavetable",  256,16},
      {"short frames, no clm",    3,32,1,0,nullptr, 256,16},
      {"single cycle 600",        3,32,1,0,nullptr, 600,1},
    };
    
    int bad=0;
    for (auto &c : fs) {
        std::vector<float> s((size_t)c.frame*c.frames);
        for(int f=0;f<c.frames;f++) for(int i=0;i<c.frame;i++)
            s[(size_t)f*c.frame+i]=std::sin(2*(float)M_PI*i/c.frame)*0.8f;
#ifndef TB_TEST_TMP
#define TB_TEST_TMP "/tmp/"
#endif
        char path[128]; snprintf(path,sizeof path,TB_TEST_TMP "fmt_%s.wav",c.name);
        for(char*q=path;*q;q++) if(*q==' ') *q='_';
        writeWav(path,s,c.tag,c.bits,c.ch,c.sub,c.clm);
        WavData w;
        if(!wavLoad(path,w)){ CHECK(false, "%s: REJECTED by the loader", c.name); bad++; continue; }
        int frame = w.clmFrameSize>0 ? w.clmFrameSize : wavFrameSizeFromName(path);
        if(!frame) frame = wavInferFrameSize(w);
        if(w.clmFrameSize<=0 && (int)w.samples.size()<=2048){
            int cyc=wavDetectCycle(w.samples,(int)w.samples.size()); if(cyc>0) frame=cyc; }
        if(w.clmFrameSize<=0 && !wavFrameSizeFromName(path))
            frame = wavRefineFrameSize(w.samples, frame);
        if(!frame) frame=(int)w.samples.size();
        int nf = (int)w.samples.size()/frame;
        std::vector<float> d=w.samples;
        if(!wavIsPow2(frame)||frame<32){ d=wavResampleFrames(d,nf,frame,2048); frame=2048; }
        float hz = nf ? heardHz(d,frame,nf) : -1;
        bool ok = hz>205 && hz<236;
        if(!ok) bad++;
        CHECK(ok, "%s -> frame %d, %d frames, %.1f Hz", c.name, frame, nf, hz);
    }
    
    
    (void) bad;
}

/* A real additive table that MUST NOT be "corrected".
 *
 * DigiAdd07 is 13824 samples of 16-bit PCM, no clm, and morphs from a full
 * harmonic series to even harmonics only. Its later frames therefore consist
 * of two identical half-cycles -- legitimately, because that is what an
 * even-harmonics-only waveform looks like. Any detector that halves a frame
 * on seeing repeated halves destroys this table: the EARLY frames are not
 * repeated, so halving reads two different cycles as one.
 *
 * The distribution is what separates the two cases. A table whose real frame
 * is half its apparent one repeats UNIFORMLY; this one grades smoothly from
 * 1.91 down to 0.00 across its frames. wavRefineFrameSize requires the whole
 * file to repeat, so it correctly leaves this alone. */
static void testMorphToEvenHarmonicsKeepsItsFrame()
{
    printf("== a table morphing to even harmonics keeps its frame ==\n");
    const int F = 512, N = 27;
    std::vector<float> s((size_t) F * N);
    for (int k = 0; k < N; k++) {
        float m = (float) k / (N - 1);          /* 0 = all harmonics, 1 = even only */
        for (int i = 0; i < F; i++) {
            float u = (float) i / F, v = 0.0f;
            for (int h = 1; h <= 16; h++) {
                float amp = (h & 1) ? (1.0f - m) / h : 1.0f / h;
                v += amp * std::sin(2.0f * (float) M_PI * h * u);
            }
            s[(size_t) k * F + i] = v * 0.4f;
        }
    }
    int got = wavRefineFrameSize(s, 512);
    CHECK(got == 512, "frames stay 512, not halved to 256 (got %d)", got);

    /* The measured gap the threshold sits in, from real files:
     *   must NOT halve   DigiAdd07 0.478, WAVETABLE Factory 0.999,
     *                    a genuine 2048 table 1.000
     *   MUST halve       128-frame table 0.056/0.043/0.026/0.056,
     *                    256-frame table 0.011
     * 0.15 is ~3x clear of both sides. These two synthetic tables stand in
     * for each end, so a future tweak that closes the gap fails here. */
    {
        /* a 2048 frame that is really 16 cycles of 128, morphing */
        const int F = 2048, N = 4;
        std::vector<float> t((size_t) F * N);
        for (int k = 0; k < N * 16; k++) {
            float m = (float) k / (N * 16 - 1);
            for (int i = 0; i < 128; i++) {
                float u = (float) i / 128, v = 0.0f;
                for (int h = 1; h <= 1 + (int) (m * 15); h++)
                    v += std::sin(2.0f * (float) M_PI * h * u) / h;
                t[(size_t) k * 128 + i] = v * 0.4f;
            }
        }
        CHECK(wavRefineFrameSize(t, 2048) == 128,
              "a 2048 frame holding 16 morphing cycles resolves to 128 (got %d)",
              wavRefineFrameSize(t, 2048));
    }

    /* and the genuine case still IS caught: every frame two identical halves */
    std::vector<float> t((size_t) F * N);
    for (int k = 0; k < N; k++)
        for (int i = 0; i < F; i++)
            t[(size_t) k * F + i] =
                std::sin(2.0f * (float) M_PI * 2.0f * i / F) * 0.8f;   /* 2 cycles */
    CHECK(wavRefineFrameSize(t, 512) == 256,
          "a frame that really is two cycles still halves");
}

static void testAnalogTables()
{
    printf("== analog tables ==\n");
    double t0 = nowMs();
    AnalogTables tables(44100.0);
    double buildMs = nowMs() - t0;
    printf("  build: %.0f ms, %.2f MB\n", buildMs, tables.bytes() / 1e6);
    CHECK(buildMs < 250.0, "analog tables build fast (%.0f ms; was 2743 additive)", buildMs);
    CHECK(tables.bytes() < 2e6, "analog tables decimated (%.2f MB)", tables.bytes() / 1e6);

    /* Spectrum-built tables must equal the additive formula wherever the
     * table can hold every harmonic. Note 60's level is built for top note
     * 60.5 (311 Hz, ~70 harmonics — well under the table Nyquist). */
    float maxErr = 0.0f;
    const float refNote = 60.5f, refHz = midiNoteToHz(60.5f);
    for (int i = 0; i < 64; i++) {
        float ph = (float) i / 64.0f;
        float gotT = tables.processTriangle(60.0f, ph);
        float wantT = waves::triangle(ph, refHz, 44100.0f);
        float gotS = tables.processSawUp(60.0f, ph);
        float wantS = waves::sawUp(ph, refHz, 44100.0f);
        maxErr = std::max({ maxErr, std::fabs(gotT - wantT), std::fabs(gotS - wantS) });
    }
    (void) refNote;
    CHECK(maxErr < 0.01f, "FFT-built tables match additive formula (err %.4f)", maxErr);

    /* Square via saws ~ +/-1; pulse pw=0.5 has ~0 DC. */
    float sq = tables.processSquare(60.0f, 0.1f);
    CHECK(sq > 0.7f && sq < 1.3f, "square high half ~ +1 (%.2f)", sq);
    double dc = 0.0;
    for (int i = 0; i < 512; i++)
        dc += tables.processPulse(60.0f, (float) i / 512.0f, 0.5f);
    CHECK(std::fabs(dc / 512.0) < 0.02, "pulse pw=0.5 DC ~ 0 (%.4f)", dc / 512.0);

    /* Noise: white RMS ~ 0.1 (gin's stddev), pink bounded. */
    WhiteNoise wn;
    PinkNoise pn;
    double wAcc = 0.0; float pPeak = 0.0f;
    for (int i = 0; i < 44100; i++) {
        float w = wn.nextSample(); wAcc += (double) w * w;
        pPeak = std::max(pPeak, std::fabs(pn.nextSample()));
    }
    float wRms = (float) std::sqrt(wAcc / 44100.0);
    CHECK(wRms > 0.08f && wRms < 0.12f, "white noise RMS ~ 0.1 (%.3f)", wRms);
    CHECK(pPeak > 0.05f && pPeak < 1.5f, "pink noise bounded (peak %.2f)", pPeak);

    /* Sub oscillator renders sanely. */
    StereoOscillator sub(tables);
    sub.setSampleRate(44100.0);
    sub.noteOn(0.0f);
    StereoOscillator::Params sp;
    sp.wave = Wave::square;
    float l[512] = {0}, r[512] = {0};
    sub.processAdding(48.0f, sp, l, r, 512);
    float pk = 0.0f; bool finite = true;
    for (auto v : l) { pk = std::max(pk, std::fabs(v)); finite &= std::isfinite(v); }
    CHECK(finite && pk > 0.5f && pk < 2.0f, "sub osc renders square (peak %.2f)", pk);
}

/* A one-cycle sine: anything else in the output came from the code under test. */
static void buildSineTable(Wavetable &wt)
{
    std::vector<float> s(2048);
    for (int i = 0; i < 2048; i++)
        s[(size_t) i] = std::sin(2.0f * (float) M_PI * (float) i / 2048.0f);
    wtBuild(wt, s.data(), 1, 2048, 44100.0f, 44100.0f);
}

static double stackDb(VoicedWTOscillator &a, VoicedWTOscillator *silent, float note, int voices)
{
    a.noteOn(0.0f);
    if (silent) silent->noteOn(0.0f);
    VoicedWTParams vp; vp.voices = voices;
    VoicedWTParams vs; vs.voices = voices; vs.gain = 0.0f;
    double acc = 0.0; int count = 0;
    for (int b = 0; b < 44100 * 3 / 128; b++) {
        float l[128] = {}, r[128] = {};
        a.processAdding(note, vp, l, r, 128);
        if (silent) silent->processAdding(note, vs, l, r, 128);
        if (b * 128 > 44100 * 2)
            for (int i = 0; i < 128; i++) { acc += (double) l[i] * l[i]; count++; }
    }
    return 10.0 * std::log10(acc / count + 1e-20);
}

/* Serum's Dist* oscillator warps: a static shaper on every stream's sample. A sine through it gains odd harmonics, a pre-gain of 0.02 with an output gain of 50 is transparent,
 * and a hard clip at high gain squares the wave off at the output gain. */
/* Serum's Sub-driven FM / PD / RM warps and its filter warps against what Serum measured (calibration/probe_warp_fm.py, probe_filterwarp.py): a sine oscillator at
 * note 48, the Sub a sine a fifth above it, read as the carrier and its first sidebands re the unwarped carrier. */
static void testXwarp()
{
    printf("== oscillator FM / PD / RM / filter warps ==\n");
    Wavetable wt;
    buildSineTable(wt);
    const double sr = 44100.0, f0 = 130.8128, fm = f0 * std::pow(2.0, 7.0 / 12.0);
    auto render = [&](float note, int xw, float a, std::vector<float> &out, int xsrc = 0, int xw2 = 0, float a2 = 0.0f) {
        VoicedWTOscillator o(kMaxUnison);
        o.setSampleRate(sr); o.setWavetable(&wt); o.noteOn(0.0f);
        VoicedWTParams vp; vp.voices = 1; vp.xwarp = xw; vp.xamount = a; vp.xsrc = xsrc; vp.xwarp2 = xw2; vp.xamount2 = a2; vp.xsrc2 = xsrc;
        out.clear();
        double ph = 0.0;
        for (int blk = 0; blk < 44100 / 128; blk++) {
            float l[128] = {}, r[128] = {}, sub[128];
            for (int i = 0; i < 128; i++) { sub[i] = (float) std::sin(6.283185307179586 * ph); ph += fm / sr; }
            const float *srcs[4] = { sub, sub, sub, sub };
            o.processAdding(note, vp, l, r, 128, srcs);
            if (blk * 128 > 8192) out.insert(out.end(), l, l + 128);
        }
    };
    auto amp = [&](const std::vector<float> &x, double hz) {
        double re = 0, im = 0;
        for (size_t i = 0; i < x.size(); i++) { const double ph = 6.283185307179586 * hz * (double) i / sr; const double w = 0.5 - 0.5 * std::cos(6.283185307179586 * (double) i / (double) x.size()); re += w * x[i] * std::cos(ph); im += w * x[i] * std::sin(ph); }
        return std::sqrt(re * re + im * im) * 4.0 / (double) x.size();
    };
    std::vector<float> ref, x;
    render(48.0f, 0, 0.0f, ref);
    const double a0 = amp(ref, f0);
    auto line = [&](double hz) { return amp(x, hz) / a0; };
    struct Case { const char *name; int xw; float a; double c, p1, p2; int src; };
    /* Serum's numbers (carrier, fc + fm, fc + 2 fm), each re the unwarped carrier */
    const Case cases[] = {
        { "FM 0.20", 3, 0.20f, 0.944, 0.230, 0.028, 0 }, { "FM 0.30", 3, 0.30f, 0.001, 0.517, 0.432, 0 },
        { "PD 0.40", 4, 0.40f, 0.828, 0.386, 0.085, 0 }, { "PD 0.50", 4, 0.50f, 0.183, 0.569, 0.369, 0 },
        { "RM 0.50", 5, 0.50f, 0.500, 0.329, 0.000, 0 }, { "RM 1.00", 5, 1.00f, 0.000, 0.657, 0.000, 0 },
        /* the same warps with an oscillator as the modulator (calibration/probe_warp_osc.py): 1.52 times as strong */
        { "FM osc 0.20", 3, 0.20f, 0.874, 0.337, 0.062, 1 }, { "FM osc 0.25", 3, 0.25f, 0.361, 0.579, 0.298, 1 },
        { "PD osc 0.40", 4, 0.40f, 0.625, 0.518, 0.181, 1 },
        { "RM osc 0.60", 5, 0.60f, 0.400, 0.600, 0.000, 1 }, { "AM osc 0.60", 6, 0.60f, 1.000, 0.600, 0.000, 1 },
    };
    for (const Case &c : cases) {
        render(48.0f, c.xw, c.a, x, c.src);
        const double lc = line(f0), l1 = line(f0 + fm), l2 = line(f0 + 2 * fm);
        CHECK(std::fabs(lc - c.c) < 0.06 && std::fabs(l1 - c.p1) < 0.05 && std::fabs(l2 - c.p2) < 0.05,
              "%s: carrier %.3f (Serum %.3f), +fm %.3f (%.3f), +2fm %.3f (%.3f)", c.name, lc, c.c, l1, c.p1, l2, c.p2);
    }
    /* two warps on one oscillator apply in turn: AM twice is x (1 + 1.2 S)^2 = x (1.72 + 2.4 S - 0.72 cos 2S...), so the carrier is 1.72 and the +fm line 1.2 */
    {
        render(48.0f, 6, 0.6f, x, 1, 6, 0.6f);
        const double lc = line(f0), l1 = line(f0 + fm);
        CHECK(std::fabs(lc - 1.72) < 0.08 && std::fabs(l1 - 1.2) < 0.08, "two AM warps: carrier %.3f (1.72), +fm %.3f (1.2)", lc, l1);
    }
    /* filter warps: a first-order filter at an absolute corner (amount 0.4 = 663 Hz): a sine at 1046 Hz passes at 1/sqrt(1 + (f/c)^2) low, the complement high */
    {
        std::vector<float> lo, hi, dry;
        render(84.0f, 0, 0.0f, dry); render(84.0f, 1, 0.4f, lo); render(84.0f, 2, 0.4f, hi);
        const double f = 1046.502, gl = amp(lo, f) / amp(dry, f), gh = amp(hi, f) / amp(dry, f), c = 663.0;
        const double wl = 1.0 / std::sqrt(1.0 + (f / c) * (f / c)), wh = (f / c) * wl;
        CHECK(std::fabs(gl - wl) < 0.04 && std::fabs(gh - wh) < 0.04, "filter warp at 0.4: low %.3f (first order %.3f), high %.3f (%.3f)", gl, wl, gh, wh);
        render(84.0f, 1, 1.0f, lo);
        CHECK(std::fabs(amp(lo, f) / amp(dry, f) - 1.0) < 0.03, "low-pass warp at 1.0 is open (%.3f)", amp(lo, f) / amp(dry, f));
    }
    /* SelfPD: the phase pushed by the wave's own value. A sine at amount 0.2 has its 2nd harmonic at -23.1 dB (Serum, calibration/validate_static_warps.py), at 0.35 -14.0 dB (this table peaks at 1, the loader's at 0.9, so the push reads about 1 dB strong here) */
    {
        render(48.0f, 7, 0.2f, x);
        const double h2 = 20.0 * std::log10(amp(x, 2 * f0) / a0);
        CHECK(std::fabs(h2 + 23.1) < 2.5, "SelfPD 0.20: 2nd harmonic %.1f dB (Serum -23.1)", h2);
        render(48.0f, 7, 0.35f, x);
        const double h2b = 20.0 * std::log10(amp(x, 2 * f0) / a0);
        CHECK(std::fabs(h2b + 14.0) < 2.0, "SelfPD 0.35: 2nd harmonic %.1f dB (Serum -14.0)", h2b);
    }
    /* the Noise as the modulator (source 4): RM is (1 - a) x + 0.409 a^2 x n, so the carrier line is 1 - a; FM below 0.45 and PD below 0.55 do nothing; PD at 0.8 leaves the carrier
     * at -13.6 dB (Serum, calibration/probe_noise_warp_law.py), FM at 0.5 at -0.9 dB */
    {
        FastGauss g(1.0f);
        auto renderN = [&](int xw, float a, std::vector<float> &out) {
            VoicedWTOscillator o(kMaxUnison);
            o.setSampleRate(sr); o.setWavetable(&wt); o.noteOn(0.0f);
            VoicedWTParams vp; vp.voices = 1; vp.xwarp = xw; vp.xamount = a; vp.xsrc = 4;
            out.clear();
            for (int blk = 0; blk < 44100 * 2 / 128; blk++) {
                float l[128] = {}, r[128] = {}, nz[128];
                for (int i = 0; i < 128; i++) nz[i] = g.next();
                const float *srcs[5] = { nz, nz, nz, nz, nz };
                o.processAdding(48.0f, vp, l, r, 128, srcs);
                if (blk * 128 > 8192) out.insert(out.end(), l, l + 128);
            }
        };
        renderN(5, 0.6f, x);
        CHECK(std::fabs(line(f0) - 0.4) < 0.05, "RM from the Noise 0.60: carrier %.3f (0.400)", line(f0));
        renderN(3, 0.40f, x);
        CHECK(std::fabs(line(f0) - 1.0) < 0.03, "FM from the Noise 0.40 does nothing (carrier %.3f)", line(f0));
        renderN(4, 0.50f, x);
        CHECK(std::fabs(line(f0) - 1.0) < 0.03, "PD from the Noise 0.50 does nothing (carrier %.3f)", line(f0));
        renderN(4, 0.80f, x);
        const double pdDb = 20.0 * std::log10(line(f0));
        CHECK(pdDb < -9.0 && pdDb > -19.0, "PD from the Noise 0.80: carrier %.1f dB (Serum -13.6)", pdDb);
        renderN(3, 0.50f, x);
        const double fmDb = 20.0 * std::log10(line(f0));
        CHECK(fmDb < -0.2 && fmDb > -9.0, "FM from the Noise 0.50: carrier %.1f dB (Serum -0.9 within 1.4 Hz; this reads one bin, so lower)", fmDb);
    }
}

static void testWarpShaper()
{
    printf("== oscillator warp shaper ==\n");
    Wavetable wt;
    buildSineTable(wt);
    const float note = 48.0f, f0 = 130.8128f;
    auto render = [&](int shape, float g, float k, std::vector<float> &out) {
        VoicedWTOscillator o(kMaxUnison);
        o.setSampleRate(44100.0); o.setWavetable(&wt); o.noteOn(0.0f);
        VoicedWTParams vp; vp.voices = 1; vp.shape = shape; vp.shapeGain = g; vp.shapeOut = k;
        out.clear();
        for (int blk = 0; blk < 44100 / 128; blk++) {
            float l[128] = {}, r[128] = {};
            o.processAdding(note, vp, l, r, 128);
            if (blk * 128 > 8192) out.insert(out.end(), l, l + 128);
        }
    };
    auto amp = [&](const std::vector<float> &x, double hz) {
        double re = 0, im = 0;
        for (size_t i = 0; i < x.size(); i++) { const double ph = 6.283185307179586 * hz * (double) i / 44100.0; const double w = 0.5 - 0.5 * std::cos(6.283185307179586 * (double) i / (double) x.size()); re += w * x[i] * std::cos(ph); im += w * x[i] * std::sin(ph); }
        return std::sqrt(re * re + im * im) * 4.0 / (double) x.size();
    };
    std::vector<float> clean, soft, ident, clip;
    render(0, 1.0f, 1.0f, clean);
    render(3, 4.0f, 1.0f, soft);
    render(3, 0.02f, 50.0f, ident);
    render(2, 100.0f, 0.5f, clip);
    const double h3clean = 20.0 * std::log10(amp(clean, 3 * f0) / amp(clean, f0) + 1e-12), h3soft = 20.0 * std::log10(amp(soft, 3 * f0) / amp(soft, f0) + 1e-12);
    CHECK(h3clean < -60.0, "no shaper: no third harmonic (%.1f dB)", h3clean);
    CHECK(h3soft > -25.0 && h3soft < -8.0, "a softsign shaper at gain 4 makes a third harmonic (%.1f dB re the fundamental)", h3soft);
    CHECK(std::fabs(20.0 * std::log10(amp(ident, f0) / amp(clean, f0))) < 0.3, "pre-gain 0.02 with output gain 50 is transparent (%.2f dB)", 20.0 * std::log10(amp(ident, f0) / amp(clean, f0)));
    /* shape 5: Serum's measured waveshaper curves (warp_curves.h). The engine's harmonics of a sine through a curve equal the curve's own, and a curve at amount 0 of a mode that is the identity there is transparent */
    {
        Wavetable wt9;                                    /* a sine peaking at 0.9, as the loader leaves a table: the curves are in file units (peak 1) and the engine rescales by 1/0.9 */
        { std::vector<float> s9(2048); for (int i = 0; i < 2048; i++) s9[(size_t) i] = 0.9f * std::sin(2.0f * (float) M_PI * (float) i / 2048.0f); wtBuild(wt9, s9.data(), 1, 2048, 44100.0f, 44100.0f); }
        auto renderCurve = [&](int mode, float amt, std::vector<float> &out) {
            VoicedWTOscillator o(kMaxUnison);
            o.setSampleRate(44100.0); o.setWavetable(&wt9); o.noteOn(0.0f);
            VoicedWTParams vp; vp.voices = 1; vp.shape = 5; vp.curveMode = mode; vp.curveAmt = amt;
            out.clear();
            for (int blk = 0; blk < 44100 / 128; blk++) {
                float l[128] = {}, r[128] = {};
                o.processAdding(note, vp, l, r, 128);
                if (blk * 128 > 8192) out.insert(out.end(), l, l + 128);
            }
        };
        auto refHarm = [&](int mode, float amt, int k) {          /* the curve's own k-th harmonic of a unit sine */
            const float pos = amt * (kWarpCurveAmts - 1); const int i0 = std::min((int) pos, kWarpCurveAmts - 2); const float fr = pos - (float) i0;
            double re = 0, im = 0; const int N = 4096;
            for (int i = 0; i < N; i++) {
                const double x = std::sin(6.283185307179586 * i / N), u = (x + 1.0) * 0.5 * (kWarpCurvePts - 1); const int j = std::min((int) u, kWarpCurvePts - 2);
                auto c = [&](int a_) { return kWarpCurveData[mode][i0][a_] * (1 - fr) + kWarpCurveData[mode][i0 + 1][a_] * fr; };
                const double y = c(j) + (c(j + 1) - c(j)) * (u - j);
                re += y * std::cos(6.283185307179586 * k * i / N); im += y * std::sin(6.283185307179586 * k * i / N);
            }
            return std::sqrt(re * re + im * im) * 2.0 / N;
        };
        std::vector<float> o;
        for (int mode : { 0, 1, 2 }) {
            renderCurve(mode, 0.7f, o);
            const double e3 = 20.0 * std::log10(amp(o, 3 * f0) / amp(o, f0)), r3 = 20.0 * std::log10(refHarm(mode, 0.7f, 3) / refHarm(mode, 0.7f, 1));
            const double e2 = 20.0 * std::log10(amp(o, 2 * f0) / amp(o, f0) + 1e-9), r2 = 20.0 * std::log10(refHarm(mode, 0.7f, 2) / refHarm(mode, 0.7f, 1) + 1e-9);
            CHECK(std::fabs(e3 - r3) < 0.7 && (std::fabs(e2 - r2) < 1.0 || (e2 < -50.0 && r2 < -50.0)), "curve %d at 0.7: 3rd %.1f dB (curve %.1f), 2nd %.1f dB (curve %.1f)", mode, e3, r3, e2, r2);
        }
        renderCurve(6, 0.0f, o);
        std::vector<float> clean9;
        { VoicedWTOscillator o9(kMaxUnison); o9.setSampleRate(44100.0); o9.setWavetable(&wt9); o9.noteOn(0.0f); VoicedWTParams vp9; vp9.voices = 1;
          for (int blk = 0; blk < 44100 / 128; blk++) { float l[128] = {}, r[128] = {}; o9.processAdding(note, vp9, l, r, 128); if (blk * 128 > 8192) clean9.insert(clean9.end(), l, l + 128); } }
        CHECK(std::fabs(20.0 * std::log10(amp(o, f0) / amp(clean9, f0))) < 0.3, "the Quantize curve at 0 is transparent (%.2f dB)", 20.0 * std::log10(amp(o, f0) / amp(clean9, f0)));
    }
    /* cost, informational: 16 unison streams, one second of audio, wall time per shaper kind (native x86, a relative figure only) */
    {
        const char *names[] = { "none", "(unused)", "clip", "softsign", "cubic" };
        for (int s = 0; s < 5; s++) {
            VoicedWTOscillator o(kMaxUnison);
            o.setSampleRate(44100.0); o.setWavetable(&wt); o.noteOn(0.0f);
            VoicedWTParams vp; vp.voices = 16; vp.detune = 0.3f; vp.shape = s; vp.shapeGain = 3.0f; vp.shapeOut = 0.4f;
            const auto t0 = std::chrono::steady_clock::now();
            float acc = 0.0f;
            for (int rep = 0; rep < 8; rep++)
                for (int blk = 0; blk < 44100 / 128; blk++) { float l[128] = {}, r[128] = {}; o.processAdding(48.0f, vp, l, r, 128); acc += l[5]; }
            const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count() / 8.0;
            printf("  cost: 16 streams, shaper %-8s %6.2f ms per second of audio (%g)\n", names[s], ms, (double) acc);
        }
    }
    float pk = 0.0f;
    for (float v : clip) pk = std::max(pk, std::fabs(v));
    CHECK(pk > 0.49f && pk < 0.6f, "a hard clip at high gain peaks at about the output gain (%.3f; the stack's 10 Hz DC blocker lifts the edges of a square by about 12 %%)", pk);
}

/* Raising the unison cap must not cost the bass.
 *
 * Every stream used to DC-block the shared buffer, so a stack was a cascade of
 * 10 Hz high-passes over everything rendered so far: -7.8 dB at 41 Hz for 16
 * streams, and -4.1 dB more from a second oscillator after it even at zero
 * gain. Coherent streams must sum to exactly sqrt(N) at any note. */
static void testUnisonKeepsTheBass()
{
    printf("== unison keeps the bass ==\n");
    Wavetable wt;
    buildSineTable(wt);
    VoicedWTOscillator a(kMaxUnison), b(kMaxUnison);
    a.setSampleRate(44100.0); b.setSampleRate(44100.0);
    a.setWavetable(&wt); b.setWavetable(&wt);

    const float e1 = 28.0f;                       /* 41 Hz */
    const double one = stackDb(a, nullptr, e1, 1);
    for (int v : { 4, 8, 16 }) {
        double loss = stackDb(a, nullptr, e1, v) - one - 10.0 * std::log10((double) v);
        CHECK(std::fabs(loss) < 0.1, "%2d streams at 41 Hz sum to sqrt(N) (%+.2f dB)", v, loss);
    }
    double hit = stackDb(a, &b, e1, 16) - stackDb(a, nullptr, e1, 16);
    CHECK(std::fabs(hit) < 0.05, "a silent stack after it does not filter it (%+.2f dB)", hit);

    /* and the blocker still does its job on what it was there for */
    std::vector<float> s(2048);
    for (int i = 0; i < 2048; i++)
        s[(size_t) i] = 0.2f + 0.5f * std::sin(2.0f * (float) M_PI * (float) i / 2048.0f);
    Wavetable dc;
    wtBuild(dc, s.data(), 1, 2048, 44100.0f, 44100.0f);
    VoicedWTOscillator c(kMaxUnison);
    c.setSampleRate(44100.0); c.setWavetable(&dc); c.noteOn(0.0f);
    VoicedWTParams vp; vp.voices = 16;
    double mean = 0.0; int count = 0;
    for (int blk = 0; blk < 44100 * 2 / 128; blk++) {
        float l[128] = {}, r[128] = {};
        c.processAdding(45.0f, vp, l, r, 128);
        if (blk * 128 > 44100)
            for (int i = 0; i < 128; i++) { mean += l[i]; count++; }
    }
    mean /= count;
    CHECK(std::fabs(mean) < 0.01, "a stack is still DC-free (mean %+.4f)", mean);

    /* longer than a block, as any caller may ask */
    float big[1000] = {}, bigR[1000] = {};
    c.processAdding(45.0f, vp, big, bigR, 1000);
    float pk = 0.0f;
    for (float v : big) pk = std::max(pk, std::fabs(v));
    CHECK(pk > 0.1f && big[999] != 0.0f, "a long request renders to its last sample");
}

/* Energy away from the harmonics of f0, in dB against the total. */
static double inharmonicDb(const std::vector<float> &x, float f0)
{
    const int N = (int) x.size();
    std::vector<double> re(N), im(N, 0.0);
    for (int i = 0; i < N; i++)
        re[i] = x[i] * (0.5 - 0.5 * std::cos(2.0 * M_PI * i / N));
    for (int i = 1, j = 0; i < N; i++) {
        int bit = N >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) { std::swap(re[i], re[j]); std::swap(im[i], im[j]); }
    }
    for (int len = 2; len <= N; len <<= 1) {
        const double ang = -2.0 * M_PI / len;
        for (int i = 0; i < N; i += len)
            for (int k = 0; k < len / 2; k++) {
                const double wr = std::cos(ang * k), wi = std::sin(ang * k);
                const double ur = re[i + k], ui = im[i + k];
                const double vr = re[i + k + len / 2] * wr - im[i + k + len / 2] * wi;
                const double vi = re[i + k + len / 2] * wi + im[i + k + len / 2] * wr;
                re[i + k] = ur + vr; im[i + k] = ui + vi;
                re[i + k + len / 2] = ur - vr; im[i + k + len / 2] = ui - vi;
            }
    }
    const double binHz = 44100.0 / N;
    double total = 0.0, off = 0.0;
    for (int b = 1; b < N / 2; b++) {
        const double e = re[b] * re[b] + im[b] * im[b];
        const double hz = b * binHz;
        const double h = std::round(hz / f0);
        total += e;
        if (std::fabs(hz - h * f0) > 5.0 * binHz) off += e;
    }
    return 10.0 * std::log10(off / total + 1e-20);
}

/* A new preset replaces the engine's sample set while the old preset's notes still ring. A voice must keep the set it plays from alive
 * (the player's region pointer is into it), or the next block reads freed memory: the crash that took the Move down playing Factory presets. */
static void testSampleSetOutlivesItsEngineShare()
{
    printf("\n== sample set lifetime ==\n");
    auto set = std::make_shared<tb::SampleSet>();
    tb::SampleRegion r;
    r.frames = 4410; r.pcm.assign(4410, 1000);
    set->regions.push_back(std::move(r));
    std::weak_ptr<tb::SampleSet> weak = set;
    tb::SamplePlayer p;
    p.start(set, 60);
    set.reset();                                   /* the engine loads another preset and drops its share */
    CHECK(!weak.expired(), "a voice still playing from a sample set keeps it alive");
    float l[64] = {}, rr[64] = {};
    p.processAdding(0.0f, 1.0f, 44100.0f, l, rr, 64);
    CHECK(l[63] > 0.0f, "and it still plays from it (%.4f)", l[63]);
    p.release();
    CHECK(weak.expired(), "the set goes when the voice lets go");
    auto set2 = std::make_shared<tb::SampleSet>();
    tb::SampleRegion r2;
    r2.frames = 8; r2.pcm.assign(8, 1000);
    set2->regions.push_back(std::move(r2));
    std::weak_ptr<tb::SampleSet> weak2 = set2;
    p.start(set2, 60);
    set2.reset();
    for (int i = 0; i < 4 && p.on; i++) p.processAdding(0.0f, 1.0f, 44100.0f, l, rr, 64);
    CHECK(!p.on && weak2.expired(), "a one-shot that reaches its end lets the set go");
}

/* A multisample holds a key several times at different dynamics (SFZ lovel/hivel). The engine used to read only the key range and always played the first region of a key:
 * the softest recording, at any velocity (Baby Grand Piano: 4 layers, the first 13 dB under the loudest). */
static void testSampleVelocityLayers()
{
    printf("\n== sample velocity layers ==\n");
    tb::SampleSet set;
    const int vl[4][2] = { {0, 41}, {42, 69}, {70, 100}, {101, 127} };
    for (int i = 0; i < 4; i++) {
        tb::SampleRegion r;
        r.lo = 48; r.hi = 50; r.root = 48; r.vlo = vl[i][0]; r.vhi = vl[i][1]; r.gain = 1.0f + (float) i;
        r.frames = 8; r.pcm.assign(8, 1000);
        set.regions.push_back(std::move(r));
    }
    tb::SampleRegion other;
    other.lo = 51; other.hi = 53; other.root = 51; other.frames = 8; other.pcm.assign(8, 1000);
    set.regions.push_back(std::move(other));
    CHECK(set.pick(49, 20)->vlo == 0 && set.pick(49, 41)->vlo == 0, "velocity 20 and 41 play the softest layer");
    CHECK(set.pick(49, 42)->vlo == 42 && set.pick(49, 69)->vlo == 42, "42 and 69 the second");
    CHECK(set.pick(49, 100)->vlo == 70, "100 the third");
    CHECK(set.pick(49, 127)->vlo == 101, "127 the loudest");
    CHECK(set.pick(52, 127)->root == 51, "a key with one region plays it at any velocity");
    CHECK(set.pick(60, 127)->root == 51, "a key outside every range falls to the nearest key");
    tb::SampleSet gap;
    tb::SampleRegion g; g.lo = 0; g.hi = 127; g.vlo = 0; g.vhi = 60; g.frames = 8; g.pcm.assign(8, 1);
    gap.regions.push_back(g);
    g.vlo = 90; g.vhi = 127; gap.regions.push_back(g);
    CHECK(gap.pick(60, 80)->vlo == 90 || gap.pick(60, 80)->vlo == 0, "a velocity in no layer takes a layer, not nothing");
    CHECK(gap.pick(60, 70)->vlo == 0 && gap.pick(60, 85)->vlo == 90, "the nearer layer");
}

static void testHardSync()
{
    printf("== hard sync ==\n");
    Wavetable wt;
    buildSineTable(wt);
    WTOscillator::Params p;

    /* Off means off: the path is not entered, so nothing can have changed. */
    {
        WTOscillator a, b;
        a.setSampleRate(44100.0); b.setSampleRate(44100.0);
        a.setWavetable(&wt); b.setWavetable(&wt);
        a.noteOn(0.25f); b.noteOn(0.25f);
        WTOscillator::Params off = p; off.sync = 0.0f;
        std::vector<float> la(2048, 0.0f), ra(2048, 0.0f), lb(2048, 0.0f), rb(2048, 0.0f);
        a.processAdding(57.0f, p, la.data(), ra.data(), 2048);
        b.processAdding(57.0f, off, lb.data(), rb.data(), 2048);
        CHECK(!std::memcmp(la.data(), lb.data(), 2048 * sizeof(float)),
              "sync 0 is bit-identical to no sync");
    }

    /* The pitch stays the master's: 220.5 Hz is a period of exactly 200. */
    const float note = 69.0f + 12.0f * std::log2(220.5f / 440.0f);
    {
        WTOscillator o;
        o.setSampleRate(44100.0); o.setWavetable(&wt); o.noteOn(0.0f);
        WTOscillator::Params s = p; s.sync = 0.5f;
        std::vector<float> l(8192, 0.0f), r(8192, 0.0f);
        o.processAdding(note, s, l.data(), r.data(), 8192);
        double diff = 0.0, sig = 0.0;
        for (int i = 2000; i < 7900; i++) {
            diff += (double) (l[(size_t) i] - l[(size_t) i + 200]) * (l[(size_t) i] - l[(size_t) i + 200]);
            sig  += (double) l[(size_t) i] * l[(size_t) i];
        }
        double ratio = std::sqrt(diff / sig);
        CHECK(ratio < 0.02, "sync repeats at the master's period (residual %.4f)", ratio);

        /* and it really is doing something: a synced sine is not a sine */
        double zero = 0.0;
        for (int i = 2000; i < 8000; i++) {
            const double want = std::sin(2.0 * M_PI * 220.5 * i / 44100.0);
            zero += (l[(size_t) i] - want) * (l[(size_t) i] - want);
        }
        CHECK(std::sqrt(zero / 6000.0) > 0.2, "sync changes the waveform");
    }

    /* The polyBLEP has to earn its keep where sync is reached for: compare with
     * the same oscillator's restart left as a raw step. 1234.5 Hz keeps the
     * partials off integer periods, so what aliases lands between harmonics. */
    {
        const float hz = 1234.5f;
        const float n = 69.0f + 12.0f * std::log2(hz / 440.0f);
        const int N = 32768;
        const float sync = 0.7f;
        WTOscillator o;
        o.setSampleRate(44100.0); o.setWavetable(&wt); o.noteOn(0.0f);
        WTOscillator::Params s = p; s.sync = sync;
        std::vector<float> l(N, 0.0f), r(N, 0.0f);
        o.processAdding(n, s, l.data(), r.data(), N);

        std::vector<float> raw(N);
        const float sNote = n + sync * kSyncRangeSemis;
        const float sHz = midiNoteToHz(sNote);
        float master = 0.0f, slave = 0.0f;
        for (int i = 0; i < N; i++) {
            master += hz / 44100.0f;
            slave += sHz / 44100.0f;
            if (master >= 1.0f) {
                master -= 1.0f;
                slave = master * sHz / hz;
            }
            if (slave >= 1.0f) slave -= 1.0f;
            raw[(size_t) i] = wt.frame(0)->processLinear(sNote, std::min(0.99999994f, slave));
        }
        double blep = inharmonicDb(l, hz), naive = inharmonicDb(raw, hz);
        CHECK(naive - blep > 6.0, "polyBLEP cuts aliasing (%.1f dB vs %.1f dB raw)", blep, naive);
    }

    /* Swept all the way up, down to zero and up again: finite, bounded, and
     * with no jump bigger than the waveform itself makes. */
    {
        WTOscillator o;
        o.setSampleRate(44100.0); o.setWavetable(&wt); o.noteOn(0.0f);
        bool finite = true;
        float pk = 0.0f;
        for (int b = 0; b < 2000; b++) {
            WTOscillator::Params s = p;
            s.sync = 0.5f + 0.5f * std::sin((float) b * 0.01f);
            float l[128] = {}, r[128] = {};
            o.processAdding(45.0f, s, l, r, 128);
            for (float v : l) { finite &= std::isfinite(v); pk = std::max(pk, std::fabs(v)); }
        }
        CHECK(finite && pk < 1.6f, "a swept amount stays finite and bounded (peak %.2f)", pk);
    }

    /* A new note starts clean: the next note at amount 0 is plain again. */
    {
        WTOscillator a, b;
        a.setSampleRate(44100.0); b.setSampleRate(44100.0);
        a.setWavetable(&wt); b.setWavetable(&wt);
        WTOscillator::Params s = p; s.sync = 0.9f;
        float tl[128] = {}, tr[128] = {};
        a.noteOn(0.0f);
        a.processAdding(57.0f, s, tl, tr, 128);
        a.noteOn(0.25f); b.noteOn(0.25f);
        std::vector<float> la(512, 0.0f), ra(512, 0.0f), lb(512, 0.0f), rb(512, 0.0f);
        a.processAdding(57.0f, p, la.data(), ra.data(), 512);
        b.processAdding(57.0f, p, lb.data(), rb.data(), 512);
        CHECK(!std::memcmp(la.data(), lb.data(), 512 * sizeof(float)),
              "noteOn disengages sync");
    }
}

static void testPresetDigest()
{
    printf("\n== preset digest (the web panel's browser list) ==\n");
    const std::string d = tb::presetDigest({ "Factory", "Deep \"House\"", "User" },
                                           { { "BA - Foo", "CH - Bar" }, { "a\\b", "x\ty" }, {} });
    const std::string want = "{\"b\":[{\"n\":\"Factory\",\"p\":[\"BA - Foo\",\"CH - Bar\"]},"
                             "{\"n\":\"Deep \\\"House\\\"\",\"p\":[\"a\\\\b\",\"xy\"]},"
                             "{\"n\":\"User\",\"p\":[]}]}";
    CHECK(d == want, "digest is the expected JSON (quotes and backslashes escaped, control characters dropped): %s", d.c_str());
    CHECK(tb::presetDigest({}, {}) == "{\"b\":[]}", "an empty store is an empty list");
}

int main()
{
    testFilter();
    testADSR();
    testShortWavetables();
    testShortFrameTables();
    testWavFormats();
    testWavNormPeak();
    testCombFilter();
    testMorphToEvenHarmonicsKeepsItsFrame();
    testAnalogTables();
    testUnisonKeepsTheBass();
    testWarpShaper();
    testXwarp();
    testHardSync();
    testSampleSetOutlivesItsEngineShare();
    testSampleVelocityLayers();
    testPresetDigest();
    printf("\n%s (%d failures)\n", g_fail ? "DSP TESTS FAILED" : "DSP TESTS PASSED", g_fail);
    return g_fail ? 1 : 0;
}
