/* Tablor engine — voice allocation, poly/mono, glide, MIDI, master out.
 * Owns the wavetables, the analog tables and the 8 voices.
 */
#pragma once

#include "fx.h"
#include "voice.h"

#include <atomic>
#include <memory>
#include <vector>

namespace tb {

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

    void onMidi(const uint8_t *msg, int len);
    void renderBlock(int16_t *outInterleavedLr, int frames);

    /* Wavetable slots — phase 4 swaps these from the loader thread. */
    void setTable(int osc, std::shared_ptr<Wavetable> t)
    {
        if (osc >= 0 && osc < kOscs) std::atomic_store(&table[osc], std::move(t));
    }

    /* The sample oscillator's set; null clears it. Swapped in from the loader thread like a wavetable. */
    void setSample(std::shared_ptr<SampleSet> s) { std::atomic_store(&sampleSet, std::move(s)); }

    void allNotesOff(bool allowTail);

    /* Wavetable streams running right now: the number kStreamBudget bounds. */
    int activeStreams() const;

private:
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
    bool  monoActive = false;

    /* One block's hold on the tables: the only thing keeping them alive. */
    struct Tables { std::shared_ptr<Wavetable> t[kOscs]; std::shared_ptr<SampleSet> s; };
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
    float fxSeen[TB_P_REV_ON - TB_P_DLY_MIX + 1] = {};
    float distSeen[TB_P_COMP_BELOW - TB_P_DIST_DRIVE + 1] = {};      /* the distortion and compressor pots are contiguous */
    float compOnSeen = -1.0f;
    uint32_t ditherState = 2463534242u;                                /* the output dither's generator */
    float modSeen[TB_P_PHAS_POLES - TB_P_CHOR_ON + 1] = {};           /* the chorus, flanger and phaser pots are contiguous */
    float hydSeen[TB_P_HYD_WET - TB_P_HYD_UNI + 1] = {};              /* the HyperD pots are contiguous */
    bool  fxSeenValid = false;
    std::vector<Voice> voices;
    std::shared_ptr<Wavetable> table[kOscs];
    std::shared_ptr<SampleSet> sampleSet;

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
