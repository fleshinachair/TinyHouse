/* The convolution reverb: Serum's Conv FX, as a partitioned FFT convolver run on the master bus.
 *
 * Why it exists. A long impulse response (a hall, a plate, a cab) is a time-domain effect: a static EQ cannot carry it, and a feedback reverb is not the same sound. The converter
 * (fxmap.py) renders Serum's Conv law into an impulse response file (the Size stretch, the Decay envelope and the level baked in) in the sample-set format (TBS), and this plays it.
 *
 * How. Two stages, both uniform-partition overlap-save convolution with the left and right channels packed into one complex FFT:
 *   head  taps [0, 4096):     blocks of 128 samples (FFT 256), 32 partitions, every engine block;
 *   tail  taps [4096, end):   blocks of 2048 samples (FFT 4096). A tail block's output is needed 2 blocks after its input ends, so its partition products are spread
 *                             over the 16 engine blocks of the interval, which keeps the per-block cost flat (no spike every 2048 samples).
 * The wet signal is 128 samples late (the engine block): the tail of a reverb does not care.
 *
 * Realtime: everything the audio thread touches (the spectra, the delay lines, the accumulators) is allocated by convBuild() on the loader thread and handed over with the plan.
 * Cost: about 16 flops x IR length x 44100 / 2048 for the tail (a 2 s stereo IR: roughly 30 Mflop/s) plus 20 Mflop/s for the head.
 */
#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <memory>
#include <vector>

#include "sample.h"

namespace tb {

namespace convdetail {
inline constexpr int kB = 128, kBN = 256, kBB = kBN / 2 + 1;               /* head block, its FFT, its bins */
inline constexpr int kHeadParts = 32;                                       /* 32 x 128 = 4096 taps */
inline constexpr int kD = 2048, kDN = 4096, kDB = kDN / 2 + 1;              /* tail block, its FFT, its bins */
inline constexpr int kHeadTaps = kB * kHeadParts;
inline constexpr int kSlices = kD / kB;                                     /* 16 engine blocks per tail block */
inline constexpr int kMaxTailParts = 140;                                   /* 140 x 2048 + 4096 taps = 6.6 s */

struct Fft {
    int n = 0;
    std::vector<float> cs, sn;
    std::vector<float> twr, twi;                                           /* cs / sn by stage, see init */
    std::vector<int> rev;

    void init(int N)
    {
        n = N;
        cs.resize((size_t) N / 2); sn.resize((size_t) N / 2); rev.resize((size_t) N);
        for (int k = 0; k < N / 2; k++) {
            cs[(size_t) k] = (float) std::cos(6.283185307179586 * k / N);
            sn[(size_t) k] = (float) -std::sin(6.283185307179586 * k / N);
        }
        /* the same twiddles laid out stage by stage (a stage of half-size h at [h - 1, 2h - 1)), so a stage reads them in order rather than at a stride */
        twr.assign((size_t) N, 0.0f); twi.assign((size_t) N, 0.0f);
        for (int half = 1; half < N; half <<= 1)
            for (int j = 0; j < half; j++) {
                twr[(size_t) (half - 1 + j)] = cs[(size_t) (j * (N / (2 * half)))];
                twi[(size_t) (half - 1 + j)] = sn[(size_t) (j * (N / (2 * half)))];
            }
        int lg = 0;
        while ((1 << lg) < N) lg++;
        for (int i = 0; i < N; i++) {
            int r = 0;
            for (int b = 0; b < lg; b++) if (i & (1 << b)) r |= 1 << (lg - 1 - b);
            rev[(size_t) i] = r;
        }
    }

