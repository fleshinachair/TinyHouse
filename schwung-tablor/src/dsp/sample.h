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
    bool  loops = false;
    float loopStart = 0.0f, loopEnd = 0.0f;     /* fractions of the region's length */
    float rate = 44100.0f;
    float tuneCents = 0.0f;
    int   channels = 1;
    int   frames = 0;
    std::vector<int16_t> pcm;                   /* interleaved */
};

struct SampleSet {
    std::vector<SampleRegion> regions;

    /* The region for a note: the one whose key range holds it, else the nearest root. */
    const SampleRegion *pick(int note) const
    {
        const SampleRegion *best = nullptr;
        int bestD = 1 << 20;
        for (const auto &r : regions) {
            if (note >= r.lo && note <= r.hi) return &r;
            const int d = std::min(std::abs(note - r.lo), std::abs(note - r.hi));
            if (d < bestD) { bestD = d; best = &r; }
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
    bool ok = std::fread(magic, 4, 1, f) == 1 && !std::memcmp(magic, "TBS1", 4) && std::fread(&n, 4, 1, f) == 1 && n > 0 && n < 512;
    out.regions.clear();
    for (uint32_t i = 0; ok && i < n; i++) {
        uint8_t h[4];
        float lf[4];
        uint32_t frames = 0;
        uint8_t ch = 1;
        SampleRegion r;
        ok = std::fread(h, 4, 1, f) == 1 && std::fread(lf, 16, 1, f) == 1 && std::fread(&frames, 4, 1, f) == 1 && std::fread(&ch, 1, 1, f) == 1 &&
             (ch == 1 || ch == 2) && frames > 0 && frames < (1u << 24);
        if (!ok) break;
        r.lo = h[0]; r.hi = h[1]; r.root = h[2]; r.loops = h[3] != 0;
        r.loopStart = lf[0]; r.loopEnd = lf[1]; r.rate = lf[2]; r.tuneCents = lf[3];
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
    bool   on = false;

    void start(const std::shared_ptr<const SampleSet> &set, int note)
    {
        own = set;
        r = set ? set->pick(note) : nullptr;
        pos = 0.0; fade = 0.0f;
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
        const float k = gain * (1.0f / 32768.0f);
        for (int i = 0; i < n; i++) {
            if (pos >= le) {
                if (r->loops && le > ls + 1.0) pos = ls + std::fmod(pos - ls, le - ls);
                else { release(); return; }
            }
            const int i0 = (int) pos;
            const float fr1 = (float) (pos - i0);
            const int i1 = std::min(i0 + 1, fr - 1);
            float a, b;
            if (ch == 1) {
                const float x0 = r->pcm[(size_t) i0], x1 = r->pcm[(size_t) i1];
                a = b = x0 + (x1 - x0) * fr1;
            } else {
                const float l0 = r->pcm[(size_t) i0 * 2], l1 = r->pcm[(size_t) i1 * 2];
                const float r0 = r->pcm[(size_t) i0 * 2 + 1], r1 = r->pcm[(size_t) i1 * 2 + 1];
                a = l0 + (l1 - l0) * fr1;
                b = r0 + (r1 - r0) * fr1;
            }
            fade = std::min(1.0f, fade + fadeStep);
            l[i] += a * k * fade;
            rt[i] += b * k * fade;
            pos += step;
        }
    }
};

} /* namespace tb */
