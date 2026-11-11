/* The sample oscillator: Serum's Sample and Multisample oscillators, as one set of key-mapped regions (see sample_export.py for the file).
 *
 * A region plays at its root key's pitch and is re-pitched by playing the sample faster or slower (linear interpolation); a region that
 * loops wraps between its loop points, one that does not stops at its end. The set is loaded off the audio thread and handed over as a
 * shared pointer, like a wavetable. */
#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>
#include <vector>

namespace tb {

struct SampleRegion {
    int   lo = 0, hi = 127, root = 60;
    int   vlo = 0, vhi = 127;                   /* the velocity layer: a multisample holds several recordings of a key at different dynamics */
    float gain = 1.0f;                          /* the region's own volume (SFZ volume, as a factor) */
    bool  loops = false;
    bool  pingPong = false;                     /* a loop that is played forward, then backward, between its points (flags bit 1) */
    float loopStart = 0.0f, loopEnd = 0.0f;     /* fractions of the region's length */
    float loopXfade = 0.0f;                     /* the loop crossfade: a fraction of the loop's length, played before each turn (TBS3) */
    float rate = 44100.0f;
    float tuneCents = 0.0f;
    int   channels = 1;
    int   frames = 0;
    std::vector<int16_t> pcm;                   /* interleaved */
};

struct SampleSet {
    std::vector<SampleRegion> regions;

    /* The region for a note played at a velocity (1..127): the one whose key range and velocity layer hold them; failing that the nearest key, then the nearest layer. The first
     * of equals wins. Before the layers were read every note played the first layer of its key: the softest recording, at any velocity. */
    const SampleRegion *pick(int note, int vel = 100) const
    {
        const SampleRegion *best = nullptr;
        int bestN = 1 << 20, bestV = 1 << 20;
        for (const auto &r : regions) {
            const int dn = note < r.lo ? r.lo - note : (note > r.hi ? note - r.hi : 0);
            const int dv = vel < r.vlo ? r.vlo - vel : (vel > r.vhi ? vel - r.vhi : 0);
            if (dn < bestN || (dn == bestN && dv < bestV)) { bestN = dn; bestV = dv; best = &r; }
            if (dn == 0 && dv == 0) break;
        }
        return best;
    }
};

inline bool sampleLoad(const char *path, SampleSet &out)
{
    FILE *f = std::fopen(path, "rb");
    if (!f) return false;
    char magic[4];
    uint32_t n = 0;
    /* TBS1: key range only. TBS2: also the velocity layer and a volume (dB) per region. TBS3: also the loop crossfade (sample_export.py). */
    bool ok = std::fread(magic, 4, 1, f) == 1 && (!std::memcmp(magic, "TBS1", 4) || !std::memcmp(magic, "TBS2", 4) || !std::memcmp(magic, "TBS3", 4)) && std::fread(&n, 4, 1, f) == 1 && n > 0 && n < 512;
    const bool v2 = ok && magic[3] >= '2', v3 = ok && magic[3] == '3';
    out.regions.clear();
    for (uint32_t i = 0; ok && i < n; i++) {
        uint8_t h[6] = { 0, 0, 0, 0, 0, 127 };
        float lf[4], volDb = 0.0f, xf = 0.0f;
        uint32_t frames = 0;
        uint8_t ch = 1;
        SampleRegion r;
        ok = std::fread(h, v2 ? 6 : 4, 1, f) == 1 && std::fread(lf, 16, 1, f) == 1 && (!v2 || std::fread(&volDb, 4, 1, f) == 1) && (!v3 || std::fread(&xf, 4, 1, f) == 1) && std::fread(&frames, 4, 1, f) == 1 && std::fread(&ch, 1, 1, f) == 1 &&
             (ch == 1 || ch == 2) && frames > 0 && frames < (1u << 24);
        if (!ok) break;
        r.lo = h[0]; r.hi = h[1]; r.root = h[2]; r.loops = (h[3] & 1) != 0; r.pingPong = (h[3] & 2) != 0; r.vlo = h[4]; r.vhi = h[5];
        r.gain = std::pow(10.0f, volDb / 20.0f);
        r.loopStart = lf[0]; r.loopEnd = lf[1]; r.loopXfade = xf >= 0.0f && xf <= 1.0f ? xf : 0.0f; r.rate = lf[2]; r.tuneCents = lf[3];
        r.channels = ch; r.frames = (int) frames;
        r.pcm.resize((size_t) frames * ch);
        ok = std::fread(r.pcm.data(), 2, r.pcm.size(), f) == r.pcm.size();
        if (ok) out.regions.push_back(std::move(r));
    }
    std::fclose(f);
    if (!ok) out.regions.clear();
    return ok;
}

/* One voice's playback of a region. */
struct SamplePlayer {
    /* The voice owns a share of its set for as long as it plays from it. `r` points into the set, and a new preset replaces the engine's
     * set while the old preset's notes still ring (held keys, release tails): without this share the set was freed under them and the
     * next block read freed memory. The last share goes when the voice ends or starts another note. */
    std::shared_ptr<const SampleSet> own;
    const SampleRegion *r = nullptr;
    double pos = 0.0;                           /* in frames */
    float  fade = 0.0f;                         /* attack ramp 0..1 */
    int    dir = 1;                             /* +1 forward, -1 backward (ping-pong loops) */
    bool   on = false;