    /* In place, split real and imaginary arrays; unnormalised in both directions. */
    void run(float *re, float *im, bool inverse) const
    {
        for (int i = 0; i < n; i++) {
            const int j = rev[(size_t) i];
            if (i < j) { std::swap(re[i], re[j]); std::swap(im[i], im[j]); }
        }
        const float sg = inverse ? -1.0f : 1.0f;                            /* the table holds the forward sign; the inverse conjugates it */
        /* Each butterfly of a stage is independent of the others, so the order they run in does not change a bit of the result: the short stages run twiddle by
         * twiddle across the groups, the long ones group by group along contiguous twiddles (which the compiler can vectorise). */
        for (int len = 2; len <= n; len <<= 1) {
            const int half = len >> 1;
            const float *twR = &twr[(size_t) (half - 1)], *twI = &twi[(size_t) (half - 1)];
            if (half < 4) {
                for (int j = 0; j < half; j++) {
                    const float wr = twR[j], wi = sg * twI[j];
                    for (int a = j; a < n; a += len) {
                        const int b = a + half;
                        const float tr = re[b] * wr - im[b] * wi, ti = re[b] * wi + im[b] * wr;
                        re[b] = re[a] - tr; im[b] = im[a] - ti;
                        re[a] += tr; im[a] += ti;
                    }
                }
                continue;
            }
            for (int i = 0; i < n; i += len) {
                float *__restrict ra = re + i, *__restrict ia = im + i, *__restrict rb = re + i + half, *__restrict ib = im + i + half;
                for (int j = 0; j < half; j++) {
                    const float wr = twR[j], wi = sg * twI[j];
                    const float tr = rb[j] * wr - ib[j] * wi, ti = rb[j] * wi + ib[j] * wr;
                    rb[j] = ra[j] - tr; ib[j] = ia[j] - ti;
                    ra[j] += tr; ia[j] += ti;
                }
            }
        }
    }
};

/* Splits the spectrum Z of (a + i b), a and b real, into the half spectra of a and b (bins 0 .. n/2). */
inline void unpack(const float *zr, const float *zi, int n, float *ar, float *ai, float *br, float *bi)
{
    for (int k = 0; k <= n / 2; k++) {
        const int m = (n - k) & (n - 1);
        ar[k] = 0.5f * (zr[k] + zr[m]);  ai[k] = 0.5f * (zi[k] - zi[m]);
        br[k] = 0.5f * (zi[k] + zi[m]);  bi[k] = -0.5f * (zr[k] - zr[m]);
    }
}

/* The spectrum of (a + i b) for real a and b from their half spectra: W = A + iB, with the upper half filled by symmetry. */
inline void pack(const float *ar, const float *ai, const float *br, const float *bi, int n, float *wr, float *wi)
{
    for (int k = 0; k <= n / 2; k++) { wr[k] = ar[k] - bi[k]; wi[k] = ai[k] + br[k]; }
    for (int k = n / 2 + 1; k < n; k++) { const int m = n - k; wr[k] = ar[m] + bi[m]; wi[k] = -ai[m] + br[m]; }
}
} /* namespace convdetail */

/* One IR (the plan) and the working state of the convolver that plays it. Built on the loader thread, used by one audio thread afterwards. */
struct ConvPlan {
    int channels = 1, tailParts = 0;
    /* IR spectra: [partition][re bins then im bins], per channel */
    std::vector<float> head[2], tail[2];

    /* the state */
    convdetail::Fft fb, fd;
    std::vector<float> fdlHead, fdlTail;                  /* input spectra: [slot][xlRe, xlIm, xrRe, xrIm] */
    std::vector<float> accRe[2], accIm[2];                 /* the tail job's sums, left and right */
    std::vector<float> tin;                                /* tail input: [previous block (2048) | current block (2048)] x {left, right} */
    std::vector<float> wr, wi, ar, ai, br, bi;             /* scratch */
    float inB[2][convdetail::kB] = {};
    float prevB[2][convdetail::kB] = {};
    float outB[2][2][convdetail::kB] = {};                 /* [read side][channel][sample] */
    float tailOut[2][2][convdetail::kD] = {};              /* [buffer][channel][sample] */
    int fill = 0, rd = 0, headPos = 0, tailPos = 0, sub = 0, curOut = 0;
    bool job = false;
    long long blocks = 0;

    void reset()
    {
        std::fill(fdlHead.begin(), fdlHead.end(), 0.0f); std::fill(fdlTail.begin(), fdlTail.end(), 0.0f);
        std::fill(tin.begin(), tin.end(), 0.0f);
        std::memset(inB, 0, sizeof inB); std::memset(prevB, 0, sizeof prevB); std::memset(outB, 0, sizeof outB); std::memset(tailOut, 0, sizeof tailOut);
        fill = rd = headPos = tailPos = sub = curOut = 0; job = false; blocks = 0;
    }

