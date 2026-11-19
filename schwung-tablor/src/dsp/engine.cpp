#include "engine.h"

#include <chrono>
#include <cmath>
#include <cstring>

namespace tb {

Engine::Engine()
{
    for (int i = 0; i < TB_PARAM_COUNT; i++) {
        pots[i] = tb_params[i].def;
        busPots[0][i] = busPots[1][i] = tb_params[i].def;
    }

    voices.reserve(8);
    for (int i = 0; i < 8; i++) {
        voices.emplace_back(analog);
        voices.back().setSampleRate(44100.0);
    }

    auto init = makeInitTable();
    for (auto &t : table) std::atomic_store(&t, init);

}

int parseMacroRoutes(const char *text, MacroRouteSpec *out, int max)
{
    int n = 0;
    const char *p = text;
    while (*p && n < max) {
        char *end = nullptr;
        const long macro = strtol(p, &end, 10);
        if (end == p || *end != ':') break;
        const char *k = end + 1;
        const char *c2 = strchr(k, ':');
        if (!c2) break;
        const size_t kl = (size_t) (c2 - k);
        const float travel = strtof(c2 + 1, &end);
        for (int i = 0; i < TB_PARAM_COUNT; i++)
            if (tb_params[i].type == TB_INT && strlen(tb_params[i].key) == kl && !strncmp(tb_params[i].key, k, kl)) {
                if (macro >= 1 && macro <= 8) out[n++] = { (int) macro - 1, i, travel };
                break;
            }
        p = end;
        if (*p == ',') p++;
        else break;
    }
    return n;
}

void Engine::setMacroRoutes(const MacroRoute *r, int n)
{
    n = std::max(0, std::min(n, kMaxMacroRoutes));
    nMacroRoutes = 0;                                         /* the audio thread never reads the routes, so this is only ordering for the next macro move */
    for (int i = 0; i < n; i++)
        if (r[i].macro >= 0 && r[i].macro < 8 && r[i].pot >= 0 && r[i].pot < TB_PARAM_COUNT)
            macroRoutes[nMacroRoutes++] = r[i];
}

void Engine::macroMoved(int macro, float now)
{
    if (macro < 0 || macro >= 8) return;
    const float change = now - macroSeen[macro];
    macroSeen[macro] = now;
    if (change == 0.0f) return;
    for (int i = 0; i < nMacroRoutes; i++) {
        const MacroRoute &r = macroRoutes[i];
        if (r.macro != macro) continue;
        const tb_param_t &p = tb_params[r.pot];
        pots[r.pot] = std::min(std::max(pots[r.pot] + r.travel * change / 127.0f, p.min), p.max);
    }
}

void Engine::macroSync()
{
    for (int i = 0; i < 8; i++) macroSeen[i] = pots[TB_P_MAC1 + i];
}

Engine::Tables Engine::loadTables() const
{
    Tables t;
    for (int o = 0; o < kOscs; o++) t.t[o] = std::atomic_load(&table[o]);
    t.s = std::atomic_load(&sampleSet);
    t.s2 = std::atomic_load(&sampleSet2);
    t.s3 = std::atomic_load(&sampleSet3);
    return t;
}

int Engine::activeStreams() const
{
    int n = 0;
    for (const auto &v : voices) n += v.streamCount();
    return n;
}

VoiceContext Engine::makeContext(const Tables &t)
{
    VoiceContext c;
    c.pots = pots;
    for (int o = 0; o < kOscs; o++) c.table[o] = t.t[o].get();
    c.sample = t.s;
    c.sample2 = t.s2;
    c.sample3 = t.s3;

    /* The share comes from the Voices control, so a chord cannot change a held
     * note's unison. More voices than that can only be sounding just after the
     * control was turned down, and the budget must hold then too. */
    const bool mono = (int) pots[TB_P_VOICE_MODE] == 1;
    int sharers = mono ? 1 : std::clamp((int) pots[TB_P_VOICES], 1, 8);
    int sounding = 0;
    for (const auto &v : voices) if (v.isActive()) sounding++;
    c.streamBudget = kStreamBudget / std::max(sharers, sounding);

    c.analog = &analog;
    c.modWheel = modWheel;
    c.aftertouch = aftertouch;
    c.pitchBendSemis = pitchBendNorm * pots[TB_P_PB_RANGE];
    c.bpm = hostBpm;
    for (int l = 0; l < kLfos; l++) c.lfoFree[l] = lfoFree[l];
    return c;
}

Voice *Engine::findVoiceToSteal()
{
    /* idle first; then quietest releasing; then oldest serial */
    Voice *best = nullptr;
    for (auto &v : voices)
        if (!v.isActive()) return &v;

    for (auto &v : voices)
        if (v.isReleasing() && (!best || v.envOutput() < best->envOutput()))
            best = &v;
    if (best) return best;

    for (auto &v : voices)
        if (!best || v.serialNumber() < best->serialNumber())
            best = &v;
    return best;
}

void Engine::noteOn(int note, float vel)
{
    Tables t = loadTables();
    VoiceContext c = makeContext(t);

    const bool mono = (int) pots[TB_P_VOICE_MODE] == 1;
    const bool glideOn = (int) pots[TB_P_GLIDE_MODE] != 0;
    /* Law Vital: a glide starts only while the previous key is still down, unless Force is Always (Law Serum glides always, as it did) */
    const bool glideNow = pots[TB_P_GLIDE_VLAW] < 0.5f || pots[TB_P_GLIDE_VFORCE] >= 0.5f || keysHeld > 0;
    if (note >= 0 && note < 128 && !keyHeld[note]) { keyHeld[note] = true; keysHeld++; }

    if (mono) {
        if (monoStackLen < kStackMax)
            monoStack[monoStackLen++] = note;

        Voice &v = voices[0];
        if (v.isActive()) {
            /* A note arriving while the previous one is still releasing takes its voice over where it is: the amplitude envelope re-attacks from its current level and the
             * oscillator phases and filters carry on. A fresh start() zeroes the envelope, which is a step down from the release level (a click: a mono preset with short notes,
             * Lead - Rollin, had 20 dB more sub-bass than Serum's from those steps). */
            v.retrigger(c, note, vel, glideNow);          /* legato / retrig path */
        } else {
            float from = glideOn && glideNow ? lastPlayedNote : -1.0f;
            v.start(c, note, vel, ++serialCounter, from);
        }
    } else {
        int maxVoices = std::clamp((int) pots[TB_P_VOICES], 1, 8);
        int activeCount = 0;
        for (auto &v : voices) if (v.isActive()) activeCount++;

        Voice *v = nullptr;
        for (auto &vv : voices)
            if (!vv.isActive()) { v = &vv; break; }
        if (!v || activeCount >= maxVoices)
            v = findVoiceToSteal();
        if (!v) return;

        float from = glideOn && glideNow ? lastPlayedNote : -1.0f;
        v->start(c, note, vel, ++serialCounter, from);
    }

    lastPlayedNote = (float) note;
}

void Engine::noteOff(int note)
{
    if (note >= 0 && note < 128 && keyHeld[note]) { keyHeld[note] = false; keysHeld--; }
    const bool mono = (int) pots[TB_P_VOICE_MODE] == 1;

    if (sustainDown) {
        if (note >= 0 && note < 128) sustained[note] = true;
        if (!mono) return;
    }

    /* Drop it from the stack whatever the mode is NOW: the stack can still
     * hold notes pressed before a mono->poly flip, and a stale entry there
     * comes back as a phantom retrigger the next time mono is selected. */
    for (int i = 0; i < monoStackLen; i++) {
        if (monoStack[i] == note) {
            for (int j = i; j < monoStackLen - 1; j++)
                monoStack[j] = monoStack[j + 1];
            monoStackLen--;
            break;
        }
    }

    if (mono) {
        Voice &v = voices[0];
        if (v.isActive() && v.currentNote() == note) {
            if (monoStackLen > 0) {
                /* return to the most recent held note (last-note priority) */
                Tables t = loadTables();
                VoiceContext c = makeContext(t);
                v.retrigger(c, monoStack[monoStackLen - 1], 0.8f);
            } else if (!sustainDown) {
                v.stop(true);
            } else {
                sustained[note & 127] = true;
            }
        }
    }

    /* Every OTHER voice holding this note is released too, in BOTH modes.
     *
     * This used to be the else-arm of the branch above, which made releasing
     * a note depend on the mode the synth is in when the finger LIFTS rather
     * than when it landed. Flip Poly -> Mono with a chord held -- one encoder
     * turn, or any preset recall that selects Mono -- and every note-off went
     * down the mono arm, which only ever looks at voices[0]. The rest of the
     * chord never got its note-off and sounded on until something stole the
     * voice: the "stuck note that clears when I play more notes" report. */
    for (size_t i = mono ? 1 : 0; i < voices.size(); i++) {
        Voice &v = voices[i];
        if (v.isActive() && !v.isReleasing() && v.currentNote() == note)
            v.stop(true);
    }
}

void Engine::allNotesOff(bool allowTail)
{
    clipKillAll(false);
    arpStop(false);
    nDAct = 0;                                  /* the demo keeps playing; what it had sounding is cut with the rest */
    for (auto &v : voices)
        if (v.isActive())
            allowTail ? v.stop(true) : v.kill();
    monoStackLen = 0;
    std::memset(sustained, 0, sizeof sustained);
    std::memset(keyHeld, 0, sizeof keyHeld);
    keysHeld = 0;
}

/* ---------------------------------------------------------------- clip player */
double Engine::clipTicksPerFrame() const
{
    const double bpm = hostBpm >= 20.0f ? std::clamp((double) hostBpm, 40.0, 300.0) : 120.0;
    return bpm / 60.0 * 96.0 / 44100.0;
}

void Engine::clipKillAll(bool noteOffs)
{
    nHeld = 0;
    for (auto &p : players) {
        if (!p.used) continue;
        if (noteOffs) for (int i = 0; i < p.nAct; i++) noteOff(p.actNote[i]);
        p.used = false; p.nAct = 0;
    }
}

void Engine::clipSync()
{
    const int e = clipEpoch.load();
    if (e == clipEpochSeen) return;
    clipEpochSeen = e;
    clipKillAll(true);                          /* a new pattern: the old players stop where they are */
}

bool Engine::clipActive()
{
    clipSync();
    auto c = std::atomic_load(&clipSp);
    return c && c->n > 0 && c->len > 0;
}

/* A player on `key` from the pattern's start (the same key's, or in the Mono mode the one player, starting over). */
void Engine::clipStart(int key)
{
    ClipPlayer *p = nullptr;
    for (auto &q : players) if (q.used && q.key == key) p = &q;
    for (auto &q : players) if (!p && !q.used) p = &q;
    if (!p) {                                   /* all busy: the oldest goes */
        p = &players[0];
        for (auto &q : players) if (q.serial < p->serial) p = &q;
    }
    for (int i = 0; i < p->nAct; i++) noteOff(p->actNote[i]);
    p->used = true; p->key = key; p->pos = 0.0; p->firedTo = 0.0; p->nAct = 0; p->serial = ++playerSerial;
}

void Engine::clipKeyDown(int key)
{
    auto c = std::atomic_load(&clipSp);
    if (c && c->mono) {
        for (int i = 0; i < nHeld; i++) if (heldKeys[i] == key) { for (int j = i; j < nHeld - 1; j++) heldKeys[j] = heldKeys[j + 1]; nHeld--; break; }
        if (nHeld < 32) heldKeys[nHeld++] = key;
        for (auto &q : players) if (q.used && q.key != key) { for (int i = 0; i < q.nAct; i++) noteOff(q.actNote[i]); q.used = false; q.nAct = 0; }
        clipStart(key);
        return;
    }
    clipStart(key);
}

void Engine::clipKeyUp(int key)
{
    auto c = std::atomic_load(&clipSp);
    if (c && c->mono) {
        for (int i = 0; i < nHeld; i++) if (heldKeys[i] == key) { for (int j = i; j < nHeld - 1; j++) heldKeys[j] = heldKeys[j + 1]; nHeld--; break; }
        bool was = false;
        for (auto &p : players)
            if (p.used && p.key == key) {
                for (int i = 0; i < p.nAct; i++) noteOff(p.actNote[i]);
                p.used = false; p.nAct = 0; was = true;
            }
        if (was && nHeld > 0) clipStart(heldKeys[nHeld - 1]);      /* back to the key still held, from the pattern's start */
        return;
    }
    for (auto &p : players)
        if (p.used && p.key == key) {
            for (int i = 0; i < p.nAct; i++) noteOff(p.actNote[i]);
            p.used = false; p.nAct = 0;
        }
}

/* Send what is due at each player's position: the note-offs first (a note ending as the next begins releases before it), then the
 * note-ons that start before this block's first sample. */
void Engine::clipFire()
{
    auto c = std::atomic_load(&clipSp);
    if (!c || c->n <= 0 || c->len <= 0) return;
    constexpr double eps = 1e-6;
    for (auto &p : players) {
        if (!p.used) continue;
        for (int i = 0; i < p.nAct;) {
            if (p.actEnd[i] < p.pos + eps) {
                noteOff(p.actNote[i]);
                p.actNote[i] = p.actNote[p.nAct - 1]; p.actEnd[i] = p.actEnd[p.nAct - 1]; p.nAct--;
            } else i++;
        }
        const double to = p.pos + eps;
        if (to <= p.firedTo) continue;
        const long k0 = (long) std::floor(p.firedTo / c->len), k1 = (long) std::floor(to / c->len);
        for (long k = k0; k <= k1; k++)
            for (int j = 0; j < c->n; j++) {
                const ClipNote &cn = c->notes[j];
                const double s = (double) k * c->len + cn.tick;
                if (s < p.firedTo || s >= to) continue;
                const int note = std::clamp((int) cn.note + p.key - 60, 0, 127);
                noteOn(note, (float) cn.vel / 127.0f);
                if (p.nAct < 16) { p.actNote[p.nAct] = note; p.actEnd[p.nAct] = s + std::max(cn.dur, 1); p.nAct++; }
            }
        p.firedTo = to;
    }
}

/* Frames until the next thing a player must do (a note start or end), so the block is cut there. */
int Engine::clipLimit(int frames)
{
    auto c = std::atomic_load(&clipSp);
    if (!c || c->n <= 0 || c->len <= 0) return frames;
    const double tpf = clipTicksPerFrame();
    for (auto &p : players) {
        if (!p.used) continue;
        double next = 1e300;
        for (int i = 0; i < p.nAct; i++) next = std::min(next, p.actEnd[i]);
        const double base = std::floor(p.firedTo / c->len) * c->len;
        for (int j = 0; j < c->n; j++) {
            double s = base + c->notes[j].tick;
            if (s < p.firedTo) s += c->len;
            next = std::min(next, s);
        }
        const double f = std::ceil((next - p.pos - 1e-6) / tpf);
        if (f < (double) frames) frames = std::max(1, (int) f);
    }
    return frames;
}

void Engine::clipAdvance(int frames)
{
    const double d = clipTicksPerFrame() * frames;
    for (auto &p : players) if (p.used) p.pos += d;
}


/* ---------------------------------------------------------------- demo player */
/* The song clock: ticks of 1/96 beat, advanced by wall time at the host tempo, one for the whole process (dsp.so is loaded once, so every slot's engine reads the same).
 * A demo that starts takes its place in its part from this clock, so bass and lead switched on at different moments still play the same bar. */
static std::atomic_flag gSongLock = ATOMIC_FLAG_INIT;
static double gSongTicks = 0.0, gSongWall = -1.0;

static double (*gWallClock)() = nullptr;
void demoSetWallClock(double (*fn)()) { gWallClock = fn; }

static double demoWallNow()
{
    return gWallClock ? gWallClock() : std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

static double demoSongTicks(float hostBpm)
{
    const double bpm = hostBpm >= 20.0f ? std::clamp((double) hostBpm, 40.0, 300.0) : 120.0;
    const double now = demoWallNow();
    while (gSongLock.test_and_set(std::memory_order_acquire)) {}
    if (gSongWall < 0.0) gSongWall = now;
    gSongTicks += (now - gSongWall) * bpm / 60.0 * 96.0;
    gSongWall = now;
    const double t = gSongTicks;
    gSongLock.clear(std::memory_order_release);
    return t;
}

void Engine::demoSend(int note, int vel)
{
    uint8_t m[3] = { (uint8_t) (vel > 0 ? 0x90 : 0x80), (uint8_t) std::clamp(note, 0, 127), (uint8_t) vel };
    onMidi(m, 3);                               /* as a played key, so an arpeggiator or clip in the preset answers it */
}

void Engine::demoStop(bool noteOffs)
{
    if (noteOffs) for (int i = 0; i < nDAct; i++) demoSend(dAct[i], 0);
    nDAct = 0;
    demoRun = false;
    demoArmed = false;
}

/* Called every block: starts (on the next bar) or stops the player to follow the switch and the part.
 * The Move's transport is the clock when the host reports one: the part's position is the transport's beat (mod the part's length), so it plays in step with the set's clips,
 * starts when the transport starts, stops when it stops and takes up again when it restarts. Switching the demo on does not start the transport: it waits, ready, for Play (at
 * the next bar after it). Only a host that reports no transport at all gets the song clock of its own. */
void Engine::demoSync()
{
    constexpr double kBar = 4 * 96;
    const int e = demoEpoch.load();
    if (e != demoEpochSeen) { demoEpochSeen = e; demoStop(true); }
    auto d = std::atomic_load(&demoSp);
    if (demoWant.load() == 0 || !d) {
        if (demoRun || demoArmed) demoStop(true);
        demoTrSeen = false; demoWaitT = -1.0;
        return;
    }
    const bool tr = hostBeatKnown && hostBeat >= 0.0;
    if (tr) demoTrSeen = true;
    if (demoRun) {
        if (demoOnTr) {
            if (!tr) { demoStop(true); return; }                              /* the transport stopped: so does the demo; it takes up again at the next bar after a restart */
            const double diff = (hostBeat * 96.0 - demoOff) - demoPos;
            if (std::fabs(diff) > 48.0) { demoStop(true); return; }           /* a jump: start over at the next bar */
            if (std::fabs(diff) > 2.0) demoPos += diff;                       /* drift (a tempo change): held to the transport */
        } else if (tr) {
            demoStop(true);                                                   /* a transport has appeared under a demo on its own clock: lock to it */
        }
        if (demoRun) return;
    }
    bool useTr = tr;
    if (!tr) {
        if (hostBeatKnown) return;                                            /* the transport is the clock: wait for Play, however long it takes */
    }
    const double t = useTr ? hostBeat * 96.0 : demoSongTicks(hostBpm);
    const long bar = (long) std::floor(t / kBar);
    if (demoArmed && demoArmTr != useTr) demoArmed = false;                   /* armed on the other clock: its bar numbers mean nothing here */
    constexpr double kLate = 48.0;                                            /* half a beat: a transport that has just started (beat 0), or a bar that has only just begun, is joined at once, from its first bar */
    if (!(useTr && t - (double) bar * kBar < kLate)) {
        if (!demoArmed) { demoArmed = true; demoArmTr = useTr; demoArmBar = bar; return; }
        if (bar <= demoArmBar) return;
    }
    const double boundary = (double) bar * kBar;
    demoFiredTo = std::fmod(boundary, (double) d->len);
    demoPos = demoFiredTo + (t - boundary);
    demoOff = t - demoPos;
    demoOnTr = useTr;
    nDAct = 0;
    demoRun = true;
}

void Engine::demoFire()
{
    auto d = std::atomic_load(&demoSp);
    if (!d || !demoRun) return;
    constexpr double eps = 1e-6;
    for (int i = 0; i < nDAct;) {
        if (dActEnd[i] < demoPos + eps) {
            demoSend(dAct[i], 0);
            dAct[i] = dAct[nDAct - 1]; dActEnd[i] = dActEnd[nDAct - 1]; nDAct--;
        } else i++;
    }
    const double to = demoPos + eps;
    if (to <= demoFiredTo) return;
    const long k0 = (long) std::floor(demoFiredTo / d->len), k1 = (long) std::floor(to / d->len);
    for (long k = k0; k <= k1; k++) {
        const double lo = std::max(demoFiredTo - (double) k * d->len, 0.0), hi = std::min(to - (double) k * d->len, (double) d->len);
        auto it = std::lower_bound(d->notes.begin(), d->notes.end(), lo, [](const DemoNote &n, double v) { return (double) n.tick < v; });
        for (; it != d->notes.end() && (double) it->tick < hi; ++it) {
            demoSend(it->note, it->vel);
            if (nDAct >= 64) { demoSend(dAct[0], 0); for (int j = 1; j < nDAct; j++) { dAct[j - 1] = dAct[j]; dActEnd[j - 1] = dActEnd[j]; } nDAct--; }
            dAct[nDAct] = it->note; dActEnd[nDAct] = (double) k * d->len + it->tick + std::max(it->dur, 1); nDAct++;
        }
    }
    demoFiredTo = to;
}

/* Frames until the next note start or end, so the block is cut there. */
int Engine::demoLimit(int frames)
{
    auto d = std::atomic_load(&demoSp);
    if (!d || !demoRun) return frames;
    const double tpf = clipTicksPerFrame();
    double next = 1e300;
    for (int i = 0; i < nDAct; i++) next = std::min(next, dActEnd[i]);
    const long k = (long) std::floor(demoFiredTo / d->len);
    const double p = demoFiredTo - (double) k * d->len;
    auto it = std::lower_bound(d->notes.begin(), d->notes.end(), p, [](const DemoNote &n, double v) { return (double) n.tick < v; });
    next = std::min(next, it != d->notes.end() ? (double) k * d->len + it->tick : (double) (k + 1) * d->len + d->notes.front().tick);
    const double f = std::ceil((next - demoPos - 1e-6) / tpf);
    return f < (double) frames ? std::max(1, (int) f) : frames;
}

void Engine::demoAdvance(int frames)
{
    if (!demoRun) return;
    demoPos += clipTicksPerFrame() * frames;
    demoSongTicks(hostBpm);                     /* keep the shared clock current while something plays (a tempo change is then integrated as it happens) */
}


/* ---------------------------------------------------------------- arpeggiator */
void Engine::arpStop(bool noteOffs)
{
    if (noteOffs) for (int i = 0; i < nAAct; i++) noteOff(aAct[i]);
    nAAct = 0; nAHeld = 0; arpRun = false;
    arpPos = arpFiredTo = 0.0; arpNextStep = 0; arpLastPick = -1;
}

void Engine::arpSync()
{
    const int e = arpEpoch.load();
    if (e == arpEpochSeen) return;
    arpEpochSeen = e;
    arpStop(true);
}

bool Engine::arpActive()
{
    arpSync();
    return std::atomic_load(&arpSp) != nullptr;
}

bool Engine::arpInZone(int key)
{
    auto a = std::atomic_load(&arpSp);
    return a && key >= a->zoneMin && key <= a->zoneMax;
}

void Engine::arpKeyDown(int key, int vel)
{
    for (int i = 0; i < nAHeld; i++)
        if (aHeld[i].note == key) { for (int j = i; j < nAHeld - 1; j++) aHeld[j] = aHeld[j + 1]; nAHeld--; break; }
    if (nAHeld >= 32) { for (int j = 0; j < 31; j++) aHeld[j] = aHeld[j + 1]; nAHeld = 31; }
    aHeld[nAHeld++] = { key, (uint8_t) std::clamp(vel, 1, 127) };
    if (!arpRun) { arpRun = true; arpPos = arpFiredTo = 0.0; arpNextStep = 0; nAAct = 0; arpLastPick = -1; }
}

bool Engine::arpKeyUp(int key)
{
    for (int i = 0; i < nAHeld; i++)
        if (aHeld[i].note == key) {
            for (int j = i; j < nAHeld - 1; j++) aHeld[j] = aHeld[j + 1];
            if (--nAHeld == 0) arpStop(true);
            return true;
        }
    return false;
}

void Engine::arpPlay(int note, int vel, double endTick)
{
    note = std::clamp(note, 0, 127);
    noteOn(note, (float) std::clamp(vel, 1, 127) / 127.0f);
    if (nAAct >= 32) { noteOff(aAct[0]); for (int i = 0; i < 31; i++) { aAct[i] = aAct[i + 1]; aActEnd[i] = aActEnd[i + 1]; } nAAct = 31; }
    aAct[nAAct] = note; aActEnd[nAAct] = endTick; nAAct++;
}

/* The note of step s for the shapes that walk the held keys. */
void Engine::arpStepNote(long s, const Arp &a)
{
    if (nAHeld <= 0) return;
    const int m = nAHeld;
    int base[32], vel[32];
    for (int i = 0; i < m; i++) { base[i] = aHeld[i].note; vel[i] = aHeld[i].vel; }
    if (a.shape != 3)
        for (int i = 1; i < m; i++) {                      /* ascending */
            const int b = base[i], v = vel[i];
            int j = i - 1;
            while (j >= 0 && base[j] > b) { base[j + 1] = base[j]; vel[j + 1] = vel[j]; j--; }
            base[j + 1] = b; vel[j + 1] = v;
        }
    const int total = std::min(m * (a.trRange + 1), 256);
    int i;
    auto rnd = [&]() { arpRng ^= arpRng << 13; arpRng ^= arpRng >> 17; arpRng ^= arpRng << 5; return (int) (arpRng >> 8); };
    switch (a.shape) {
    case 1: i = total - 1 - (int) (s % total); break;
    case 2: { const int period = std::max(1, 2 * total - 2); const int p = (int) (s % period); i = p < total ? p : period - p; break; }
    case 5: i = rnd() % total; break;
    case 6: i = rnd() % total; if (total > 1 && i == arpLastPick) i = (i + 1 + rnd() % (total - 1)) % total; arpLastPick = i; break;
    default:
        if (a.shape == 0 && a.trShape && a.trRange > 0) {          /* the passes follow Serum's Transpose Shape; the keys inside one pass go up */
            const int R = a.trRange;
            const long k = s / m;
            int pass;
            if (a.trShape == 1) pass = R - (int) (k % (R + 1));
            else { const int q = (int) (k % (2 * R)); const int u = q <= R ? q : 2 * R - q; pass = a.trShape == 2 ? u : R - u; }
            i = pass * m + (int) (s % m);
        } else i = (int) (s % total);
        break;
    }
    const double st = (double) s * a.step;
    arpPlay(base[i % m] + (i / m) * a.trShift + a.transpose, vel[i % m], st + std::max(1.0, a.step * a.gate / 100.0));
}

void Engine::arpFire()
{
    auto a = std::atomic_load(&arpSp);
    if (!a || !arpRun) return;
    constexpr double eps = 1e-6;
    for (int i = 0; i < nAAct;) {
        if (aActEnd[i] < arpPos + eps) {
            noteOff(aAct[i]);
            aAct[i] = aAct[nAAct - 1]; aActEnd[i] = aActEnd[nAAct - 1]; nAAct--;
        } else i++;
    }
    const double to = arpPos + eps;
    if (to <= arpFiredTo) return;
    if (a->shape == 4) {
        int sorted[32];
        const int m = nAHeld;
        for (int i = 0; i < m; i++) sorted[i] = aHeld[i].note;
        std::sort(sorted, sorted + m);
        const long k0 = (long) std::floor(arpFiredTo / a->len), k1 = (long) std::floor(to / a->len);
        for (long k = k0; k <= k1; k++)
            for (int j = 0; j < a->n; j++) {
                const ArpNote &cn = a->notes[j];
                const double s = (double) k * a->len + cn.tick;
                if (s < arpFiredTo || s >= to || m <= 0) continue;
                const int q = (int) std::floor((double) cn.idx / m), r = cn.idx - q * m;
                const int qa = q < 0 ? -((-q) % (a->wrapRange + 1)) : q % (a->wrapRange + 1);
                arpPlay(sorted[r] + qa * a->wrapSemis + a->transpose, cn.vel, s + std::max(1.0, cn.dur * a->gate / 100.0));
            }
    } else {
        while ((double) arpNextStep * a->step < to) { arpStepNote(arpNextStep, *a); arpNextStep++; }
    }
    arpFiredTo = to;
}

int Engine::arpLimit(int frames)
{
    auto a = std::atomic_load(&arpSp);
    if (!a || !arpRun) return frames;
    const double tpf = clipTicksPerFrame();
    double next = 1e300;
    for (int i = 0; i < nAAct; i++) next = std::min(next, aActEnd[i]);
    if (a->shape == 4) {
        const double base = std::floor(arpFiredTo / a->len) * a->len;
        for (int j = 0; j < a->n; j++) {
            double s = base + a->notes[j].tick;
            if (s < arpFiredTo) s += a->len;
            next = std::min(next, s);
        }
    } else next = std::min(next, (double) arpNextStep * a->step);
    const double f = std::ceil((next - arpPos - 1e-6) / tpf);
    return f < (double) frames ? std::max(1, (int) f) : frames;
}

void Engine::arpAdvance(int frames)
{
    if (arpRun) arpPos += clipTicksPerFrame() * frames;
}

void Engine::onMidi(const uint8_t *msg, int len)
{
    if (len < 2) return;
    const uint8_t status = msg[0] & 0xF0;

    switch (status) {
    case 0x90:
        if (len >= 3 && msg[2] > 0) {
            if (arpActive() && arpInZone(msg[1])) arpKeyDown(msg[1], msg[2]);
            else if (clipActive()) clipKeyDown(msg[1]); else noteOn(msg[1], (float) msg[2] / 127.0f);
            break;
        }
        [[fallthrough]];
    case 0x80:
        if (arpActive() && arpKeyUp(msg[1])) break;
        if (clipActive()) clipKeyUp(msg[1]); else noteOff(msg[1]);
        break;
    case 0xB0:
        if (len >= 3) {
            switch (msg[1]) {
            case 1:  modWheel = (float) msg[2] / 127.0f; break;
            case 64: {
                bool down = msg[2] >= 64;
                if (sustainDown && !down) {
                    /* release everything that was sustained */
                    for (int n = 0; n < 128; n++) {
                        if (sustained[n]) {
                            sustained[n] = false;
                            for (auto &v : voices)
                                if (v.isActive() && !v.isReleasing() && v.currentNote() == n)
                                    v.stop(true);
                        }
                    }
                }
                sustainDown = down;
                break;
            }
            case 120: case 123:
                allNotesOff(msg[1] == 123);
                break;
            }
        }
        break;
    case 0xD0:
        aftertouch = (float) msg[1] / 127.0f;
        break;
    case 0xE0:
        if (len >= 3) {
            int v14 = (msg[2] << 7) | msg[1];
            pitchBendNorm = (float) (v14 - 8192) / 8192.0f;
        }
        break;
    }
}

/* The pots, in physical units. SPACE scales both mixes: 100 is the preset as programmed. */
/* The master volume is (pot/127)^2 times this: 0.8 as it always was, times +18 dB, which is the headroom
 * the converted Deep House sounds need to reach Serum's level (the output stage is the soft limiter
 * below it, so the extra gain is a ceiling it enforces, not something that can clip). */
static constexpr float kMasterMax = 0.8f * 7.943282f;

FxSettings Engine::fxSettings(const float *pots) const
{
    FxSettings s;
    const float space = std::min(pots[TB_P_FX_SPACE] / 100.0f, 1.35f);
    /* The switches: FX Enable gates all four, then each stage has its own. Off is a mix of 0 (the stage
     * fades out and goes quiet) or, for the EQ, a crossfade to the dry signal. */
    const bool all = pots[TB_P_FX_ON] >= 1.0f;
    const bool wideOn = all && pots[TB_P_WIDE_ON] >= 1.0f;
    const bool delayOn = all && pots[TB_P_DLY_ON] >= 1.0f;
    const bool revOn = all && pots[TB_P_REV_ON] >= 1.0f;
    s.eqEnable = all && pots[TB_P_EQ_ON] >= 1.0f;
    s.distDrive = all ? pot01(pots[TB_P_DIST_DRIVE]) : 0.0f;
    s.distMix = pot01(pots[TB_P_DIST_MIX]);
    s.hydVoices = all ? std::clamp(std::min((int) pots[TB_P_HYD_UNI], (int) this->pots[TB_P_UNI_CAP]), 1, 8) : 1;     /* Unison Cap: the slot's own, also for a bus rack */
    s.hydDetune = pot01(pots[TB_P_HYD_DET]);
    s.hydRate = pot01(pots[TB_P_HYD_RATE]);
    s.hydWet = all ? pot01(pots[TB_P_HYD_WET]) : 0.0f;
    s.distMode = std::clamp((int) pots[TB_P_DIST_MODE], 0, 6);
    s.distHold = 1 + (int) pots[TB_P_DIST_DRIVE];          /* mode 4 (Downsample): Drive is the hold length minus one (1..128 samples; calibration/dev/probe_dist_downsample.py) */
    s.distVital = pots[TB_P_DIST_CAL] >= 2.0f;
    s.distCal = pots[TB_P_DIST_CAL] >= 1.0f && !s.distVital;
    if (s.distVital) {
        /* Vital's distortion: Drive is the gain in dB (-30..+30), Mix a linear crossfade, and the filter's Cutoff a MIDI note (8..136) */
        s.distDriveDb = -30.0f + pots[TB_P_DIST_DRIVE] * (60.0f / 127.0f);
        s.distGain = std::pow(10.0f, s.distDriveDb / 20.0f);
        if (!all) s.distMix = 0.0f;
        s.distDrive = 1.0f;
        s.distFlt = std::clamp((int) pots[TB_P_DIST_FLT], 0, 2);
        s.distFltNote = 8.0f + pots[TB_P_DIST_FLT_CUT] * (128.0f / 127.0f);
        s.distFltRes = pots[TB_P_DIST_FLT_RES] / 127.0f;
        s.distFltBlend = pots[TB_P_DIST_FLT_BLEND] * (2.0f / 127.0f);
        s.distAudio = pots[TB_P_VDL_ON] >= 0.5f;
        if (s.distAudio) {
            s.distAudioTrans = (pots[TB_P_VDL_TRANS] - 64.0f) * 0.75f;
            const int amtP[4] = { TB_P_VDL_DRIVE, TB_P_VDL_CUT, TB_P_VDL_RES, TB_P_VDL_BLEND };
            for (int k = 0; k < 4; k++) s.distAudioAmt[k] = potBipolar(pots[amtP[k]]);
            for (int k = 0; k < 16; k++) s.distAudioShape[k] = pot01(pots[TB_P_VDL_S0 + k]);
        }
    }
    if (s.distCal) {
        /* Drive is the pre-gain in dB (-16..+54), Wet Gain the curve's output gain (-36..+12 dB), Level a gain on the sum (-24..+24 dB). Off with FX Enable. */
        s.distGain = std::pow(10.0f, (-16.0f + pots[TB_P_DIST_DRIVE] * (70.0f / 127.0f)) / 20.0f);
        s.distOut = std::pow(10.0f, (-36.0f + pots[TB_P_DIST_OUT] * (48.0f / 127.0f)) / 20.0f);
        s.distLevel = std::pow(10.0f, (pots[TB_P_DIST_LEVEL] - 64.0f) * (24.0f / 64.0f) / 20.0f);
        if (!all) s.distMix = 0.0f;
        /* the filter ahead of the curve: 0 off, 1 low-pass, 2 high-pass; Freq 20 Hz..20 kHz and Q 0.3..3, both log */
        s.distPre = std::clamp((int) pots[TB_P_DIST_PRE], 0, 2);
        s.distPreHz = 20.0f * std::pow(1000.0f, pots[TB_P_DIST_PRE_FREQ] / 127.0f);
        s.distPreQ = 0.3f * std::pow(10.0f, pots[TB_P_DIST_PRE_Q] / 127.0f);
    }
    s.splitBand = all ? std::clamp((int) pots[TB_P_SPLIT_BAND], 0, 4) : 0;
    s.splitStages = (int) pots[TB_P_SPLIT_STAGES] & 255;
    s.splitHz = 20.0f * std::pow(1000.0f, pots[TB_P_SPLIT_FREQ] / 127.0f);
    s.compMix = all && pots[TB_P_COMP_ON] >= 1.0f ? pot01(pots[TB_P_COMP_MIX]) : 0.0f;
    s.compThreshDb = -90.0f + 120.0f * pot01(pots[TB_P_COMP_THRESH]);      /* a level at the stage's input, before the master gain */
    s.compRatio = std::pow(100.0f, pot01(pots[TB_P_COMP_RATIO]));
    s.compKneeDb = 48.0f * pot01(pots[TB_P_COMP_KNEE]);
    s.compMulti = pots[TB_P_COMP_MULTI] >= 1.0f;
    s.compVital = pots[TB_P_COMP_MULTI] >= 2.0f;                       /* Comp Multiband = Vital: the Vital compressor's law (fx.h processCompVital) */
    if (s.compVital) {
        s.vcBands = std::clamp((int) pots[TB_P_VC_BANDS], 0, 3);
        const int ut[3] = { TB_P_VC_UT1, TB_P_VC_UT2, TB_P_VC_UT3 }, lt[3] = { TB_P_VC_LT1, TB_P_VC_LT2, TB_P_VC_LT3 };
        const int ur[3] = { TB_P_VC_UR1, TB_P_VC_UR2, TB_P_VC_UR3 }, lr[3] = { TB_P_VC_LR1, TB_P_VC_LR2, TB_P_VC_LR3 }, gn[3] = { TB_P_VC_G1, TB_P_VC_G2, TB_P_VC_G3 };
        for (int b = 0; b < 3; b++) {
            s.vcUpThrDb[b] = -100.0f + 112.0f * pot01(pots[ut[b]]);
            s.vcLoThrDb[b] = -100.0f + 112.0f * pot01(pots[lt[b]]);
            s.vcUpRatio[b] = pot01(pots[ur[b]]);
            s.vcLoRatio[b] = std::clamp((pots[lr[b]] - 64.0f) / 63.5f, -1.0f, 1.0f);
            const float g = pots[gn[b]];
            s.vcGainDb[b] = g >= 64.0f ? (g - 64.0f) / 63.0f * 30.0f : (g - 64.0f) / 64.0f * 30.0f;
        }
        s.vcAttack = pot01(pots[TB_P_VC_ATT]);
        s.vcRelease = pot01(pots[TB_P_VC_REL]);
    }
    s.compThreshBandDb[0] = s.compThreshDb;
    s.compThreshBandDb[1] = -90.0f + 120.0f * pot01(pots[TB_P_COMP_THRESH2]);
    s.compThreshBandDb[2] = -90.0f + 120.0f * pot01(pots[TB_P_COMP_THRESH3]);
    for (int b = 0; b < 3; b++) {
        const float g = pots[TB_P_COMP_GAIN1 + b];
        s.compBandGainDb[b] = g >= 64.0f ? (g - 64.0f) / 63.0f * 24.0f : (g - 64.0f) / 64.0f * 24.0f;
    }
    s.compBelow = pot01(pots[TB_P_COMP_BELOW]);
    s.fxOrder = std::clamp((int) pots[TB_P_FX_ORDER], 0, 5039);
    s.compBoostDb = std::clamp(pots[TB_P_COMP_BOOST], 0.0f, 48.0f);
    s.compAttackMs = 0.1f * std::pow(1000.0f, pot01(pots[TB_P_COMP_ATTACK]));
    s.compReleaseMs = 10.0f * std::pow(300.0f, pot01(pots[TB_P_COMP_RELEASE]));
    s.compMakeupDb = 24.0f * pot01(pots[TB_P_COMP_MAKEUP]);
    /* The modulated effects: rates are 0..20 Hz on a cubic knob, delays 0..30 ms, the mixes are gated by their own switches. */
    auto rateHz = [](float v) { const float x = pot01(v); return 20.0f * x * x * x; };
    s.chorMix = all && pots[TB_P_CHOR_ON] >= 1.0f ? pot01(pots[TB_P_CHOR_MIX]) : 0.0f;
    s.chorRate = rateHz(pots[TB_P_CHOR_RATE]);
    s.chorD1Ms = 30.0f * pot01(pots[TB_P_CHOR_D1]);
    s.chorD2Ms = 30.0f * pot01(pots[TB_P_CHOR_D2]);
    s.chorDepth = pot01(pots[TB_P_CHOR_DEPTH]);
    s.chorFb = 0.8f * pot01(pots[TB_P_CHOR_FB]);
    s.chorFiltHz = potEqFreq(pots[TB_P_CHOR_FILT]);
    s.chorHpf = pots[TB_P_CHOR_FMODE] >= 1.0f;
    s.chorVital = pots[TB_P_CHOR_VITAL] >= 1.0f;
    if (s.chorVital) {                                    /* Vital's chorus: Delay 1 / 2 are log2 seconds, -10..-5.64 over the pot; Feedback bipolar; the loop filter a MIDI note and a spread */
        s.chorD1Ms = 1000.0f * std::pow(2.0f, -10.0f + 4.35614f * pot01(pots[TB_P_CHOR_D1]));
        s.chorD2Ms = 1000.0f * std::pow(2.0f, -10.0f + 4.35614f * pot01(pots[TB_P_CHOR_D2]));
        s.chorFb = 0.95f * (2.0f * pot01(pots[TB_P_CHOR_FB]) - 1.0f);
        s.chorVoices = std::clamp((int) (pots[TB_P_CHOR_VOICES] + 0.5f), 1, 4);
        s.chorVCutNote = 8.0f + 128.0f * pot01(pots[TB_P_CHOR_VCUT]);
        s.chorVSpread = pot01(pots[TB_P_CHOR_VSPREAD]);
    }
    s.flanMix = all && pots[TB_P_FLAN_ON] >= 1.0f ? pot01(pots[TB_P_FLAN_MIX]) : 0.0f;
    s.flanRate = rateHz(pots[TB_P_FLAN_RATE]);
    s.flanDepth = pot01(pots[TB_P_FLAN_DEPTH]);
    s.flanFb = 0.8f * pot01(pots[TB_P_FLAN_FB]);
    s.flanPhase = pot01(pots[TB_P_FLAN_PHASE]);
    s.phasMix = all && pots[TB_P_PHAS_ON] >= 1.0f ? pot01(pots[TB_P_PHAS_MIX]) : 0.0f;
    s.phasRate = rateHz(pots[TB_P_PHAS_RATE]);
    s.phasDepth = pot01(pots[TB_P_PHAS_DEPTH]);
    s.phasDepth2 = pot01(pots[TB_P_PHAS_DEPTH2]);
    s.phasFreqHz = potEqFreq(pots[TB_P_PHAS_FREQ]);
    s.phasFb = 0.8f * pot01(pots[TB_P_PHAS_FB]);
    s.phasPhase = pot01(pots[TB_P_PHAS_PHASE]);
    s.flanVital = pots[TB_P_FLAN_VITAL] >= 1.0f;
    if (s.flanVital) {                                    /* Vital's flanger: Feedback bipolar, the centre a MIDI note (pot 0..127 = 8..136) */
        s.flanFb = 2.0f * pot01(pots[TB_P_FLAN_FB]) - 1.0f;
        s.flanVCenterNote = 8.0f + 128.0f * pot01(pots[TB_P_FLAN_VCENTER]);
    }
    s.phasVital = pots[TB_P_PHAS_VITAL] >= 1.0f;
    if (s.phasVital) {                                    /* Vital's phaser: Feedback -1..1 over the pot (the resonance is its positive part), the centre a MIDI note, Blend 0..2 */
        s.phasFb = 2.0f * pot01(pots[TB_P_PHAS_FB]) - 1.0f;
        s.phasVCenterNote = 8.0f + 128.0f * pot01(pots[TB_P_PHAS_VCENTER]);
        s.phasVBlend = 2.0f * pot01(pots[TB_P_PHAS_VBLEND]);
    }
    s.phasPoles = (int) pots[TB_P_PHAS_POLES];
    s.wideMix = wideOn ? pot01(pots[TB_P_FX_WIDE]) : 0.0f;
    s.wideRate = 0.05f * std::pow(100.0f, pot01(pots[TB_P_FX_WRATE]));
    s.wideDepth = pot01(pots[TB_P_FX_WDEPTH]);

    s.delayMix = delayOn ? std::min(pot01(pots[TB_P_DLY_MIX]) * space, 1.0f) : 0.0f;
    const float bpm = hostBpm >= 20.0f ? std::clamp(hostBpm, 40.0f, 300.0f) : 120.0f;       /* no tempo from the host (0): 120 */
    const float tempoScale = pots[TB_P_DLY_SYNC] >= 1.0f ? 120.0f / bpm : 1.0f;                /* the times are a 120 BPM beat's */
    s.delayMs = std::pow(1500.0f, pot01(pots[TB_P_DLY_TIME])) * tempoScale;
    s.delayRMs = std::pow(1500.0f, pot01(pots[TB_P_DLY_TIMER])) * tempoScale;
    s.delayFb = std::min(pot01(pots[TB_P_DLY_FB]), 0.97f);
    s.delayLoHz = potEqFreq(pots[TB_P_DLY_LO]);
    s.delayHiHz = potEqFreq(pots[TB_P_DLY_HI]);
    s.delayPing = (int) pots[TB_P_DLY_MODE] == 1;
    s.delay2Mix = delayOn ? std::min(pot01(pots[TB_P_DLY2_MIX]) * space, 1.0f) : 0.0f;
    s.delay2Ms = std::pow(1500.0f, pot01(pots[TB_P_DLY2_TIME])) * tempoScale;
    s.delay2RMs = std::pow(1500.0f, pot01(pots[TB_P_DLY2_TIMER])) * tempoScale;
    s.delay2Fb = std::min(pot01(pots[TB_P_DLY2_FB]), 0.97f);
    s.delay2LoHz = potEqFreq(pots[TB_P_DLY2_LO]);
    s.delay2HiHz = potEqFreq(pots[TB_P_DLY2_HI]);
    s.delay2Ping = (int) pots[TB_P_DLY2_MODE] == 1;

    s.revMix = revOn ? std::min(pot01(pots[TB_P_REV_MIX]) * space, 1.0f) : 0.0f;
    s.convMix = revOn ? std::min(pot01(pots[TB_P_CONV_MIX]) * space, 1.0f) : 0.0f;
    s.revT60 = 0.2f * std::pow(150.0f, pot01(pots[TB_P_REV_DECAY]));
    s.revDampHz = potEqFreq(pots[TB_P_REV_DAMP]);
    s.revLoHz = potEqFreq(pots[TB_P_REV_LO]);
    s.revWidth = pot01(pots[TB_P_REV_WIDTH]);
    s.revVital = pots[TB_P_REV_VITAL] >= 1.0f;
    s.revVDelayS = 0.3f * pot01(pots[TB_P_REV_PREDELAY]);                 /* the pre-delay is shared: Serum's reverb takes it too (0 leaves it as it was) */
    if (s.revVital) {                                     /* Vital's reverb shares Decay, Damp and Lo cut with Serum's: Decay is the T60, Damp the top of the input band (Vital's Pre High), Lo cut its bottom (Pre Low) */
        auto note = [](float hz) { return 69.0f + 12.0f * std::log2(std::max(hz, 1.0f) / 440.0f); };
        s.revVDecay = s.revT60;
        s.revVSize = pot01(pots[TB_P_REV_VSIZE]);
        s.revVPreLo = note(s.revLoHz);
        s.revVPreHi = note(s.revDampHz);
        s.revVLoNote = 130.0f * pot01(pots[TB_P_REV_VLOCUT]);
        s.revVLoDb = -24.0f * pot01(pots[TB_P_REV_VLOGAIN]);
        s.revVHiNote = 130.0f * pot01(pots[TB_P_REV_VHICUT]);
        s.revVHiDb = -24.0f * pot01(pots[TB_P_REV_VHIGAIN]);
        s.revVChorus = pot01(pots[TB_P_REV_VCHORUS]);
        s.revVChorusHz = std::pow(2.0f, -8.0f + 11.0f * pot01(pots[TB_P_REV_VCHORUSF]));
    }

    for (int b = 0; b < 4; b++) {
        const int base = TB_P_EQ1_TYPE + b * 4;
        const int type = std::clamp((int) pots[base], 0, 5);
        s.band[b].type = type;
        s.band[b].hz = potEqFreq(pots[base + 1]);
        s.band[b].gainDb = potEqGain(pots[base + 2]);
        s.band[b].q = potEqQ(type, pots[base + 3]);
    }
    /* the SplitMS branches' bands: the params are in pairs of bands (type, freq, gain, q of band 1 and 2, then of 3 and 4) */
    for (int k = 0; k < 2; k++) {
        const int first = k == 0 ? TB_P_EQM1_TYPE : TB_P_EQS1_TYPE;
        for (int b = 0; b < 4; b++) {
            const int at = first + (b / 2) * 8 + (b % 2);
            const int type = std::clamp((int) pots[at], 0, 5);
            s.msBand[k][b].type = type;
            s.msBand[k][b].hz = potEqFreq(pots[at + 2]);
            s.msBand[k][b].gainDb = potEqGain(pots[at + 4]);
            s.msBand[k][b].q = potEqQ(type, pots[at + 6]);
        }
    }
    return s;
}

void Engine::refreshFx()
{
    /* The FX pots are one contiguous run, so a moved one is a memcmp away. */
    const float tempoNow = pots[TB_P_DLY_SYNC] >= 1.0f ? hostBpm : 0.0f;
    if (fxSeenValid && tempoSeen == tempoNow && std::memcmp(fxSeen, pots + TB_P_DLY_MIX, sizeof fxSeen) == 0 &&
        std::memcmp(distSeen, pots + TB_P_DIST_DRIVE, sizeof distSeen) == 0 && compOnSeen == pots[TB_P_COMP_ON] &&
        std::memcmp(modSeen, pots + TB_P_CHOR_ON, sizeof modSeen) == 0 && std::memcmp(hydSeen, pots + TB_P_HYD_UNI, sizeof hydSeen) == 0 && std::memcmp(dly2Seen, pots + TB_P_DLY2_MIX, sizeof dly2Seen) == 0 && std::memcmp(distPreSeen, pots + TB_P_DIST_PRE, sizeof distPreSeen) == 0 &&
        std::memcmp(vitalFxSeen, pots + TB_P_VC_UT1, sizeof vitalFxSeen) == 0 && std::memcmp(vdistSeen, pots + TB_P_FX_ORDER, sizeof vdistSeen) == 0 && std::memcmp(msSeen, pots + TB_P_EQM1_TYPE, sizeof msSeen) == 0 && convSeen == pots[TB_P_CONV_MIX] && std::memcmp(splitSeen, pots + TB_P_SPLIT_BAND, sizeof splitSeen) == 0) return;
    convSeen = pots[TB_P_CONV_MIX];
    tempoSeen = tempoNow;
    std::memcpy(fxSeen, pots + TB_P_DLY_MIX, sizeof fxSeen);
    std::memcpy(distSeen, pots + TB_P_DIST_DRIVE, sizeof distSeen);
    compOnSeen = pots[TB_P_COMP_ON];
    std::memcpy(modSeen, pots + TB_P_CHOR_ON, sizeof modSeen);
    std::memcpy(hydSeen, pots + TB_P_HYD_UNI, sizeof hydSeen);
    std::memcpy(dly2Seen, pots + TB_P_DLY2_MIX, sizeof dly2Seen);
    std::memcpy(distPreSeen, pots + TB_P_DIST_PRE, sizeof distPreSeen);
    std::memcpy(vdistSeen, pots + TB_P_FX_ORDER, sizeof vdistSeen);
    std::memcpy(vitalFxSeen, pots + TB_P_VC_UT1, sizeof vitalFxSeen);
    std::memcpy(msSeen, pots + TB_P_EQM1_TYPE, sizeof msSeen);
    std::memcpy(splitSeen, pots + TB_P_SPLIT_BAND, sizeof splitSeen);
    fxSeenValid = true;
    fx.set(fxSettings(pots));
}

/* A route from an LFO or envelope to the Level Out knob of the Utils that ends a rack. Serum adds depth x source to the knob (0..1, stored 0.5 = unity, stopping at both ends: calibration/dev/probe_utils_level_route.py)
 * and the gain is (knob / 0.5)^2 (probe_utils_level.py: -12 dB at 0.25, +12 dB at 1.0, -28 dB at 0.1). The converter carries it when the stored knob is its default, so the gain here is (2 x (base + m))^2 with the base the rack's util_out pot / 127 (0 = no route).
 * The gain is ramped across the block so a fast pump does not step. */
void Engine::applyUtilOut(int rack, float *l, float *r, int n)
{
    const float *pp = rack == 0 ? pots : busPots[rack - 1];
    const float base = pp[TB_P_UTIL_OUT];
    if (base < 0.5f) {                                  /* no route on this rack's Utils */
        if (utilOutCur[rack] == 1.0f) return;
    }
    const float knob = std::clamp(base / 127.0f + utilOutMod[rack], 0.0f, 1.0f);
    const float target = base < 0.5f ? 1.0f : 4.0f * knob * knob;
    float g = utilOutCur[rack];
    if (g == 1.0f && target == 1.0f) return;
    const float step = (target - g) / (float) n;       /* 0 once the gain has settled: a steady gain is still applied */
    for (int i = 0; i < n; i++) { g += step; l[i] *= g; r[i] *= g; }
    utilOutCur[rack] = target;
}

void Engine::refreshBus(int b)
{
    /* The bus rack's pots, with the main rack's FX on / Space in their places: compared where they lie (the two taken from the main pots on their own), and
     * copied only when something moved, rather than copying the whole set to compare it on every call. */
    constexpr int kLo = TB_P_FX_ON < TB_P_FX_SPACE ? TB_P_FX_ON : TB_P_FX_SPACE, kHi = TB_P_FX_ON < TB_P_FX_SPACE ? TB_P_FX_SPACE : TB_P_FX_ON;
    const float *bp = busPots[b];
    const float *seen = busSeen[b];
    const float fxOn = pots[TB_P_FX_ON], fxSpace = pots[TB_P_FX_SPACE];
    const float tempoNow = (TB_P_DLY_SYNC == TB_P_FX_ON ? fxOn : TB_P_DLY_SYNC == TB_P_FX_SPACE ? fxSpace : bp[TB_P_DLY_SYNC]) >= 1.0f ? hostBpm : 0.0f;
    const auto same = [&](int from, int to) { return to <= from || std::memcmp(seen + from, bp + from, sizeof(float) * (size_t) (to - from)) == 0; };
    if (busSeenValid[b] && busTempoSeen[b] == tempoNow && std::memcmp(&seen[TB_P_FX_ON], &fxOn, sizeof fxOn) == 0 && std::memcmp(&seen[TB_P_FX_SPACE], &fxSpace, sizeof fxSpace) == 0 &&
        same(0, kLo) && same(kLo + 1, kHi) && same(kHi + 1, TB_PARAM_COUNT)) return;
    float p[TB_PARAM_COUNT];
    std::memcpy(p, bp, sizeof p);
    p[TB_P_FX_ON] = fxOn;
    p[TB_P_FX_SPACE] = fxSpace;
    std::memcpy(busSeen[b], p, sizeof p);
    busTempoSeen[b] = tempoNow;
    busSeenValid[b] = true;
    fxBus[b].set(fxSettings(p));
}

/* Shelf knobs: 64 is flat, the ends are +/-12 dB. */
static float potShelfDb(float v)
{
    return v >= 64.0f ? (v - 64.0f) / 63.0f * 12.0f : (v - 64.0f) / 64.0f * 12.0f;
}

void Engine::refreshTone()
{
    const float lo = potShelfDb(pots[TB_P_LO_SHELF]), hi = potShelfDb(pots[TB_P_HI_SHELF]);
    if (toneValid && lo == toneLoDb && hi == toneHiDb) return;
    /* A shelf at exactly 0 dB is skipped, so a neutral pair passes the signal bit for bit. Coming back
     * from it the filter starts from silence, which is inaudible for the first step of the knob (0.2 dB). */
    loActive = lo != 0.0f;
    hiActive = hi != 0.0f;
    if (loActive) loShelf.c.set(EQ_LOSHELF, 150.0f, lo, 0.7071f, 44100.0f); else loShelf.clear();
    if (hiActive) hiShelf.c.set(EQ_HISHELF, 5000.0f, hi, 0.7071f, 44100.0f); else hiShelf.clear();
    toneLoDb = lo; toneHiDb = hi; toneValid = true;
}

void Engine::refreshStereo()
{
    const float mono = pots[TB_P_MONOHZ];
    if (mono == monoSeen) return;
    monoSeen = mono;
    monoActive = mono >= 1.0f;
    if (monoActive) {
        const float hz = 40.0f * std::pow(12.5f, (mono - 1.0f) / 126.0f);        /* 40 Hz .. 500 Hz */
        sideHp.c.set(EQ_HIPASS, hz, 0.0f, 0.7071f, 44100.0f);
    } else {
        sideHp.clear();
    }
}

/* An effects rack asleep. A rack on digital silence still costs its full price (the convolver's FFTs, the reverb's lines, the chorus, the multiband compressor: about
 * 180 us of the 2667 us frame per idle slot on the Move), and a slot nobody is playing is silent most of the time. So once both what goes into the rack and what comes
 * out of it have stayed under -120 dBFS (a sixteenth of a 16-bit step) for longer than any effect's memory here (a delay's 3 s, the convolver's impulse response: `hold` is 3.5 s, or the IR's length and one second when the rack has one), nothing is
 * left inside it that could still be heard, and it is skipped until its input is above that again. The rack's state is silence by then, so waking it is seamless. */
static void gatedFx(Fx &f, long &silent, long hold, float *L, float *R, int n)
{
#ifdef TB_NO_FX_GATE                                  /* the comparison builds (null tests) run the rack always */
    f.process(L, R, n); (void) silent; return;
#endif
    constexpr float kSilence = 1e-6f;
    auto peak = [n](const float *a, const float *b) { float m = 0.0f; for (int i = 0; i < n; i++) m = std::max(m, std::max(std::fabs(a[i]), std::fabs(b[i]))); return m; };
    const float pin = peak(L, R);
    if (silent >= hold && pin < kSilence) return;
    f.process(L, R, n);
    silent = (pin < kSilence && peak(L, R) < kSilence) ? std::min(silent + (long) n, hold) : 0;
}

void Engine::renderBlock(int16_t *out, int frames)
{
    Tables t = loadTables();

    float L[kBlock], R[kBlock];

    int done = 0;
    while (done < frames) {
        int n = std::min(frames - done, kBlock);
        clipSync();
        clipFire();
        n = clipLimit(n);
        arpSync();
        arpFire();
        n = arpLimit(n);
        demoSync();
        demoFire();
        n = demoLimit(n);
        std::memset(L, 0, (size_t) n * sizeof(float));
        std::memset(R, 0, (size_t) n * sizeof(float));

        VoiceContext c = makeContext(t);
        /* The FX buses are fed only while some source sends to them (the send pots are in pairs, bus 1 then bus 2). */
        bool busLive[2] = {};
        for (int k = 0; k < 8; k++)
            for (int b = 0; b < 2; b++)
                busLive[b] = busLive[b] || pots[TB_P_WT1_BUS1 + 2 * k + b] > 0.0f;
        for (int b = 0; b < 2; b++)
            if (busLive[b]) {
                std::memset(busBuf[2 * b], 0, (size_t) n * sizeof(float));
                std::memset(busBuf[2 * b + 1], 0, (size_t) n * sizeof(float));
                c.bus[2 * b] = busBuf[2 * b];
                c.bus[2 * b + 1] = busBuf[2 * b + 1];
            }
        std::memset(dryBuf[0], 0, (size_t) n * sizeof(float));
        std::memset(dryBuf[1], 0, (size_t) n * sizeof(float));
        c.dry[0] = dryBuf[0]; c.dry[1] = dryBuf[1];
        for (auto &v : voices)
            if (v.isActive())
                v.render(c, L, R, n);

        for (int l = 0; l < kLfos; l++) {               /* the Free LFOs run on whether or not a note is down */
            lfoFree[l] += lfoHz(pots, l, hostBpm) * (float) n / 44100.0f;
            lfoFree[l] -= std::floor(lfoFree[l]);
        }

        refreshFx();
        {                                               /* the Filter FX follows the filter envelope of the loudest voice */
            const bool on = pots[TB_P_FXF_ON] >= 0.5f && pots[TB_P_FX_ON] >= 0.5f;
            float env = 0.0f;
            for (auto &v : voices)
                if (v.isActive()) env = std::max(env, v.filterEnvelope());
            const float filterWidth = 12.0f * std::log2(20000.0f / 440.0f) + 69.0f;
            /* an LFO (or spare envelope) route to the Filter FX cutoff lives in the voices' modulation; the effect is shared, so it follows the newest voice */
            float fxfMod = 0.0f, fxfWet = 0.0f;
            uint32_t newest = 0;
            for (auto &v : voices)
                if (v.isActive() && v.serialNumber() >= newest) { newest = v.serialNumber(); fxfMod = v.fxfCutoffMod(); fxfWet = v.fxfWetMod(); }
            /* Vital's Filter FX Key Track: the cutoff moves by the track amount x (the last played note - 60) semitones (0 = off, the pot's middle) */
            const float fxfKey = std::clamp((pots[TB_P_FXF_KEY] - 64.0f) / 63.0f, -1.0f, 1.0f) * (lastPlayedNote >= 0.0f ? lastPlayedNote - 60.0f : 0.0f);
            const float note = potFilterNote(pots[TB_P_FXF_FREQ]) + env * potBipolar(pots[TB_P_FXF_ENV]) * filterWidth + fxfMod * filterWidth + fxfKey;
            const float hz = std::clamp(midiNoteToHz(note), 4.0f, 20000.0f);
            const float res = std::clamp(pots[TB_P_FXF_RES] / 127.0f, 0.0f, 1.0f);
            fx.setFilter(on, (int) pots[TB_P_FXF_TYPE], hz, kFilterQ / (1.0f - res * 0.99f), pots[TB_P_FXF_WET] / 127.0f + fxfWet, pots[TB_P_FXF_DRIVE] / 127.0f, res, pots[TB_P_FLT_VDL] >= 0.5f);
            {                                           /* an envelope / LFO route to the Distortion's Wet: the newest voice's, like the Filter FX cutoff above */
                float dm = 0.0f;
                uint32_t newestD = 0;
                for (auto &v : voices)
                    if (v.isActive() && v.serialNumber() >= newestD) { newestD = v.serialNumber(); dm = v.distWetMod(); }
                fx.setDistWetMod(dm);
                float cm = 0.0f;
                uint32_t newestC = 0;
                for (auto &v : voices)
                    if (v.isActive() && v.serialNumber() >= newestC) { newestC = v.serialNumber(); cm = v.compWetMod(); }
                fx.setCompWetMod(cm);
                uint32_t newestU = 0;
                for (auto &v : voices)
                    if (v.isActive() && v.serialNumber() >= newestU) { newestU = v.serialNumber(); for (int r = 0; r < 3; r++) utilOutMod[r] = v.utilOutMod(r); }       /* with no voice sounding it keeps its last offsets: a bus tail stays at the pump's end level */
                uint32_t newestE = 0;
                for (auto &v : voices)
                    if (v.isActive() && v.serialNumber() >= newestE) { newestE = v.serialNumber(); for (int i = 0; i < 8; i++) eqModNow[i] = v.eqMod(i); }
                uint32_t newestV = 0;
                for (auto &v : voices)
                    if (v.isActive() && v.serialNumber() >= newestV) { newestV = v.serialNumber(); for (int i = 0; i < 4; i++) distVModNow[i] = v.distVMod(i); }
                fx.setVitalDistMod(distVModNow);
                {                                       /* the audio-rate distortion LFO follows the newest voice's note */
                    uint32_t newestN = 0;
                    for (auto &v : voices)
                        if (v.isActive() && v.serialNumber() >= newestN && v.currentNote() >= 0) { newestN = v.serialNumber(); fx.setVitalAudioNote((float) v.currentNote()); }
                }
                fx.setEqMod(eqModNow);                  /* the master EQ's bands moved by an envelope / LFO route (the newest voice's, kept while none sounds) */
            }
            /* The buses' racks have the same Filter FX (the "b1.fxf_*" / "b2.fxf_*" keys), moved by the same envelope: Lead - Other Side's bus 1 filters
             * its distortion and the EQs behind it boost the highs it takes away. */
            for (int b = 0; b < 2; b++) {
                const float *bp = busPots[b];
                const bool bon = bp[TB_P_FXF_ON] >= 0.5f && pots[TB_P_FX_ON] >= 0.5f;
                if (!bon && !fxBus[b].filterOn()) continue;
                const float bnote = potFilterNote(bp[TB_P_FXF_FREQ]) + env * potBipolar(bp[TB_P_FXF_ENV]) * filterWidth;
                const float bres = std::clamp(bp[TB_P_FXF_RES] / 127.0f, 0.0f, 1.0f);
                fxBus[b].setFilter(bon, (int) bp[TB_P_FXF_TYPE], std::clamp(midiNoteToHz(bnote), 4.0f, 20000.0f), kFilterQ / (1.0f - bres * 0.99f), bp[TB_P_FXF_WET] / 127.0f, bp[TB_P_FXF_DRIVE] / 127.0f, bres, bp[TB_P_FLT_VDL] >= 0.5f);
            }
        }
        for (int b = 0; b < 2; b++) {                   /* the buses' racks: their output joins the main path ahead of the main rack (probe: a Filter FX in the main rack filters what a bus returns) */
            if (!busLive[b] && !fxBus[b].active()) continue;
            if (!busLive[b]) {
                std::memset(busBuf[2 * b], 0, (size_t) n * sizeof(float));
                std::memset(busBuf[2 * b + 1], 0, (size_t) n * sizeof(float));
            }
            refreshBus(b);
            if (fxBus[b].active())
                gatedFx(fxBus[b], fxSilent[1 + b], 7 * 44100 / 2, busBuf[2 * b], busBuf[2 * b + 1], n);
            /* The rack's Utils width: the side signal of what the bus returns, scaled (Serum's Utils node sits inside the rack; Lead - Rollin's bus 1 narrows to 23 %). */
            const float bwT = busPots[b][TB_P_MWIDTH] / 64.0f;
            if (bwT != 1.0f || busWidthCur[b] != 1.0f) {
                float *bl = busBuf[2 * b], *br = busBuf[2 * b + 1];
                for (int i = 0; i < n; i++) {
                    busWidthCur[b] += (bwT - busWidthCur[b]) * 0.002f;
                    if (std::fabs(bwT - busWidthCur[b]) < 1e-5f) busWidthCur[b] = bwT;
                    const float m = 0.5f * (bl[i] + br[i]), sd = 0.5f * (bl[i] - br[i]) * busWidthCur[b];
                    bl[i] = m + sd; br[i] = m - sd;
                }
            }
            applyUtilOut(1 + b, busBuf[2 * b], busBuf[2 * b + 1], n);
            for (int i = 0; i < n; i++) { L[i] += busBuf[2 * b][i]; R[i] += busBuf[2 * b + 1][i]; }
        }
        fx.setConvPlan(std::atomic_load(&convPlan));
        if (fx.active())
        {
            const auto cp = std::atomic_load(&convPlan);
            const long irSamples = cp ? (long) convdetail::kHeadTaps + (long) cp->tailParts * convdetail::kD : 0;
            gatedFx(fx, fxSilent[0], std::max(7L * 44100 / 2, irSamples + 44100), L, R, n);
        }
        applyUtilOut(0, L, R, n);
        for (int i = 0; i < n; i++) { L[i] += dryBuf[0][i]; R[i] += dryBuf[1][i]; }          /* Out: past the effects */

        refreshTone();
        if (loActive || hiActive) {
            for (int i = 0; i < n; i++) {
                float l = L[i], r = R[i];
                if (loActive) { l = loShelf.tick(0, l); r = loShelf.tick(1, r); }
                if (hiActive) { l = hiShelf.tick(0, l); r = hiShelf.tick(1, r); }
                L[i] = l; R[i] = r;
            }
        }

        refreshStereo();
        const float widthT = pots[TB_P_MWIDTH] / 64.0f;
        if (widthT != 1.0f || widthCur != 1.0f || monoActive) {
            for (int i = 0; i < n; i++) {
                widthCur += (widthT - widthCur) * 0.002f;
                if (std::fabs(widthT - widthCur) < 1e-5f) widthCur = widthT;
                const float m = 0.5f * (L[i] + R[i]);
                float sd = 0.5f * (L[i] - R[i]) * widthCur;
                if (monoActive) sd = sideHp.tick(0, sd);
                L[i] = m + sd; R[i] = m - sd;
            }
        }

        const float master = potSquared(pots[TB_P_VOLUME]) * kMasterMax;
        const float driveT = pot01(pots[TB_P_DRIVE]);
        for (int i = 0; i < n; i++) {
            float l = L[i] * master, r = R[i] * master;
            /* Drive: a blend from the signal to a tanh of it, so 0 is the signal itself and there is no
             * step as the knob leaves 0. The gain into the tanh is 1..8 and it is scaled by 1/sqrt(gain),
             * which keeps the level near where it was while the peaks round off. */
            driveCur += (driveT - driveCur) * 0.004f;
            if (driveT == 0.0f && driveCur < 1e-4f) driveCur = 0.0f;
            if (driveCur > 0.0f) {
                const float g = 1.0f + 7.0f * driveCur, k = 1.0f / std::sqrt(g);
                l += driveCur * (fastTanh(g * l) * k - l);
                r += driveCur * (fastTanh(g * r) * k - r);
            }
            l = softLimit(l);
            r = softLimit(r);
            /* To 16 bits with TPDF dither (two uniform values, +-1 LSB) and rounding. Truncating, as this did, turns a quiet tail into
             * correlated quantisation distortion: a grainy sizzle as a note dies away. Digital silence stays exactly 0. */
            if (l == 0.0f && r == 0.0f) {
                out[(done + i) * 2 + 0] = 0;
                out[(done + i) * 2 + 1] = 0;
            } else {
                ditherState ^= ditherState << 13; ditherState ^= ditherState >> 17; ditherState ^= ditherState << 5;
                const float d1 = (float) (ditherState & 0xFFFF) * (1.0f / 65536.0f);
                ditherState ^= ditherState << 13; ditherState ^= ditherState >> 17; ditherState ^= ditherState << 5;
                const float d2 = (float) (ditherState & 0xFFFF) * (1.0f / 65536.0f);
                const float dl = d1 - d2;
                ditherState ^= ditherState << 13; ditherState ^= ditherState >> 17; ditherState ^= ditherState << 5;
                const float d3 = (float) (ditherState & 0xFFFF) * (1.0f / 65536.0f);
                const float dr = d2 - d3;
                const float ql = std::min(std::max(std::nearbyint(l * 32767.0f + dl), -32768.0f), 32767.0f);
                const float qr = std::min(std::max(std::nearbyint(r * 32767.0f + dr), -32768.0f), 32767.0f);
                out[(done + i) * 2 + 0] = (int16_t) ql;
                out[(done + i) * 2 + 1] = (int16_t) qr;
            }
        }
        clipAdvance(n);
        arpAdvance(n);
        demoAdvance(n);
        done += n;
    }
}

std::shared_ptr<Wavetable> makeInitTable()
{
    /* 64 frames x 2048: pulse morphing 50% -> 6% duty with a saw underlay.
     * Naive shapes on purpose — wtBuild band-limits every level. */
    const int F = 64, S = 2048;
    std::vector<float> data((size_t) F * S);
    for (int f = 0; f < F; f++) {
        float m = (float) f / (F - 1);
        float pw = 0.5f - 0.44f * m;
        for (int i = 0; i < S; i++) {
            float ph = (float) i / S;
            float pulse = ph < pw ? 1.0f : -1.0f;
            float saw = 2.0f * ph - 1.0f;
            data[(size_t) f * S + i] = 0.8f * ((1.0f - m * 0.5f) * pulse * 0.6f +
                                               m * 0.4f * saw);
        }
    }
    /* normalize the source to 0.9 peak so the default patch lands hot */
    float peak = 0.0f;
    for (float v : data) peak = std::max(peak, std::fabs(v));
    if (peak > 0.0f)
        for (float &v : data) v *= 0.9f / peak;

    auto wt = std::make_shared<Wavetable>();
    wtBuild(*wt, data.data(), F, S, 44100.0f, 44100.0f);
    return wt;
}

} // namespace tb
