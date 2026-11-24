/* Renders an instance's audio on a thread of its own, one block ahead of the host.
 *
 * Schwung calls every module's render_block one after another on one core (the SPI callback, SCHED_FIFO 70, core 3), so four
 * heavy slots add up: a set of them reached 76 % of the 2.9 ms block on average and went over it at its peaks. The other three
 * cores sat mostly idle. Here each instance renders block N+1 on its own thread (SCHED_FIFO 20, cores 0-2) while the callback
 * hands the host block N, which the thread finished during the previous period. The callback's own share is a copy, a few
 * queued MIDI messages and a futex wake; the slots render side by side. The price is one block of output delay (128 frames,
 * 2.9 ms).
 *
 * Priority 20 is below Move's Link Main (35) and audio workers (70), as the Gearmulator modules measured (20 was clean, 70
 * starved Link Audio, SCHED_OTHER underran). The thread is placed on cores 0-2 because core 3 is the SPI callback's own.
 *
 * One rule keeps the engine single-threaded: the thread and the callback never touch it at the same time. The callback starts
 * a block (launch) only from render_block and waits for it (quiesce) before it next changes anything the render reads (MIDI,
 * a parameter, a preset). Handover is a release/acquire pair on two sequence numbers, so whatever one side wrote is visible to
 * the other. MIDI that arrives while a block is in flight is queued and applied, in order, before the next block starts.
 *
 * Not Linux (the desktop tests): start() reports false and the module renders on the callback as before. */
#pragma once

#include <atomic>
#include <cstdint>
#include <cstring>
#include <time.h>

#if defined(__linux__)
#include <pthread.h>
#include <sched.h>
#include <unistd.h>
#include <sys/syscall.h>
#include <linux/futex.h>
#endif

namespace tb {

class AsyncRender {
public:
    enum { kMaxFrames = 512, kMidiCap = 512, kMidiLen = 8 };
    typedef void (*RenderFn)(void *ctx, int16_t *out, int frames);

    /* From create_instance. prio: SCHED_FIFO priority; allowOther: run at SCHED_OTHER when FIFO is refused (tests only -- on the
     * Move a SCHED_OTHER render thread can be held off for a whole time slice). False: the caller renders synchronously. */
    bool start(RenderFn f, void *c, int prio, bool allowOther)
    {
#if defined(__linux__)
        fn = f; ctx = c;
        pthread_attr_t a;
        pthread_attr_init(&a);
        pthread_attr_setinheritsched(&a, PTHREAD_EXPLICIT_SCHED);   /* never inherit the callback's FIFO 70 */
        pthread_attr_setschedpolicy(&a, SCHED_FIFO);
        sched_param sp {};
        sp.sched_priority = prio;
        pthread_attr_setschedparam(&a, &sp);
        int rc = pthread_create(&th, &a, entry, this);
        prio_ = prio;
        if (rc != 0 && allowOther) {
            pthread_attr_setschedpolicy(&a, SCHED_OTHER);
            sp.sched_priority = 0;
            pthread_attr_setschedparam(&a, &sp);
            rc = pthread_create(&th, &a, entry, this);
            prio_ = 0;
        }
        pthread_attr_destroy(&a);
        if (rc != 0) { prio_ = -1; return false; }
        if (sysconf(_SC_NPROCESSORS_CONF) >= 4) {                   /* off core 3, the SPI callback's */
            cpu_set_t set;
            CPU_ZERO(&set);
            CPU_SET(0, &set); CPU_SET(1, &set); CPU_SET(2, &set);
            pthread_setaffinity_np(th, sizeof set, &set);
        }
        threadUp = true;
        running = true;
        return true;
#else
        (void) f; (void) c; (void) prio; (void) allowOther;
        return false;
#endif
    }

    /* From destroy_instance: the thread finishes its block and exits. */
    void stop()
    {
#if defined(__linux__)
        if (!threadUp) return;
        stopping.store(true, std::memory_order_release);
        kick.fetch_add(1, std::memory_order_acq_rel);
        wake();
        pthread_join(th, nullptr);
        threadUp = false;
#endif
        running = false;
    }

    bool on() const { return running; }
    bool busy() const { return running && done.load(std::memory_order_acquire) != kick.load(std::memory_order_relaxed); }

    /* Wait for the block in flight, however long it takes. Callback thread. Used before anything changes what the render reads
     * (a parameter, a preset, MIDI): the engine is the thread's until its block is done.
     *
     * It used to give up after 50 ms, mark the thread stuck and let the callback render the engine itself, while the thread was
     * still inside renderBlock. Two threads then ran the same engine: at a busy Move start-up one of them made a Vital delay's
     * memory while the other read it, and the Move app died (SIGSEGV in Fx::processDelayVital, 2026-11-24). A late thread is
     * only held off by busier ones; it always finishes, so waiting is safe. */
    void quiesce() { settle(-1); }