    /* One sample in, the convolved sample out (128 samples late). */
    inline void step(float l, float r, float &wl, float &wrr)
    {
        wl = outB[rd][0][fill]; wrr = outB[rd][1][fill];
        inB[0][fill] = l; inB[1][fill] = r;
        if (++fill == convdetail::kB) { block(); fill = 0; rd ^= 1; }
    }

private:
    void block()
    {
        using namespace convdetail;
        const int nb = kBB;
        /* ---- head: this block's spectrum into the delay line, then the sum over the 32 partitions */
        float *re = wr.data(), *im = wi.data();
        std::memcpy(re, prevB[0], sizeof(float) * kB); std::memcpy(re + kB, inB[0], sizeof(float) * kB);
        std::memcpy(im, prevB[1], sizeof(float) * kB); std::memcpy(im + kB, inB[1], sizeof(float) * kB);
        std::memcpy(prevB[0], inB[0], sizeof(float) * kB); std::memcpy(prevB[1], inB[1], sizeof(float) * kB);
        fb.run(re, im, false);
        float *slot = &fdlHead[(size_t) headPos * 4 * nb];
        unpack(re, im, kBN, slot, slot + nb, slot + 2 * nb, slot + 3 * nb);
        float *yr[2] = { ar.data(), br.data() }, *yi[2] = { ai.data(), bi.data() };
        for (int c = 0; c < 2; c++) { std::fill(yr[c], yr[c] + nb, 0.0f); std::fill(yi[c], yi[c] + nb, 0.0f); }
        for (int p = 0; p < kHeadParts; p++) {
            const float *x = &fdlHead[(size_t) ((headPos - p + kHeadParts) % kHeadParts) * 4 * nb];
            for (int c = 0; c < 2; c++) {
                const int hc = channels == 1 ? 0 : c;
                const float *hr = &head[hc][(size_t) p * 2 * nb], *hi = hr + nb;
                const float *xr = x + (c ? 2 * nb : 0), *xi = xr + nb;
                float *ur = yr[c], *ui = yi[c];
                for (int k = 0; k < nb; k++) { ur[k] += hr[k] * xr[k] - hi[k] * xi[k]; ui[k] += hr[k] * xi[k] + hi[k] * xr[k]; }
            }
        }
        headPos = (headPos + 1) % kHeadParts;
        pack(yr[0], yi[0], yr[1], yi[1], kBN, re, im);
        fb.run(re, im, true);
        const float sc = 1.0f / (float) kBN;
        float *o = outB[rd ^ 1][0], *o2 = outB[rd ^ 1][1];
        /* the tail's contribution for this block comes from the buffer that finished one interval ago */
        const float *t0 = tailOut[curOut][0] + sub * kB, *t1 = tailOut[curOut][1] + sub * kB;
        for (int i = 0; i < kB; i++) { o[i] = re[kB + i] * sc + t0[i]; o2[i] = im[kB + i] * sc + t1[i]; }

        /* ---- tail: a block of 2048 samples is one interval of 16 engine blocks. At its first block the spectrum of the block that has just ended goes into the delay line and a job starts
         * (partition products over blocks 1..14, the inverse FFT in block 15), so the two 4096-point FFTs never land in the same block and the work stays level. */
        if (tailParts > 0) {
            if (sub == 0) tailStart();
            std::memcpy(&tin[(size_t) kD + (size_t) sub * kB], inB[0], sizeof(float) * kB);                      /* left, current block */
            std::memcpy(&tin[(size_t) kDN + (size_t) kD + (size_t) sub * kB], inB[1], sizeof(float) * kB);        /* right */
            if (job && sub >= 1 && sub <= kSlices - 2) tailSlice(sub - 1);
            if (job && sub == kSlices - 1) tailEnd();
        }
        if (++sub == kSlices) { sub = 0; curOut ^= 1; }
    }

    /* The input of the tail block that has just ended is in tin[ch][kD .. 2 kD); [0 .. kD) holds the block before it. */
    void tailStart()
    {
        using namespace convdetail;
        const int nb = kDB;
        float *re = wr.data(), *im = wi.data();
        std::memcpy(re, &tin[0], sizeof(float) * kDN);
        std::memcpy(im, &tin[(size_t) kDN], sizeof(float) * kDN);
        std::memmove(&tin[0], &tin[(size_t) kD], sizeof(float) * kD);                   /* the finished block becomes the previous one */
        std::memmove(&tin[(size_t) kDN], &tin[(size_t) kDN + kD], sizeof(float) * kD);
        fd.run(re, im, false);
        tailPos = (tailPos + 1) % tailParts;
        float *slot = &fdlTail[(size_t) tailPos * 4 * nb];
        unpack(re, im, kDN, slot, slot + nb, slot + 2 * nb, slot + 3 * nb);
        for (int c = 0; c < 2; c++) { std::fill(accRe[c].begin(), accRe[c].end(), 0.0f); std::fill(accIm[c].begin(), accIm[c].end(), 0.0f); }
        job = true;
    }

    /* The job's sums are done: its output is the next interval's tail. */
    void tailEnd()
    {
        using namespace convdetail;
        float *re = wr.data(), *im = wi.data();
        pack(accRe[0].data(), accIm[0].data(), accRe[1].data(), accIm[1].data(), kDN, re, im);
        fd.run(re, im, true);
        const float sc = 1.0f / (float) kDN;
        for (int i = 0; i < kD; i++) { tailOut[curOut ^ 1][0][i] = re[kD + i] * sc; tailOut[curOut ^ 1][1][i] = im[kD + i] * sc; }
        job = false;
    }

