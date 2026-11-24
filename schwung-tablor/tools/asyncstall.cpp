/* The render thread held up past render_block's 50 ms limit must keep the engine to itself (async_render.h, 2026-11-24).
 *
 * The callback side below does what tablor_plugin.cpp does: settle(50 ms) in render_block, quiesce() before a parameter or
 * MIDI change. The render function stalls 80 ms on its third block, as a FIFO 20 thread can when a busy start-up holds it off.
 * Every "engine" entry counts itself in and out; two at once is the start-up crash. Linux only (the class renders nothing
 * elsewhere): run it on the Move. Exit 0 = pass. */
#include "async_render.h"

#include <atomic>
#include <cstdio>
#include <time.h>

static std::atomic<int> inside { 0 }, worst { 0 };
static std::atomic<int> renders { 0 };

static void sleepUs(long us)
{
    timespec ts { us / 1000000, (us % 1000000) * 1000 };
    nanosleep(&ts, nullptr);
}

static void enter()
{
    const int now = inside.fetch_add(1) + 1;
    int w = worst.load();
    while (now > w && !worst.compare_exchange_weak(w, now)) {}
}
static void leave() { inside.fetch_sub(1); }

static void render(void *, int16_t *out, int frames)
{
    enter();
    const int n = renders.fetch_add(1);
    sleepUs(n == 2 ? 80000 : 300);
    for (int i = 0; i < frames * 2; i++) out[i] = 0;
    leave();
}

int main()
{
    tb::AsyncRender a;
    if (!a.start(render, nullptr, 20, true)) { std::printf("asyncstall: no render thread here (not Linux?)\n"); return 2; }
    int16_t out[128 * 2];
    int silent = 0;
    for (int period = 0; period < 200; period++) {
        if (!a.settle(50000)) { silent++; sleepUs(2900); continue; }        /* render_block: the thread still has it */
        a.collect(out, 128);
        if (period % 7 == 3) { a.quiesce(); enter(); sleepUs(50); leave(); }   /* set_param between blocks */
        a.launch(128);
        if (period % 5 == 1) { a.quiesce(); enter(); sleepUs(50); leave(); }   /* set_param while a block is in flight */
        sleepUs(2900);
    }
    a.quiesce();
    a.stop();
    std::printf("asyncstall: %d renders, %d silent periods, skipped=%ld stuck=%d, most inside the engine at once: %d\n",
                renders.load(), silent, a.skippedCount(), a.wasStuck() ? 1 : 0, worst.load());
    const bool ok = worst.load() == 1 && a.skippedCount() >= 1;
    std::printf("asyncstall: %s\n", ok ? "PASS" : "FAIL");
    return ok ? 0 : 1;
}
