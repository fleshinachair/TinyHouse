/* Tablor engine — voice allocation, poly/mono, glide, MIDI, master out.
 * Owns the wavetables, the analog tables and the 8 voices.
 */
#pragma once

#include "fx.h"
#include "voice.h"

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <memory>
#include <vector>

namespace tb {

/* A note pattern the engine plays when a key goes down (Serum's Clip Player): the notes loop at the host tempo, transposed by the
 * key's distance from middle C (60). Times are in ticks of 1/96 beat. */
inline constexpr int kClipMax = 128;
struct ClipNote { int tick, dur; uint8_t note, vel; };
struct Clip {
    bool mono = false;              /* Serum's Mono clip mode: one pattern at a time, played from the last key held */
    int len = 0;                    /* the loop, ticks */
    int n = 0;
    ClipNote notes[kClipMax];
};

/* The state's "clip" value: "len[:m]|tick,dur,note,vel|tick,dur,note,vel|..." (ticks of 1/96 beat; ":m" for the Mono clip mode). Null if it holds no note. */
inline std::shared_ptr<Clip> parseClip(const char *s)
{
    if (!s || !*s) return nullptr;
    auto c = std::make_shared<Clip>();
    char *e = nullptr;
    c->len = (int) strtol(s, &e, 10);
    if (e && *e == ':') { c->mono = e[1] == 'm'; e += 2; }
    while (e && *e == '|' && c->n < kClipMax) {
        ++e;
        long v[4];
        for (int i = 0; i < 4; i++) {
            v[i] = strtol(e, &e, 10);
            if (i < 3) { if (*e != ',') return c->n > 0 && c->len > 0 ? c : nullptr; ++e; }
        }
        c->notes[c->n++] = { (int) v[0], (int) v[1], (uint8_t) std::clamp(v[2], 0L, 127L), (uint8_t) std::clamp(v[3], 1L, 127L) };
    }
    return c->n > 0 && c->len > 0 ? c : nullptr;
}

/* Serum's arpeggiator: the held keys played one at a time (or, for the Pattern shape, a stored pattern of key indexes) at a tempo-synced step. Ticks of 1/96 beat.
 * The state's "arp" value: "shape,step,gate,trRange,trShift,transpose,wrapRange,wrapSemis,zoneMin,zoneMax,len|tick,dur,idx,vel|..." (the pattern part only for shape 4). */
struct ArpNote { int tick, dur; int idx; uint8_t vel; };
struct Arp {
    int shape = 0;                  /* 0 Up, 1 Down, 2 UpDown, 3 Played (press order), 4 Pattern, 5 random, 6 random without a repeat */
    int step = 24;                  /* one step, ticks */
    int gate = 100;                 /* note length, % of the step (of the pattern note) */
    int trRange = 0, trShift = 0;   /* the held notes repeat trRange more times, each trShift semitones above the one before */
    int trShape = 0;                /* the order of those passes (Serum's Transpose Shape, probed: Up 0,1..R; Down R..0; UpDown 0..R..1; DownUp R..0..1): 0 Up, 1 Down, 2 UpDown, 3 DownUp. In the state it rides in the shape field as shape + 8 * trShape */
    int transpose = 0;              /* everything, semitones */
    int wrapRange = 0, wrapSemis = 12;   /* a pattern index past the held keys wraps round them, up to wrapRange times, wrapSemis higher each time */
    int zoneMin = 0, zoneMax = 127; /* keys outside play as they are */
    int len = 0, n = 0;             /* the pattern's loop, ticks, and its notes */
    ArpNote notes[kClipMax];
};

inline std::shared_ptr<Arp> parseArp(const char *s)
{
    if (!s || !*s) return nullptr;
    auto a = std::make_shared<Arp>();
    char *e = nullptr;
    long h[11];
    for (int i = 0; i < 11; i++) {
        h[i] = strtol(s, &e, 10);
        if (e == s) return nullptr;
        s = e;
        if (i < 10) { if (*s != ',') return nullptr; ++s; }
    }
    a->shape = (int) std::clamp(h[0] & 7L, 0L, 6L); a->trShape = (int) std::clamp(h[0] >> 3, 0L, 3L); a->step = (int) std::clamp(h[1], 1L, 4096L); a->gate = (int) std::clamp(h[2], 1L, 400L);
    a->trRange = (int) std::clamp(h[3], 0L, 8L); a->trShift = (int) std::clamp(h[4], -48L, 48L); a->transpose = (int) std::clamp(h[5], -48L, 48L);
    a->wrapRange = (int) std::clamp(h[6], 0L, 24L); a->wrapSemis = (int) std::clamp(h[7], -48L, 48L);
    a->zoneMin = (int) std::clamp(h[8], 0L, 127L); a->zoneMax = (int) std::clamp(h[9], 0L, 127L); a->len = (int) std::clamp(h[10], 0L, 1000000L);
    e = const_cast<char *>(s);
    while (*e == '|' && a->n < kClipMax) {
        ++e;
        long v[4];
        for (int i = 0; i < 4; i++) {
            v[i] = strtol(e, &e, 10);
            if (i < 3) { if (*e != ',') return a->shape == 4 && a->n > 0 ? a : nullptr; ++e; }
        }
        a->notes[a->n++] = { (int) v[0], (int) std::max(v[1], 1L), (int) std::clamp(v[2], -128L, 128L), (uint8_t) std::clamp(v[3], 1L, 127L) };
    }
    if (a->shape == 4 && (a->n <= 0 || a->len <= 0)) return nullptr;
    return a;
}

/* A demo part: the notes of one MIDI part (a bass line, a lead, the chords) of a whole song, played on this slot by the demo mode at the host tempo. Unlike the clip
 * (a short loop transposed by the key) it is a performance at absolute pitch: up to kDemoMax notes, ticks of 1/96 beat, looped every `len` ticks (a whole number of bars,
 * the same for every part of one song so the parts stay together). The file holds the clip's text form: "len|tick,dur,note,vel|...". */
inline constexpr int kDemoMax = 20000;
struct DemoNote { int tick, dur; uint8_t note, vel; };
struct Demo {
    int len = 0;
    std::vector<DemoNote> notes;             /* by tick */
};

inline std::shared_ptr<Demo> parseDemo(const char *s)
{
    if (!s || !*s) return nullptr;
    auto d = std::make_shared<Demo>();
    char *e = nullptr;
    d->len = (int) strtol(s, &e, 10);
    while (e && *e == '|' && (int) d->notes.size() < kDemoMax) {
        ++e;
        long v[4];
        for (int i = 0; i < 4; i++) {
            v[i] = strtol(e, &e, 10);
            if (i < 3) { if (*e != ',') goto done; ++e; }
        }
        d->notes.push_back({ (int) std::max(v[0], 0L), (int) std::max(v[1], 1L), (uint8_t) std::clamp(v[2], 0L, 127L), (uint8_t) std::clamp(v[3], 1L, 127L) });
    }
done:
    if (d->notes.empty() || d->len <= 0) return nullptr;
    std::stable_sort(d->notes.begin(), d->notes.end(), [](const DemoNote &a, const DemoNote &b) { return a.tick < b.tick; });
    return d;
}

/* The macro routes a state's "mac" key holds, "macro:key:travel,..." (macro 1..8, an integer parameter's key, its travel over the macro's range): up to `max` routes, the
 * macro as a 0-based index. A route naming a key this build lacks, or a parameter that is not a plain number, is skipped. */
struct MacroRouteSpec { int macro; int pot; float travel; };
int parseMacroRoutes(const char *text, MacroRouteSpec *out, int max);

/* The clock the song clock follows (seconds): the steady clock unless a test supplies its own. */
void demoSetWallClock(double (*fn)());

class Engine {
public:
    Engine();