    void tailSlice(int s)
    {
        using namespace convdetail;
        const int nb = kDB;
        const int p0 = s * tailParts / (kSlices - 2), p1 = (s + 1) * tailParts / (kSlices - 2);
        for (int p = p0; p < p1; p++) {
            const float *x = &fdlTail[(size_t) ((tailPos - p + tailParts) % tailParts) * 4 * nb];
            for (int c = 0; c < 2; c++) {
                const int hc = channels == 1 ? 0 : c;
                const float *hr = &tail[hc][(size_t) p * 2 * nb], *hi = hr + nb;
                const float *xr = x + (c ? 2 * nb : 0), *xi = xr + nb;
                float *ur = accRe[c].data(), *ui = accIm[c].data();
                for (int k = 0; k < nb; k++) { ur[k] += hr[k] * xr[k] - hi[k] * xi[k]; ui[k] += hr[k] * xi[k] + hi[k] * xr[k]; }
            }
        }
    }
};

/* Builds a plan from the first region of an IR set (any sample rate: resampled linearly to `sr`). Loader thread only. Null if the set holds no audio. */
inline std::shared_ptr<ConvPlan> convBuild(const SampleSet &set, float sr)
{
    using namespace convdetail;
    if (set.regions.empty()) return nullptr;
    const SampleRegion &r = set.regions[0];
    if (r.frames < 2 || r.pcm.empty()) return nullptr;
    auto plan = std::make_shared<ConvPlan>();
    const int ch = r.channels == 2 ? 2 : 1;
    plan->channels = ch;
    const double ratio = (double) sr / (double) r.rate;
    int len = (int) ((double) r.frames * ratio);
    len = std::min(len, kHeadTaps + kMaxTailParts * kD);
    if (len < 1) return nullptr;
    plan->fb.init(kBN); plan->fd.init(kDN);
    plan->tailParts = len > kHeadTaps ? (len - kHeadTaps + kD - 1) / kD : 0;
    const float g = r.gain * (1.0f / 32768.0f);
    std::vector<float> x((size_t) len), re((size_t) kDN), im((size_t) kDN);
    plan->head[0].assign((size_t) kHeadParts * 2 * kBB, 0.0f);
    if (ch == 2) plan->head[1].assign((size_t) kHeadParts * 2 * kBB, 0.0f);
    for (int c = 0; c < ch; c++) {
        for (int i = 0; i < len; i++) {                                           /* the channel, resampled */
            const double p = (double) i / ratio;
            const int i0 = std::min((int) p, r.frames - 1), i1 = std::min(i0 + 1, r.frames - 1);
            const float f = (float) (p - i0);
            const float a = (float) r.pcm[(size_t) i0 * r.channels + (r.channels == 2 ? c : 0)], b = (float) r.pcm[(size_t) i1 * r.channels + (r.channels == 2 ? c : 0)];
            x[(size_t) i] = (a + (b - a) * f) * g;
        }
        for (int p = 0; p < kHeadParts; p++) {
            std::fill(re.begin(), re.begin() + kBN, 0.0f); std::fill(im.begin(), im.begin() + kBN, 0.0f);
            for (int i = 0; i < kB; i++) { const int t = p * kB + i; if (t < len) re[(size_t) i] = x[(size_t) t]; }
            plan->fb.run(re.data(), im.data(), false);
            float *h = &plan->head[c][(size_t) p * 2 * kBB];
            for (int k = 0; k < kBB; k++) { h[k] = re[(size_t) k]; h[kBB + k] = im[(size_t) k]; }
        }
        plan->tail[c].assign((size_t) plan->tailParts * 2 * kDB, 0.0f);
        for (int p = 0; p < plan->tailParts; p++) {
            std::fill(re.begin(), re.end(), 0.0f); std::fill(im.begin(), im.end(), 0.0f);
            for (int i = 0; i < kD; i++) { const int t = kHeadTaps + p * kD + i; if (t < len) re[(size_t) i] = x[(size_t) t]; }
            plan->fd.run(re.data(), im.data(), false);
            float *h = &plan->tail[c][(size_t) p * 2 * kDB];
            for (int k = 0; k < kDB; k++) { h[k] = re[(size_t) k]; h[kDB + k] = im[(size_t) k]; }
        }
        if (ch == 1) break;
    }
    plan->fdlHead.assign((size_t) kHeadParts * 4 * kBB, 0.0f);
    plan->fdlTail.assign((size_t) std::max(plan->tailParts, 1) * 4 * kDB, 0.0f);
    for (int c = 0; c < 2; c++) { plan->accRe[c].assign((size_t) kDB, 0.0f); plan->accIm[c].assign((size_t) kDB, 0.0f); }
    plan->tin.assign((size_t) 2 * kDN, 0.0f);
    plan->wr.assign((size_t) kDN, 0.0f); plan->wi.assign((size_t) kDN, 0.0f);
    plan->ar.assign((size_t) kDB, 0.0f); plan->ai.assign((size_t) kDB, 0.0f); plan->br.assign((size_t) kDB, 0.0f); plan->bi.assign((size_t) kDB, 0.0f);
    plan->reset();
    return plan;
}

} /* namespace tb */
