#include "engine.h"

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

Engine::Tables Engine::loadTables() const
{
    Tables t;
    for (int o = 0; o < kOscs; o++) t.t[o] = std::atomic_load(&table[o]);
    t.s = std::atomic_load(&sampleSet);
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

    if (mono) {
        if (monoStackLen < kStackMax)
            monoStack[monoStackLen++] = note;

        Voice &v = voices[0];
        if (v.isActive()) {
            /* A note arriving while the previous one is still releasing takes its voice over where it is: the amplitude envelope re-attacks from its current level and the
             * oscillator phases and filters carry on. A fresh start() zeroes the envelope, which is a step down from the release level (a click: a mono preset with short notes,
             * Lead - Rollin, had 20 dB more sub-bass than Serum's from those steps). */
            v.retrigger(c, note, vel);          /* legato / retrig path */
        } else {
            float from = glideOn ? lastPlayedNote : -1.0f;
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

        float from = glideOn ? lastPlayedNote : -1.0f;
        v->start(c, note, vel, ++serialCounter, from);
    }

    lastPlayedNote = (float) note;
}

void Engine::noteOff(int note)
{
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
    for (auto &v : voices)
        if (v.isActive())
            allowTail ? v.stop(true) : v.kill();
    monoStackLen = 0;
    std::memset(sustained, 0, sizeof sustained);
}

void Engine::onMidi(const uint8_t *msg, int len)
{
    if (len < 2) return;
    const uint8_t status = msg[0] & 0xF0;

    switch (status) {
    case 0x90:
        if (len >= 3 && msg[2] > 0) { noteOn(msg[1], (float) msg[2] / 127.0f); break; }
        [[fallthrough]];
    case 0x80:
        noteOff(msg[1]);
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
    s.hydVoices = all ? std::clamp((int) pots[TB_P_HYD_UNI], 1, 8) : 1;
    s.hydDetune = pot01(pots[TB_P_HYD_DET]);
    s.hydRate = pot01(pots[TB_P_HYD_RATE]);
    s.hydWet = all ? pot01(pots[TB_P_HYD_WET]) : 0.0f;
    s.distMode = std::clamp((int) pots[TB_P_DIST_MODE], 0, 4);
    s.distHold = 1 + (int) pots[TB_P_DIST_DRIVE];          /* mode 4 (Downsample): Drive is the hold length minus one (1..128 samples; calibration/probe_dist_downsample.py) */
    s.distCal = pots[TB_P_DIST_CAL] >= 1.0f;
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
    s.compMix = all && pots[TB_P_COMP_ON] >= 1.0f ? pot01(pots[TB_P_COMP_MIX]) : 0.0f;
    s.compThreshDb = -90.0f + 120.0f * pot01(pots[TB_P_COMP_THRESH]);      /* a level at the stage's input, before the master gain */
    s.compRatio = std::pow(100.0f, pot01(pots[TB_P_COMP_RATIO]));
    s.compKneeDb = 48.0f * pot01(pots[TB_P_COMP_KNEE]);
    s.compMulti = pots[TB_P_COMP_MULTI] >= 1.0f;
    s.compThreshBandDb[0] = s.compThreshDb;
    s.compThreshBandDb[1] = -90.0f + 120.0f * pot01(pots[TB_P_COMP_THRESH2]);
    s.compThreshBandDb[2] = -90.0f + 120.0f * pot01(pots[TB_P_COMP_THRESH3]);
    for (int b = 0; b < 3; b++) {
        const float g = pots[TB_P_COMP_GAIN1 + b];
        s.compBandGainDb[b] = g >= 64.0f ? (g - 64.0f) / 63.0f * 24.0f : (g - 64.0f) / 64.0f * 24.0f;
    }
    s.compBelow = pot01(pots[TB_P_COMP_BELOW]);
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
    s.revT60 = 0.2f * std::pow(150.0f, pot01(pots[TB_P_REV_DECAY]));
    s.revDampHz = potEqFreq(pots[TB_P_REV_DAMP]);
    s.revLoHz = potEqFreq(pots[TB_P_REV_LO]);
    s.revWidth = pot01(pots[TB_P_REV_WIDTH]);

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
        std::memcmp(msSeen, pots + TB_P_EQM1_TYPE, sizeof msSeen) == 0) return;
    tempoSeen = tempoNow;
    std::memcpy(fxSeen, pots + TB_P_DLY_MIX, sizeof fxSeen);
    std::memcpy(distSeen, pots + TB_P_DIST_DRIVE, sizeof distSeen);
    compOnSeen = pots[TB_P_COMP_ON];
    std::memcpy(modSeen, pots + TB_P_CHOR_ON, sizeof modSeen);
    std::memcpy(hydSeen, pots + TB_P_HYD_UNI, sizeof hydSeen);
    std::memcpy(dly2Seen, pots + TB_P_DLY2_MIX, sizeof dly2Seen);
    std::memcpy(distPreSeen, pots + TB_P_DIST_PRE, sizeof distPreSeen);
    std::memcpy(msSeen, pots + TB_P_EQM1_TYPE, sizeof msSeen);
    fxSeenValid = true;
    fx.set(fxSettings(pots));
}

void Engine::refreshBus(int b)
{
    float p[TB_PARAM_COUNT];
    std::memcpy(p, busPots[b], sizeof p);
    p[TB_P_FX_ON] = pots[TB_P_FX_ON];
    p[TB_P_FX_SPACE] = pots[TB_P_FX_SPACE];
    const float tempoNow = p[TB_P_DLY_SYNC] >= 1.0f ? hostBpm : 0.0f;
    if (busSeenValid[b] && busTempoSeen[b] == tempoNow && std::memcmp(busSeen[b], p, sizeof p) == 0) return;
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

void Engine::renderBlock(int16_t *out, int frames)
{
    Tables t = loadTables();

    float L[kBlock], R[kBlock];

    int done = 0;
    while (done < frames) {
        int n = std::min(frames - done, kBlock);
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
            lfoFree[l] += potLfoHz(pots[TB_P_LFO1_RATE + l * (TB_P_LFO2_RATE - TB_P_LFO1_RATE)]) * (float) n / 44100.0f;
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
            float fxfMod = 0.0f;
            uint32_t newest = 0;
            for (auto &v : voices)
                if (v.isActive() && v.serialNumber() >= newest) { newest = v.serialNumber(); fxfMod = v.fxfCutoffMod(); }
            const float note = potFilterNote(pots[TB_P_FXF_FREQ]) + env * potBipolar(pots[TB_P_FXF_ENV]) * filterWidth + fxfMod * filterWidth;
            const float hz = std::clamp(midiNoteToHz(note), 4.0f, 20000.0f);
            const float res = std::clamp(pots[TB_P_FXF_RES] / 127.0f, 0.0f, 1.0f);
            fx.setFilter(on, (int) pots[TB_P_FXF_TYPE], hz, kFilterQ / (1.0f - res * 0.99f));
            /* The buses' racks have the same Filter FX (the "b1.fxf_*" / "b2.fxf_*" keys), moved by the same envelope: Lead - Other Side's bus 1 filters
             * its distortion and the EQs behind it boost the highs it takes away. */
            for (int b = 0; b < 2; b++) {
                const float *bp = busPots[b];
                const bool bon = bp[TB_P_FXF_ON] >= 0.5f && pots[TB_P_FX_ON] >= 0.5f;
                if (!bon && !fxBus[b].filterOn()) continue;
                const float bnote = potFilterNote(bp[TB_P_FXF_FREQ]) + env * potBipolar(bp[TB_P_FXF_ENV]) * filterWidth;
                const float bres = std::clamp(bp[TB_P_FXF_RES] / 127.0f, 0.0f, 1.0f);
                fxBus[b].setFilter(bon, (int) bp[TB_P_FXF_TYPE], std::clamp(midiNoteToHz(bnote), 4.0f, 20000.0f), kFilterQ / (1.0f - bres * 0.99f));
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
                fxBus[b].process(busBuf[2 * b], busBuf[2 * b + 1], n);
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
            for (int i = 0; i < n; i++) { L[i] += busBuf[2 * b][i]; R[i] += busBuf[2 * b + 1][i]; }
        }
        if (fx.active())
            fx.process(L, R, n);
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
