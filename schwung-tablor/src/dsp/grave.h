/* Frees big shared data off the audio thread.
 *
 * A wavetable, a sample set or a convolution plan is megabytes; the last reference to one can drop on the audio callback (a voice that was still playing from it lets go
 * when its note ends), and freeing it there costs a slice of a 2.7 ms block. A shared_ptr made with makeGrave<T>() hands its memory to this queue when that happens on any
 * thread but a loader worker; a worker drains the queue and does the delete. While no worker runs (the unit tests) a delete is done in place.
 *
 * The queue is fixed-size, so pushing never allocates; a full queue falls back to deleting in place. */
#pragma once

#include <atomic>
#include <memory>
#include <mutex>

namespace tb {

class Grave {
public:
    static Grave &get() { static Grave g; return g; }

    /* A loader worker calls these around its life: the queue is used while at least one is running. */
    void workerUp() { users.fetch_add(1); }
    void workerDown() { if (users.fetch_sub(1) == 1) drain(); }
    bool active() const { return users.load(std::memory_order_acquire) > 0; }
    static bool &onWorker() { thread_local bool w = false; return w; }

    /* Any thread (in practice the audio thread). False: no room, the caller deletes. */
    bool push(void *p, void (*del)(void *))
    {
        unsigned h = head.load(std::memory_order_relaxed);
        for (;;) {
            if (h - tail.load(std::memory_order_acquire) >= kN) return false;
            if (head.compare_exchange_weak(h, h + 1, std::memory_order_acq_rel)) break;
        }
        q[h % kN].p = p; q[h % kN].del = del;
        q[h % kN].ready.store(true, std::memory_order_release);
        return true;
    }

    /* A worker thread: delete what has been queued. */
    void drain()
    {
        std::lock_guard<std::mutex> lk(drainM);
        for (;;) {
            const unsigned t = tail.load(std::memory_order_relaxed);
            if (t == head.load(std::memory_order_acquire)) break;
            Slot &s = q[t % kN];
            if (!s.ready.load(std::memory_order_acquire)) break;           /* a producer is still filling it in: the next pass takes it */
            void *p = s.p; void (*del)(void *) = s.del;
            s.ready.store(false, std::memory_order_relaxed);
            tail.store(t + 1, std::memory_order_release);
            del(p);
        }
    }

private:
    static constexpr unsigned kN = 64;
    struct Slot { void *p = nullptr; void (*del)(void *) = nullptr; std::atomic<bool> ready { false }; };
    Slot q[kN];
    std::atomic<unsigned> head { 0 }, tail { 0 };
    std::atomic<int> users { 0 };
    std::mutex drainM;
};

template <class T> struct GraveDelete {
    void operator()(T *p) const
    {
        Grave &g = Grave::get();
        if (g.active() && !Grave::onWorker() && g.push(p, [](void *x) { delete static_cast<T *>(x); })) return;
        delete p;
    }
};

template <class T> inline std::shared_ptr<T> makeGrave() { return std::shared_ptr<T>(new T(), GraveDelete<T>()); }

} // namespace tb