    /* Pots are written by set_param (UI thread) and read by the audio
     * thread; aligned float stores are atomic on aarch64. */
    float pots[TB_PARAM_COUNT] = {};

    /* The two FX buses' effects: a full set of the FX pots each (the state's "b1." and "b2." keys). Only the effect pots are read;
     * FX Enable and Space come from the main pots, so the SPACE knob moves a bus reverb as it does the main one. */
    float busPots[2][TB_PARAM_COUNT] = {};

    /* Refresh mod slots from the pot array (call after any m*_ change). */

    void setHostBpm(float bpm) { hostBpm = bpm; }
    /* The Move's transport: beats since it started (< 0: stopped). `known` says the host reports it at all; without that the demo runs on a clock of its own. */
    void setHostBeat(double beats, bool known) { hostBeat = beats; hostBeatKnown = known; }

    /* Macros. A route says that parameter `pot` travels `travel` pot steps over the macro's whole range (0..127). Moving a macro pot moves each of its parameters
     * by travel x (the macro's change) / 127, so the preset's own settings are where the macro sat when it was saved. The routes belong to the preset
     * (the state's "mac" key); set them from the thread that sets pots. */
    using MacroRoute = MacroRouteSpec;
    static constexpr int kMaxMacroRoutes = 96;
    void setMacroRoutes(const MacroRoute *r, int n);
    void macroMoved(int macro, float now);                   /* the macro pot has just been set to `now`: move its parameters by the change */
    void macroSync();                                        /* take the macro pots as they are (a state was loaded): nothing moves */
    int macroRouteCount() const { return nMacroRoutes; }

