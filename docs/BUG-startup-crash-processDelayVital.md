# Bug: Move app crashes at start-up in `tb::Fx::processDelayVital`

Status: **fixed in 0.9.19** (2026-11-24). Found 2026-11-24 (nothing in TinyHouse was changed).

## Resolution

- **Fault confirmed as a two-thread race.** Disassembling the deployed `dsp.so` (capstone over `.dynsym`):
  `processDelayVital+940/944` loads `vdly.buf[0].data()` into x10 and `vdly.buf[1].data()` into x11 together, and
  `+1120` reads through x10 without fault, then `+1148` faults reading through x11 (null). So `buf[0]` was allocated and `buf[1]` was
  not. On one thread that can't happen, because both are assigned together before anything reads them. Another thread was between the two
  `assign`s. The lr (+808) is stale, left over from an earlier `tan` call.
- **Only render paths touch `Fx`** (`Fx::set` comes from `Engine::refreshFx` / `refreshBus` inside `renderBlock`), so two
  `renderBlock`s ran at once. The only way that happens is `AsyncRender::quiesce()`'s 50 ms give-up: it set `running = false`
  while the thread was still rendering, and `tb_render_block` then rendered the engine itself.
- **Fix** (`async_render.h`, `tablor_plugin.cpp`): `quiesce()` (before a param, preset or MIDI change) now waits until the block
  is done. `tb_render_block` uses `settle(50000)`: if the thread is still busy after 50 ms, that period is silent and
  the callback leaves the engine alone. The thread is never abandoned. The `async` diag key gained `skipped=`.
  Hardening in `fx.h`: the lazy-allocation tests look at the last buffer made (`buf[1]`, `vr.pd[1]`), not the first.
- **Test:** `tools/asyncstall.cpp` (built as `tablor_asyncstall` by `zig-build.sh arm`, Linux only) stalls the render thread
  80 ms and counts threads inside the "engine". On the Move the 0.9.18 logic reached 2, and the fix keeps it at 1 (PASS).
- Suggested fix 1 (allocate in the constructor) was not done: with the race gone it only costs memory (~6 MB per instance).

## Symptom

Rarely at Move start-up (2 of ~98 starts in the log) the whole Move app dies with a SIGSEGV. Pads stay lit, the screen is
dark, and nothing restarts it. Restarting brings it back and the same sets then load fine. TinyHouse's `dsp.so` runs inside
the Move process, so its crash takes the device UI down.

Restart: `ssh ableton@move.local 'nohup sh /data/UserData/schwung/restart-move.sh >/dev/null 2>&1 &'`

## Evidence (`/data/UserData/schwung/debug.log`, `crash_maps.txt`)

Both crashes (19:32:34 and 22:16:32) are identical, ~0.1-0.2 s after the third TinyHouse instance logs "ready", right
after `frames-dropped` warnings, on a start caused by `restart-move.sh`:

```
CRASH Caught SIGSEGV si_addr=0xf5298 pc=0x7f97a098fc lr=0x7f97a097a8
```

- Library: the deployed `dsp.so` is the repo build (`schwung-tablor/build/dsp.so`, md5 `6cc87a71ecc80610b8a314eb6035be15`).
- pc is file offset `0x1698fc`, lr `0x1697a8`: both inside `_ZN2tb2Fx17processDelayVitalEPfS1_i`
  (`schwung-tablor/src/dsp/fx.h:2043`), +1148 and +808 bytes. Not disassembled, so the exact line is unknown.
- `si_addr 0xf5298` = 4 x 251,046: a valid float index into a `kMax` (1<<18) buffer, off a **null base pointer**. So a
  `std::vector<float>` in `vdly.buf[]` had a null `data()` (empty) while being indexed. It is not a null `Fx`/`Engine`
  (measured member offsets don't reach that address).

## Likely cause (inferred, not confirmed)

The delay buffers are created lazily on the render path: `Fx::set` (`fx.h:950`) and `processDelayVital` (`fx.h:2047`) both
do `if (buf[0].empty()) { buf[0].assign(kMax,0); buf[1].assign(kMax,0); }`. On one thread that is safe, so it needs two
threads inside the engine at once.

Suspect: `AsyncRender::quiesce()` (`src/dsp/async_render.h`, ~line 99) gives up after 50 ms, sets `running = false`
and returns **while the render thread may still be inside `Engine::renderBlock`**. The callback then renders the same
engine itself (`tablor_plugin.cpp` ~2630), so two threads run `Fx::set` / `processDelayVital` on the same vectors. A
concurrent first-use `assign` gives a null `data()` with a valid index. Start-up (several instances created at once,
dropped frames) is when a 50 ms stall is most likely. Not proven: nothing has logged `stuck`, and a FIFO render thread
shouldn't stall that long.

## How to confirm

- Log when `quiesce()` takes the stuck path (it logs nothing now), and read the `async` diag key
  (`tablor_plugin.cpp:2529`, reports `stuck=`) after a start-up. Restart Move 30+ times; it is rare.
- Or build natively with ThreadSanitizer, or force the 50 ms limit to 0 in a test, and watch `processDelayVital`.
- Disassemble `processDelayVital` around +1148 to see which access faulted.

## Suggested fix

1. Allocate the Vital delay and reverb buffers in `Fx`'s constructor (like `chorusBuf`, `dly[]`, `lines[]`), not in
   `set()` / `process`. The reverb buffers at `fx.h:951` (`vr.fb`, `vr.ap`, `vr.pd`) have the same pattern. Memory is
   ~2 MB x 2 per `Fx`, 3 `Fx` per instance.
2. Never leave two threads in the engine: on the stuck path, stop/join the render thread before the callback touches
   the engine, or keep rendering on the thread.
3. Cheap hardening: test `buf[0].empty() || buf[1].empty()` (not a fix for the race).

TinyVoice is not involved; its deploy script just restarts Schwung often enough to hit a rare race.