    void start(const std::shared_ptr<const SampleSet> &set, int note, int vel = 100)
    {
        own = set;
        r = set ? set->pick(note, vel) : nullptr;
        pos = 0.0; fade = 0.0f; dir = 1;
        on = r != nullptr;
    }

    void release() { on = false; r = nullptr; own.reset(); }

    /* Adds n samples to l/r. semis: the pitch offset from the region's root key (note - root, plus the oscillator's own tuning and bend). */
    void processAdding(float semis, float gain, float sr, float *l, float *rt, int n)
    {
        if (!on || !r) return;
        const double step = std::pow(2.0, (double) (semis + r->tuneCents / 100.0f) / 12.0) * (double) r->rate / (double) sr;
        const int fr = r->frames, ch = r->channels;
        const double ls = r->loops ? (double) r->loopStart * fr : 0.0, le = r->loops ? (double) r->loopEnd * fr : (double) fr;
        const float fadeStep = 1.0f / (0.010f * sr);
        const float k = gain * r->gain * (1.0f / 32768.0f);
        const bool pingPong = r->loops && r->pingPong && le > ls + 2.0;
        /* the crossfade is as long as the loop times the setting, and no longer than the audio that lies beyond the turn it fades into */
        const double xfLen = r->loops ? std::min((le - ls) * (double) r->loopXfade, pingPong ? std::min(ls, (double) fr - le) : ls) : 0.0;
        auto read = [&](double p, float &a, float &b) {
            const int i0 = (int) p;
            const float fr1 = (float) (p - i0);
            const int i1 = std::min(i0 + 1, fr - 1);
            if (ch == 1) {
                const float x0 = r->pcm[(size_t) i0], x1 = r->pcm[(size_t) i1];
                a = b = x0 + (x1 - x0) * fr1;
            } else {
                const float l0 = r->pcm[(size_t) i0 * 2], l1 = r->pcm[(size_t) i1 * 2];
                const float r0 = r->pcm[(size_t) i0 * 2 + 1], r1 = r->pcm[(size_t) i1 * 2 + 1];
                a = l0 + (l1 - l0) * fr1;
                b = r0 + (r1 - r0) * fr1;
            }
        };
        for (int i = 0; i < n; i++) {
            if (pingPong) {                         /* played to the loop end, then back to its start, and so on: the position is reflected at each point */
                if (dir > 0 && pos >= le) { pos = std::max(2.0 * le - pos, ls); dir = -1; pos = std::min(pos, le - 1e-3); }
                else if (dir < 0 && pos <= ls) { pos = std::min(2.0 * ls - pos, le - 1e-3); dir = 1; }
            } else if (pos >= le) {
                if (r->loops && le > ls + 1.0) pos = ls + std::fmod(pos - ls, le - ls);
                else { release(); return; }
            }
            float a, b;
            read(pos, a, b);
            if (xfLen > 0.0 && r->loops) {          /* the loop crossfade: before each turn the audio fades into what plays after it, extended back in time */
                const double d = (pingPong && dir < 0) ? pos - ls : le - pos;       /* the distance to the turn */
                if (d < xfLen) {
                    const double pp = !pingPong ? pos - (le - ls) : (dir > 0 ? 2.0 * le - pos : 2.0 * ls - pos);
                    const float w = std::pow((float) (1.0 - d / xfLen), 0.8f);    /* measured on Serum (calibration/probe_loop_mode.py): a weight rising a little faster than linear */
                    float a2, b2;
                    read(std::min(std::max(pp, 0.0), (double) fr - 1.001), a2, b2);
                    a += (a2 - a) * w; b += (b2 - b) * w;
                }
            }
            fade = std::min(1.0f, fade + fadeStep);
            l[i] += a * k * fade;
            rt[i] += b * k * fade;
            pos += dir * step;
        }
    }
};

} /* namespace tb */