    void onMidi(const uint8_t *msg, int len);
    void renderBlock(int16_t *outInterleavedLr, int frames);

    /* Wavetable slots — phase 4 swaps these from the loader thread. */
    void setTable(int osc, std::shared_ptr<Wavetable> t)
    {
        if (osc >= 0 && osc < kOscs) std::atomic_store(&table[osc], std::move(t));
    }

    /* The same swap, handing the old one back: a preset change clears a slot on the audio callback, and dropping the last reference there frees
     * the table or the samples (tens of MB) on that thread. The caller passes what it gets to the worker to free. */
    std::shared_ptr<Wavetable> takeTable(int osc)
    {
        if (osc < 0 || osc >= kOscs) return nullptr;
        return std::atomic_exchange(&table[osc], std::shared_ptr<Wavetable>());
    }
    std::shared_ptr<SampleSet> takeSample(int which)
    {
        return std::atomic_exchange(which == 2 ? &sampleSet3 : which == 1 ? &sampleSet2 : &sampleSet, std::shared_ptr<SampleSet>());
    }
    std::shared_ptr<ConvPlan> takeConv() { return std::atomic_exchange(&convPlan, std::shared_ptr<ConvPlan>()); }

    /* The sample oscillator's set; null clears it. Swapped in from the loader thread like a wavetable. */
    void setSample(std::shared_ptr<SampleSet> s) { std::atomic_store(&sampleSet, std::move(s)); }
    void setSample2(std::shared_ptr<SampleSet> s) { std::atomic_store(&sampleSet2, std::move(s)); }
    void setSample3(std::shared_ptr<SampleSet> s) { std::atomic_store(&sampleSet3, std::move(s)); }
    /* The convolution reverb's plan (conv.h); null clears it. */
    void setConv(std::shared_ptr<ConvPlan> p) { std::atomic_store(&convPlan, std::move(p)); }

    void allNotesOff(bool allowTail);

    /* The note pattern (null or empty: keys play their own note). Swapped in like a table; the players restart. */
    void setClip(std::shared_ptr<const Clip> c) { std::atomic_store(&clipSp, std::move(c)); clipEpoch.fetch_add(1); }

    /* The arpeggiator (null: off). Swapped in like the note pattern. */
    void setArp(std::shared_ptr<const Arp> a) { std::atomic_store(&arpSp, std::move(a)); arpEpoch.fetch_add(1); }

    /* The demo part (null: none) and the demo switch. Playing starts at the next bar of the song clock every slot shares (see demoSongTicks), so the parts of one song
     * line up whenever each is switched on. */
    void setDemo(std::shared_ptr<const Demo> d) { std::atomic_store(&demoSp, std::move(d)); demoEpoch.fetch_add(1); }
    void setDemoOn(bool on) { demoWant.store(on ? 1 : 0); }
    bool demoPlaying() const { return demoRun; }