    /* render_block's wait: at most limitUs (-1 = no limit). False: the block is still being rendered. The caller then plays
     * silence for this period and leaves the engine alone; the next period asks again. */
    bool settle(int64_t limitUs)
    {
        if (!running) return true;
        const uint32_t k = kick.load(std::memory_order_relaxed);
        if (done.load(std::memory_order_acquire) == k) return true;
        const int64_t t0 = nowUs();
        unsigned spins = 0;
        bool ok = true;
        while (done.load(std::memory_order_acquire) != k) {
            relax();
            if ((++spins & 255u) == 0) {
                const int64_t w = nowUs() - t0;
                if (w > 50000) stuck = true;                        /* for the "async" key: a block held up past 50 ms */
                if (limitUs >= 0 && w > limitUs) { ok = false; break; }
            }
        }
        const int64_t w = nowUs() - t0;
        late.fetch_add(1, std::memory_order_relaxed);
        if (!ok) skipped.fetch_add(1, std::memory_order_relaxed);
        if (w > waitMax.load(std::memory_order_relaxed)) waitMax.store(w, std::memory_order_relaxed);
        return ok;
    }

    /* The block the thread finished (call after quiesce). False when there is none of this size yet. */
    bool collect(int16_t *out, int frames) const
    {
        if (have != frames) return false;
        memcpy(out, buf, (size_t) frames * 2 * sizeof(int16_t));
        return true;
    }

    /* Start the next block. Callback thread, after quiesce. */
    void launch(int frames)
    {
        jobFrames = frames;
        kick.fetch_add(1, std::memory_order_release);
        wake();
    }

    /* MIDI that came in while a block was in flight. Callback thread only, both ends. False: full (or too long), apply it now. */
    bool queueMidi(const uint8_t *m, int len)
    {
        if (len <= 0 || len > kMidiLen || nMidi >= kMidiCap) return false;
        midi[nMidi].len = (uint8_t) len;
        memcpy(midi[nMidi].b, m, (size_t) len);
        nMidi++;
        return true;
    }
    bool midiPending() const { return nMidi > 0; }
    template <class F> void drainMidi(F &&f)
    {
        for (int i = 0; i < nMidi; i++) f(midi[i].b, (int) midi[i].len);
        nMidi = 0;
    }

    /* For the "async" diagnostic key: the scheduling it got, how often and how long the callback waited, the slowest block. */
    int prio() const { return prio_; }
    bool wasStuck() const { return stuck; }
    long lateCount() const { return late.load(std::memory_order_relaxed); }
    long skippedCount() const { return skipped.load(std::memory_order_relaxed); }
    long long waitMaxUs() const { return (long long) waitMax.load(std::memory_order_relaxed); }
    long long renderMaxUs() const { return (long long) renderMax.load(std::memory_order_relaxed); }
    long blocks() const { return (long) done.load(std::memory_order_relaxed); }

private:
    struct Msg { uint8_t len; uint8_t b[kMidiLen]; };

    static int64_t nowUs()
    {
        timespec ts;
        clock_gettime(CLOCK_MONOTONIC, &ts);
        return (int64_t) ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
    }
    static void relax()
    {
#if defined(__aarch64__)
        __asm__ volatile("yield" ::: "memory");
#elif defined(__x86_64__) || defined(__i386__)
        __builtin_ia32_pause();
#endif
    }
    void wake()
    {
#if defined(__linux__)
        syscall(SYS_futex, reinterpret_cast<uint32_t *>(&kick), FUTEX_WAKE_PRIVATE, 1, nullptr, nullptr, 0);
#endif
    }

#if defined(__linux__)
    static void *entry(void *self) { static_cast<AsyncRender *>(self)->loop(); return nullptr; }
    void loop()
    {
#if defined(__aarch64__)
        {   /* flush-to-zero for this thread's whole life (FPCR is per thread; the callback sets it only for its own call) */
            uint64_t f;
            __asm__ volatile("mrs %0, fpcr" : "=r"(f));
            __asm__ volatile("msr fpcr, %0" : : "r"(f | (1ULL << 24)));
        }
#endif
        pthread_setname_np(pthread_self(), "tinyhouse-rt");
        uint32_t seen = 0;                                          /* kick starts at 0; a launch before this line still counts */
        for (;;) {
            uint32_t k;
            while ((k = kick.load(std::memory_order_acquire)) == seen)
                syscall(SYS_futex, reinterpret_cast<uint32_t *>(&kick), FUTEX_WAIT_PRIVATE, seen, nullptr, nullptr, 0);
            if (stopping.load(std::memory_order_acquire)) return;
            const int64_t t0 = nowUs();
            fn(ctx, buf, jobFrames);
            have = jobFrames;
            const int64_t r = nowUs() - t0;
            if (r > renderMax.load(std::memory_order_relaxed)) renderMax.store(r, std::memory_order_relaxed);
            seen = k;
            done.store(k, std::memory_order_release);
        }
    }
    pthread_t th {};
#endif

    RenderFn fn = nullptr;
    void *ctx = nullptr;
    bool threadUp = false, running = false, stuck = false;
    int prio_ = -1;
    std::atomic<uint32_t> kick { 0 }, done { 0 };
    std::atomic<bool> stopping { false };
    int jobFrames = 0, have = 0;
    alignas(64) int16_t buf[kMaxFrames * 2] = {};
    Msg midi[kMidiCap];
    int nMidi = 0;
    std::atomic<long> late { 0 }, skipped { 0 };
    std::atomic<int64_t> waitMax { 0 }, renderMax { 0 };
};

} // namespace tb