    /* Wavetable streams running right now: the number kStreamBudget bounds. */
    int activeStreams() const;

private:
    /* The demo player. */
    std::shared_ptr<const Demo> demoSp;
    std::atomic<int> demoEpoch { 0 }, demoWant { 0 };
    int demoEpochSeen = 0;
    bool demoRun = false, demoArmed = false, demoOnTr = false, demoTrSeen = false, demoArmTr = false;
    long demoArmBar = 0;
    double demoOff = 0.0, demoWaitT = -1.0;        /* transport ticks minus demoPos at the start; the wall time the wait for a transport began */
    double hostBeat = -1.0;
    bool hostBeatKnown = false;
    MacroRoute macroRoutes[kMaxMacroRoutes] = {};
    int nMacroRoutes = 0;
    float macroSeen[8] = {};                                 /* each macro's position as the routes last saw it */
    double demoPos = 0.0, demoFiredTo = 0.0;       /* ticks of the song since the loop's first lap began; the note-ons below demoFiredTo have been sent */
    int dAct[64];
    double dActEnd[64];
    int nDAct = 0;
    void demoSync();
    void demoStop(bool noteOffs);
    void demoFire();
    int  demoLimit(int frames);
    void demoAdvance(int frames);
    void demoSend(int note, int vel);
    /* The clip player: one player per held key, each walking the pattern from the key's own start. */
    struct ClipPlayer {
        bool used = false;
        int key = 0;
        double pos = 0.0, firedTo = 0.0;           /* ticks since the key went down; the note-ons below firedTo have been sent */
        int nAct = 0;
        int actNote[16];
        double actEnd[16];
        uint32_t serial = 0;
    };
    static constexpr int kPlayers = 8;
    ClipPlayer players[kPlayers];
    std::shared_ptr<const Clip> clipSp;
    std::atomic<int> clipEpoch { 0 };
    int clipEpochSeen = 0;
    uint32_t playerSerial = 0;
    int heldKeys[32];                              /* the Mono clip mode's keys held, last pressed last */
    int nHeld = 0;
    void clipStart(int key);
    void clipSync();
    bool clipActive();
    void clipKeyDown(int key);
    void clipKeyUp(int key);
    void clipFire();
    int  clipLimit(int frames);
    void clipAdvance(int frames);
    double clipTicksPerFrame() const;
    void clipKillAll(bool noteOffs);

    /* The arpeggiator: the keys held (in the zone), and one clock from the first key down to the last one up. */
    struct ArpHeld { int note; uint8_t vel; };
    std::shared_ptr<const Arp> arpSp;
    std::atomic<int> arpEpoch { 0 };
    int arpEpochSeen = 0;
    ArpHeld aHeld[32];
    int nAHeld = 0;
    bool arpRun = false;
    double arpPos = 0.0, arpFiredTo = 0.0;         /* ticks since the first key went down */
    long arpNextStep = 0;                          /* the next step to play (the shapes other than Pattern) */
    int aAct[32];                                  /* the notes sounding and the tick each one ends */
    double aActEnd[32];
    int nAAct = 0;
    uint32_t arpRng = 12345u;
    int arpLastPick = -1;
    void arpSync();
    bool arpActive();
    bool arpInZone(int key);
    void arpKeyDown(int key, int vel);
    bool arpKeyUp(int key);
    void arpStop(bool noteOffs);
    void arpFire();
    int  arpLimit(int frames);
    void arpAdvance(int frames);
    void arpPlay(int note, int vel, double endTick);
    void arpStepNote(long s, const Arp &a);

    void noteOn(int note, float vel);
    void noteOff(int note);
    Voice *findVoiceToSteal();

    /* The FX block's settings follow the pots, but only recomputed when one moved. */
    void refreshFx();
    FxSettings fxSettings(const float *pots) const;
    FxSettings fxSettings() const { return fxSettings(this->pots); }

    /* Tone and drive, after the FX block. A shelf at 0 dB is skipped (a neutral pair is the signal bit for
     * bit). Drive follows the master gain and runs ahead of the output clipper; at 0 it is skipped. */
    void refreshTone();
    StereoBiquad loShelf, hiShelf;
    float toneLoDb = 0.0f, toneHiDb = 0.0f;
    bool  toneValid = false, loActive = false, hiActive = false;
    float driveCur = 0.0f;
    float lfoFree[4] = {};                          /* the Free-mode LFOs' phases */

    /* Master width and the mono-below crossover, after the shelves. Both neutral (64 and 0) skips the stage, so a
     * preset that does not use it is bit for bit what it was. */
    void refreshStereo();
    StereoBiquad sideHp;
    float widthCur = 1.0f, monoSeen = -1.0f;
    float busWidthCur[2] = {1.0f, 1.0f};              /* a bus rack's own Utils width (the "bN.mwidth" key), smoothed */
    float utilOutCur[3] = {1.0f, 1.0f, 1.0f};        /* the gain a route on a rack's ending Utils Level Out has put on it (main, bus 1, bus 2), ramped across each block */
    float eqModNow[8] = {};                          /* the master EQ's route offsets, read from the newest voice once per block (the last ones stay while no voice sounds) */
    float utilOutMod[3] = {0.0f, 0.0f, 0.0f};        /* the routes' offsets on that knob, read from the newest voice once per block */
    bool  monoActive = false;

    /* One block's hold on the tables: the only thing keeping them alive. */
    struct Tables { std::shared_ptr<Wavetable> t[kOscs]; std::shared_ptr<SampleSet> s, s2, s3; };
    Tables loadTables() const;
    VoiceContext makeContext(const Tables &t);

    AnalogTables analog { 44100.0 };
    Fx fx { 44100.0f };
    Fx fxBus[2] { Fx(44100.0f), Fx(44100.0f) };
    float busSeen[2][TB_PARAM_COUNT] = {};
    bool  busSeenValid[2] = {};
    float busBuf[4][kBlock] = {};
    float dryBuf[2][kBlock] = {};                 /* the sources routed Out, which skip the effects */
    float busTempoSeen[2] = {};
    void refreshBus(int b);
    void applyUtilOut(int rack, float *l, float *r, int n);       /* the gain of a route on the Level Out of the Utils that ends a rack (0 main, 1 and 2 buses) */
    float fxSeen[TB_P_REV_ON - TB_P_DLY_MIX + 1] = {};
    float distSeen[TB_P_COMP_BELOW - TB_P_DIST_DRIVE + 1] = {};      /* the distortion and compressor pots are contiguous */
    float compOnSeen = -1.0f;
    float convSeen = -1.0f;
    uint32_t ditherState = 2463534242u;                                /* the output dither's generator */
    float modSeen[TB_P_PHAS_POLES - TB_P_CHOR_ON + 1] = {};           /* the chorus, flanger and phaser pots are contiguous */
    float splitSeen[TB_P_SPLIT_STAGES - TB_P_SPLIT_BAND + 1] = {};               /* the Split pots are contiguous */
    float distPreSeen[TB_P_DIST_PRE_Q - TB_P_DIST_PRE + 1] = {};                /* the distortion's pre-filter pots are contiguous */
    float dly2Seen[TB_P_DLY2_MODE - TB_P_DLY2_MIX + 1] = {};              /* the second delay's pots are contiguous */
    float hydSeen[TB_P_HYD_WET - TB_P_HYD_UNI + 1] = {};              /* the HyperD pots are contiguous */
    float msSeen[TB_P_EQS4_Q - TB_P_EQM1_TYPE + 1] = {};              /* so are the SplitMS branch EQs' */
    bool  fxSeenValid = false;
    std::vector<Voice> voices;
    std::shared_ptr<Wavetable> table[kOscs];
    std::shared_ptr<SampleSet> sampleSet, sampleSet2, sampleSet3;
    std::shared_ptr<ConvPlan> convPlan;

    /* mono mode state: held-note stack, last-note priority */
    static constexpr int kStackMax = 32;
    int  monoStack[kStackMax];
    int  monoStackLen = 0;

    float lastPlayedNote = -1.0f;
    uint32_t serialCounter = 0;

    /* controller state */
    float modWheel = 0.0f, aftertouch = 0.0f;
    float pitchBendNorm = 0.0f;
    float hostBpm = 120.0f;
    float tempoSeen = 0.0f;                 /* the host tempo the delay times were last scaled by (0: Delay Sync off) */
    bool  sustainDown = false;
    bool  sustained[128] = {};
};

/* Built-in "Init" wavetable: 64-frame PWM morph, generated in memory so the
 * synth makes sound with no files installed. Band-limiting happens in
 * wtBuild, so the naive source waveform is fine. */
std::shared_ptr<Wavetable> makeInitTable();

} // namespace tb
