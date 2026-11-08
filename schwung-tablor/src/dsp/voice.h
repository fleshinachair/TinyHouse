/* Tablor voice — the per-voice signal path, structured after
 * WavetableVoice.cpp in the original (FigBug/Wavetable, BSD-3):
 *
 *   osc1/osc2/osc3 (unison WT) ─┬─ preFilter ── Filter ──┐
 *   sub, noise ─────────────────┴─ postFilter ───────────┴─ VCA ADSR ─ out
 *   (each source can also go to Filter 2, or to both by a balance: see addSource)
 *
 * Per-source pre/post-filter routing, per-voice filter ADSR, 3 per-voice
 * glide via noteSmoother. Parameters are read from the engine's pot
 * array each block through param_map.h; the mod matrix contributes offsets.
 */
#pragma once

#include "../ported/wt_oscillator.h"
#include "../ported/analog_tables.h"
#include "../ported/filter.h"
#include "sample.h"
#include "../ported/adsr.h"
#include "param_map.h"
#include "params.h"
#include "saturate.h"

#include <cstdlib>
#include <cstring>

namespace tb {

inline float velocityToGain(float velocity, float sensitivity)
{
    float v = velocity * sensitivity + 1.0f - sensitivity;
    float g = v * std::pow(25.0f, v) * 0.04f;
    return std::clamp(g, 0.0f, 1.0f);
}

/* The velocity law the converter writes (VCA Velocity Law = Serum): Serum's loudness does not depend on the velocity unless a route puts it there, and then it is linear
 * in the velocity (calibration/vel_amp probe: a plain saw is flat from 20 to 127, a velocity->Voice Amp route at 100 % follows 20 log10(vel / 127)). The curve is anchored
 * at velocity 100, the one the calibrations play, where it gives the original law's gain for the default tracking (0.4856), so every level fitted at velocity 100 holds. */
inline float velocityToGainSerum(float velocity, float sensitivity)
{
    constexpr float kAt100 = 0.4856f, kV100 = 100.0f / 127.0f;
    return std::clamp(kAt100 * ((1.0f - sensitivity) + sensitivity * velocity / kV100), 0.0f, 1.0f);
}

inline constexpr int kBlock = 128;
inline constexpr int kOscs = TB_WT_SLOTS;

/* The most wavetable streams the engine will run at once, across every voice.
 *
 * 64 is upstream's own worst case (8 voices x 2 oscs x unison 4), the load its
 * benchmark was written around. Raising the limits without a budget would have
 * made the new worst case 384 streams, which no patch needs and this box will
 * not survive. The share is decided by the Voices control and not by how many
 * notes happen to be down, so a chord cannot change a held note's unison and
 * make it step: fewer voices buys wider stacks, and 4 voices of 7+7 fit whole. */
inline constexpr int kStreamBudget = 64;

/* Everything a voice needs from the engine each block. */
/* Where a spare envelope can be pointed. The ORDER is the option order of
 * meN_dst in gen_params.py -- the DSP receives the selection as an index, so
 * the two lists are one contract and must move together. */
enum ModDst {
    MD_NONE = 0,
    MD_WT1_POS, MD_WT2_POS, MD_WT1_LEVEL, MD_WT2_LEVEL, MD_WT1_TUNE, MD_WT2_TUNE,
    MD_WT1_BEND, MD_WT2_BEND, MD_WT1_FORMANT, MD_WT2_FORMANT, MD_WT1_PAN, MD_WT2_PAN,
    MD_FLT_FREQ, MD_FLT_RES, MD_SUB_LEVEL, MD_NOISE_LEVEL, MD_AMP,
    /* Appended, never inserted: saved presets store the index. */
    MD_WT3_POS, MD_WT3_LEVEL, MD_WT1_SYNC, MD_WT2_SYNC, MD_WT3_SYNC, MD_FLT2_FREQ,
    MD_COUNT
};

inline constexpr int kModEnvs = 2;

struct VoiceContext {
    const float *pots = nullptr;            /* TB_PARAM_COUNT raw values   */
    const Wavetable *table[kOscs] = {};
    const SampleSet *sample = nullptr;       /* the sample oscillator's set, if one is loaded */
    int streamBudget = kStreamBudget;       /* this voice's share, see kStreamBudget */
    AnalogTables *analog = nullptr;
    float modWheel = 0.0f;                  /* 0..1  */
    float aftertouch = 0.0f;                /* 0..1  */
    float pitchBendSemis = 0.0f;            /* +/- pb_range already applied */
    float bpm = 120.0f;
    float lfoFree[4] = {};                  /* where each Free-mode LFO is in its cycle (0..1) */
    float *bus[4] = {};                     /* the FX buses' inputs (bus 1 L, R, bus 2 L, R) the voices add their sends to; null: that bus is not in use */
    float *dry[2] = {};                     /* the sources routed Out: past the filter and the effects, added to the output after them (null: nowhere to put them) */
};

inline constexpr int kLfos = 4, kLfoRoutes = 8, kLfoShape = 16;
/* The strides the code below walks the parameter table with: if the generator ever reorders them this stops the build. */
static_assert(TB_P_LFO2_RATE - TB_P_LFO1_RATE == 2 + kLfoShape && TB_P_LFO1_S0 == TB_P_LFO1_RATE + 2 &&
              TB_P_LFO1_MODE == TB_P_LFO1_RATE + 1, "LFO parameters must be consecutive: rate, mode, 16 shape points");
static_assert(TB_P_LFO1_S15 - TB_P_LFO1_S0 == kLfoShape - 1, "LFO shape points must be consecutive");
static_assert(TB_P_LR2_SRC - TB_P_LR1_SRC == 4 && TB_P_LR1_DST == TB_P_LR1_SRC + 1 && TB_P_LR1_AMT == TB_P_LR1_SRC + 2 &&
              TB_P_LR1_BIP == TB_P_LR1_SRC + 3, "LFO route parameters must be src, dst, amt, bip, consecutive");

/* An LFO's rate pot in Hz: 0.05 to 120, logarithmic. */
inline float potLfoHz(float v) { return 0.05f * std::pow(2400.0f, pot01(v)); }

/* The shape: 16 points of one cycle, 0..127 for 0..1, read round the loop with linear interpolation. */
inline float lfoShapeAt(const float *P, int lfo, float phase)
{
    const float x = phase * (float) kLfoShape;
    const int i0 = ((int) x) & (kLfoShape - 1);
    const float fr = x - std::floor(x);
    const int base = TB_P_LFO1_S0 + lfo * (TB_P_LFO2_S0 - TB_P_LFO1_S0);
    const float a = P[base + i0], b = P[base + ((i0 + 1) & (kLfoShape - 1))];
    return pot01(a + (b - a) * fr);
}

class Voice {
public:
    explicit Voice(AnalogTables &analog) : sub(analog), noise(analog) {}

    void setSampleRate(double sr)
    {
        sampleRate = sr;
        for (auto &o : osc) o.setSampleRate(sr);
        sub.setSampleRate(sr); noise.setSampleRate(sr);
        filter.setSampleRate(sr);
        filter2.setSampleRate(sr);
        noiseHp.setSampleRate(sr); noiseLp.setSampleRate(sr);
        filterADSR.setSampleRate(sr);
        adsr.setSampleRate(sr);
        for (auto &e : modEnv) e.setSampleRate(sr);
        noteSmoother.setSampleRate(sr);
    }

    bool isActive() const  { return active; }
    float filterEnvelope() const { return filterADSR.getOutput(); }       /* the Filter FX in the effects block follows it */
    bool isReleasing() const { return adsr.getState() == AnalogADSR::State::release; }
    int  currentNote() const { return midiNote; }
    uint32_t serialNumber() const { return serial; }
    float envOutput() const { return adsr.getOutput(); }

    /* Wavetable streams this voice is rendering now. */
    int streamCount() const
    {
        int n = 0;
        for (int o = 0; o < kOscs; o++)
            if (oscGain[o] > 0.0f) n += vp[o].voices;
        return active ? n : 0;
    }

    void start(const VoiceContext &c, int note, float vel, uint32_t serial_,
               float glideFromNote)
    {
        midiNote = note;
        velocity = vel;
        serial = serial_;
        active = true;
        fastKill = false;

        for (int o = 0; o < kOscs; o++) osc[o].setWavetable(c.table[o]);
        smp.start(c.sample, note);

        const float *P = c.pots;
        const int glideMode = (int) P[TB_P_GLIDE_MODE];
        glissando  = glideMode == 1;
        portamento = glideMode == 2;
        if (glideFromNote >= 0.0f && (glissando || portamento)) {
            noteSmoother.setTime(potGlideTime(P[TB_P_GLIDE]));
            noteSmoother.setValueUnsmoothed(glideFromNote / 127.0f);
            noteSmoother.setValue((float) note / 127.0f);
        } else {
            noteSmoother.setValueUnsmoothed((float) note / 127.0f);
        }

        filter.reset();
        fltPrev = -1.0f;
        filter2.reset();
        fltPrev2 = -1.0f;
        noiseHp.reset(); noiseLp.reset();
        noiseHpSet = noiseLpSet = 0.0f;
        filterADSR.reset();
        for (auto &e : modEnv) e.reset();

        updateParams(c, 0);

        /* oscillator phases: free-running (the old retrig-off default —
         * the retrig switch was cut from the surface) */
        for (auto &o : osc) {
            float phases[kMaxUnison];
            for (auto &ph : phases)
                ph = (float) rand() / (float) RAND_MAX;
            o.noteOn(phases, kMaxUnison);
        }
        sub.noteOn(0.0f);
        noise.noteOn(0.0f);
        /* Serum's noise is mono with a random left/right level imbalance per note (2.3 dB on average, calibration/noise_stereo: correlation 1.000, side/mid -13..-29 dB) */
        noisePan = ((float) rand() / (float) RAND_MAX * 2.0f - 1.0f) * 0.27f;
        for (int l = 0; l < kLfos; l++)             /* Trig restarts at the top of the cycle, Free joins the running one */
            lfoPhase[l] = P[TB_P_LFO1_MODE + l * (TB_P_LFO2_MODE - TB_P_LFO1_MODE)] >= 1.0f ? c.lfoFree[l] : 0.0f;

        filterADSR.noteOn();
        for (auto &e : modEnv) e.noteOn();

        adsr.reset();
        adsr.noteOn();
    }

    /* Mono-mode retrigger without phase reset. */
    void retrigger(const VoiceContext &c, int note, float vel)
    {
        const float *P = c.pots;
        midiNote = note;
        velocity = vel;
        smp.start(c.sample, note);
        if (glissando || portamento) {
            noteSmoother.setTime(potGlideTime(P[TB_P_GLIDE]));
            noteSmoother.setValue((float) note / 127.0f);
        } else {
            noteSmoother.setValueUnsmoothed((float) note / 127.0f);
        }
        bool legato = P[TB_P_LEGATO] > 0.5f && !isReleasing();       /* a releasing voice has to be brought back in whatever the legato switch says */
        if (!legato) {              /* env retrig switches cut: always retrig */
            for (int l = 0; l < kLfos; l++)
                if (P[TB_P_LFO1_MODE + l * (TB_P_LFO2_MODE - TB_P_LFO1_MODE)] < 1.0f) lfoPhase[l] = 0.0f;
            filterADSR.noteOn();
        for (auto &e : modEnv) e.noteOn();
            adsr.noteOn();
        }
    }

    void stop(bool allowTail)
    {
        adsr.noteOff();
        filterADSR.noteOff();
        for (auto &e : modEnv) e.noteOff();
        if (!allowTail) {
            active = false;
        }
    }

    void kill()
    {
        fastKill = true;
        adsr.noteOff(); filterADSR.noteOff();
        for (auto &e : modEnv) e.noteOff();
    }

    void render(const VoiceContext &c, float *outL, float *outR, int n)
    {
        if (!active) return;

        /* Re-point at the context's tables every block: the loader may have
         * swapped them since noteStarted, and the block's shared_ptr is the
         * only thing keeping a table alive. A held raw pointer would dangle.
         * A swap mid-note lands as a waveform step at this block's first
         * sample — fade the block in from silence (2.9 ms) to mask it. */
        bool swapped = false;
        for (int o = 0; o < kOscs; o++) {
            swapped |= lastTable[o] && c.table[o] != lastTable[o];
            lastTable[o] = c.table[o];
            osc[o].setWavetable(c.table[o]);
        }

        updateParams(c, n);

        float preL[kBlock] = {}, preR[kBlock] = {};
        float pre2L[kBlock] = {}, pre2R[kBlock] = {};              /* the sources that go to Filter 2 */
        float postL[kBlock] = {}, postR[kBlock] = {};

        const float *P = c.pots;

        /* Each source goes through the filter (pre) or straight to the amp (post: Serum's "direct" routing). Through the filters it has a
         * balance t from -1 (Filter 1 only) to +1 (Filter 2 only): Filter 1 gets (1 - t) / 2 of it and Filter 2 (1 + t) / 2, as Serum's
         * (calibration/probe_filter2.py). Filter 2 only runs if some source reaches it. */
        bool usesF2 = false;
        float dryL[kBlock] = {}, dryR[kBlock] = {};
        bool dryUsed = false;
        auto route = [&](bool direct, int balPot, auto &&renderTo) {
            if (direct) { renderTo(postL, postR); return; }
            const float t = std::clamp(P[balPot] / 63.5f - 1.0f, -1.0f, 1.0f);
            if (t <= -0.999f) { renderTo(preL, preR); return; }
            usesF2 = true;
            if (t >= 0.999f) { renderTo(pre2L, pre2R); return; }
            float tl[kBlock] = {}, tr[kBlock] = {};
            renderTo(tl, tr);
            const float wa = 0.5f * (1.0f - t), wb = 0.5f * (1.0f + t);
            for (int i = 0; i < n; i++) {
                preL[i] += wa * tl[i];  preR[i] += wa * tr[i];
                pre2L[i] += wb * tl[i]; pre2R[i] += wb * tr[i];
            }
        };
        /* The FX buses: a source can also send to Bus 1 and Bus 2 (gain = pot/127, which the converter writes as Serum's level cubed), taken
         * before the filter and after the amp envelope (calibration/probe_bus.py), and its route can be None, which leaves it on the buses alone,
         * or Out, which takes it past the effects as well. */
        float busL[2][kBlock] = {}, busR[2][kBlock] = {};
        bool busUsed = false;
        auto addSource = [&](float routePot, int balPot, int sendPot, auto &&renderTo) {
            const bool none = routePot >= 1.5f && routePot < 2.5f, out = routePot >= 2.5f, direct = routePot >= 0.5f && routePot < 1.5f;
            const float s1 = c.bus[0] ? P[sendPot] * (1.0f / 127.0f) : 0.0f, s2 = c.bus[2] ? P[sendPot + 1] * (1.0f / 127.0f) : 0.0f;
            const bool sends = s1 > 0.0f || s2 > 0.0f;
            if (!sends) {
                if (out) { renderTo(dryL, dryR); dryUsed = true; }
                else if (!none) route(direct, balPot, renderTo);
                return;
            }
            float tl[kBlock] = {}, tr[kBlock] = {};
            renderTo(tl, tr);
            auto addTo = [&](float *l, float *r) { for (int i = 0; i < n; i++) { l[i] += tl[i]; r[i] += tr[i]; } };
            if (out) { addTo(dryL, dryR); dryUsed = true; }
            else if (!none) route(direct, balPot, addTo);
            busUsed = true;
            for (int i = 0; i < n; i++) {
                busL[0][i] += s1 * tl[i]; busR[0][i] += s1 * tr[i];
                busL[1][i] += s2 * tl[i]; busR[1][i] += s2 * tr[i];
            }
        };
        static const int kOscPost[kOscs] = { TB_P_WT1_POST, TB_P_WT2_POST, TB_P_WT3_POST };
        static const int kOscBal[kOscs] = { TB_P_WT1_BAL, TB_P_WT2_BAL, TB_P_WT3_BAL };
        for (int o = 0; o < kOscs; o++)
            if (oscGain[o] > 0.0f)
                addSource(P[kOscPost[o]], kOscBal[o], TB_P_WT1_BUS1 + 2 * o, [&](float *l, float *r) { osc[o].processAdding(oscNote[o], vp[o], l, r, n); });
        if (subParams.leftGain + subParams.rightGain > 0.0f)
            addSource(P[TB_P_SUB_POST], TB_P_SUB_BAL, TB_P_SUB_BUS1, [&](float *l, float *r) { sub.processAdding(subNote, subParams, l, r, n); });
        if (noiseParams.leftGain + noiseParams.rightGain > 0.0f)
            addSource(P[TB_P_NOISE_POST], TB_P_NOISE_BAL, TB_P_NOISE_BUS1, [&](float *l, float *r) {
                if (noiseHpHz <= 0.0f && noiseLpHz <= 0.0f) { noise.processAdding(60.0f, noiseParams, l, r, n); return; }
                float tl[kBlock] = {}, tr[kBlock] = {};                /* the noise through its own colour filters first (Serum's Color: a 12 dB/octave low-pass and high-pass) */
                noise.processAdding(60.0f, noiseParams, tl, tr, n);
                if (noiseHpHz > 0.0f) noiseHp.process(tl, tr, n);
                if (noiseLpHz > 0.0f) noiseLp.process(tl, tr, n);
                for (int i = 0; i < n; i++) { l[i] += tl[i]; r[i] += tr[i]; }
            });
        if (smp.on && P[TB_P_SMP_LEVEL] > 0.0f) {                  /* the sample oscillator: its level knob is squared, as the wavetables' are */
            const float knob = pot01(P[TB_P_SMP_LEVEL]);
            const bool fixedPitch = P[TB_P_SMP_FIXED] >= 0.5f;                 /* a sample that does not follow the keyboard (Serum's noise slot samples) */
            const float semis = (fixedPitch ? 0.0f : (float) (midiNote - smp.r->root) + c.pitchBendSemis) + P[TB_P_SMP_TUNE] + P[TB_P_SMP_FINE] / 100.0f;
            addSource(P[TB_P_SMP_POST], TB_P_SMP_BAL, TB_P_SMP_BUS1, [&](float *l, float *r) { smp.processAdding(semis, knob * knob, (float) sampleRate, l, r, n); });
        }

        /* velocity */
        float vgain = (P[TB_P_VCA_VLAW] >= 0.5f ? velocityToGainSerum(velocity, ampVelTrack) : velocityToGain(velocity, ampVelTrack)) * ampModGain;
        for (int i = 0; i < n; i++) {
            preL[i] *= vgain;  preR[i] *= vgain;
            pre2L[i] *= vgain; pre2R[i] *= vgain;
            postL[i] *= vgain; postR[i] *= vgain;
            dryL[i] *= vgain;  dryR[i] *= vgain;
        }

        /* A voice filter's output: to the main path (Master), to the buses alone (None) or past the effects (Out), and a send to each bus taken after the filter
         * (Serum's routing slots 5 and 6). The velocity was applied to the sources, so a send takes it off again: sends do not follow it. */
        const float busGain = P[TB_P_VCA_VLAW] >= 0.5f ? velocityToGainSerum(velocity, 0.0f) : velocityToGain(velocity, ampVelTrack);
        auto filterOut = [&](const float *l, const float *r, int outPot, int sendPot) {
            const int dest = (int) P[outPot];
            for (int i = 0; i < n; i++) {
                if (dest == 0) { postL[i] += l[i]; postR[i] += r[i]; }
                else if (dest == 2) { dryL[i] += l[i]; dryR[i] += r[i]; }
            }
            if (dest == 2) dryUsed = true;
            const float rel = vgain > 1e-6f ? 1.0f / vgain : 0.0f;
            for (int b = 0; b < 2; b++) {
                const float s = c.bus[2 * b] ? P[sendPot + b] * (1.0f / 127.0f) * rel : 0.0f;
                if (s <= 0.0f) continue;
                busUsed = true;
                for (int i = 0; i < n; i++) { busL[b][i] += s * l[i]; busR[b][i] += s * r[i]; }
            }
        };

        /* Filter drive: saturate going in, so the harmonics are what the filter then
         * shapes, and again coming out, so a resonant peak rounds off instead of
         * ringing past everything. The gain ramps across the block because a knob
         * step would otherwise be a level step. */
        const bool driving = driveMix > 0.0f || driveMixTarget > 0.0f;
        if (driving) driveIn(preL, preR, n, driveGain, driveGainTarget, driveMix, driveMixTarget);
        runFilter(filter, fltPrev, fltTarget, fltQ, preL, preR, n);
        if (driving) driveOut(preL, preR, n, driveMix, driveMixTarget);
        if (P[TB_P_FLT_GAIN] > 0.0f) {                                  /* Serum's filter Drive as the gain it is */
            const float g = std::pow(10.0f, P[TB_P_FLT_GAIN] / 127.0f * 24.0f / 20.0f);
            for (int i = 0; i < n; i++) { preL[i] *= g; preR[i] *= g; }
        }
        driveGain = driveGainTarget;
        driveMix = driveMixTarget;

        if ((int) P[TB_P_FLT1_OUT] == 3) {                          /* Filter 1 in series into Filter 2 */
            usesF2 = true;
            for (int i = 0; i < n; i++) { pre2L[i] += preL[i]; pre2R[i] += preR[i]; }
        }
        if (usesF2) {
            const bool driving2 = drive2Mix > 0.0f || drive2MixTarget > 0.0f;
            if (driving2) driveIn(pre2L, pre2R, n, drive2Gain, drive2GainTarget, drive2Mix, drive2MixTarget);
            runFilter(filter2, fltPrev2, fltTarget2, fltQ2, pre2L, pre2R, n);
            if (driving2) driveOut(pre2L, pre2R, n, drive2Mix, drive2MixTarget);
            if (P[TB_P_FLT2_GAIN] > 0.0f) {
                const float g = std::pow(10.0f, P[TB_P_FLT2_GAIN] / 127.0f * 24.0f / 20.0f);
                for (int i = 0; i < n; i++) { pre2L[i] *= g; pre2R[i] *= g; }
            }
            filterOut(pre2L, pre2R, TB_P_FLT2_OUT, TB_P_FLT2_BUS1);
        } else {
            fltPrev2 = -1.0f;                                       /* a filter that was not run restarts from its target */
        }
        drive2Gain = drive2GainTarget;
        drive2Mix = drive2MixTarget;

        filterOut(preL, preR, TB_P_FLT1_OUT, TB_P_FLT1_BUS1);

        if (busUsed || dryUsed) {                                   /* the amp envelope on the main path, the sends and the Out sources alike; a send does not follow the velocity */
            for (int i = 0; i < n; i++) {
                adsr.process();
                const float g = adsr.getOutput();
                postL[i] *= g; postR[i] *= g;
                dryL[i] *= g; dryR[i] *= g;
                for (int b = 0; b < 2; b++) { busL[b][i] *= g * busGain; busR[b][i] *= g * busGain; }
            }
            for (int b = 0; b < 2; b++)
                if (c.bus[2 * b])
                    for (int i = 0; i < n; i++) { c.bus[2 * b][i] += busL[b][i]; c.bus[2 * b + 1][i] += busR[b][i]; }
            if (c.dry[0])
                for (int i = 0; i < n; i++) { c.dry[0][i] += dryL[i]; c.dry[1][i] += dryR[i]; }
        } else {
            adsr.processMultiplying(postL, postR, n);
        }
        if (swapped) {
            for (int i = 0; i < n; i++) {
                float g = (float) i / (float) n;
                postL[i] *= g;
                postR[i] *= g;
            }
        }

        if (adsr.getState() == AnalogADSR::State::idle)
            active = false;

        for (int i = 0; i < n; i++) {
            outL[i] += postL[i];
            outR[i] += postR[i];
        }

        noteSmoother.process(n);
    }

private:
        void updateParams(const VoiceContext &c, int blockSize)
    {
        const float *P = c.pots;

        /* ---- envelopes ---- */
        /* The Decay macro moves both decays by the same number of pot steps (64 is neutral): a step is
         * the same ratio of time on either envelope, so the two stay in proportion. */
        const float dOff = P[TB_P_DECAY] - 64.0f;
        auto decayPot = [dOff](float v) { return std::min(std::max(v + dOff, 0.0f), 127.0f); };
        filterADSR.setAttack(potAttackTime(P[TB_P_FLT_A]));
        filterADSR.setDecay(potEnvTime(decayPot(P[TB_P_FLT_D])));
        filterADSR.setSustainLevel(pot01(P[TB_P_FLT_S]));
        filterADSR.setRelease(potEnvTime(P[TB_P_FLT_R]));
        if (blockSize > 0) filterADSR.process(blockSize);

        adsr.setAttack(potAttackTime(P[TB_P_VCA_A]));
        adsr.setDecay(potEnvTime(decayPot(P[TB_P_VCA_D])));
        adsr.setSustainLevel(pot01(P[TB_P_VCA_S]));
        adsr.setRelease(fastKill ? 0.01f : potEnvTime(P[TB_P_VCA_R]));

        /* ---- the two spare envelopes, and where they point ----
         * Each contributes env * amount to ONE destination. Amount is bipolar
         * so a sweep can fall as well as rise, which is the shape that makes a
         * wavetable read as a struck instrument. */
        for (int i = 0; i < MD_COUNT; i++) modOff[i] = lfoLvl[i] = 0.0f;
        {
            static const struct { int a, d, s, r, dst, amt; } k[kModEnvs] = {
                { TB_P_ME1_A, TB_P_ME1_D, TB_P_ME1_S, TB_P_ME1_R,
                  TB_P_ME1_DST, TB_P_ME1_AMT },
                { TB_P_ME2_A, TB_P_ME2_D, TB_P_ME2_S, TB_P_ME2_R,
                  TB_P_ME2_DST, TB_P_ME2_AMT },
            };
            for (int i = 0; i < kModEnvs; i++) {
                modEnv[i].setAttack(potAttackTime(P[k[i].a]));
                modEnv[i].setDecay(potEnvTime(P[k[i].d]));
                modEnv[i].setSustainLevel(pot01(P[k[i].s]));
                modEnv[i].setRelease(potEnvTime(P[k[i].r]));
                if (blockSize > 0) modEnv[i].process(blockSize);
                const int dst = (int) P[k[i].dst];
                if (dst > MD_NONE && dst < MD_COUNT) {
                    const float e = modEnv[i].getOutput() * potBipolar(P[k[i].amt]);
                    /* Serum adds an envelope route to the oscillator volume KNOB and the knob's taper (gain = knob^2) comes after, as for the LFOs:
                     * from volume 0 a route of depth a gives gain a^2, not a (calibration/probe_noise_route.py: noise slot and WT oscillator, 12 dB per halving of the depth) */
                    if (dst == MD_WT1_LEVEL || dst == MD_WT2_LEVEL || dst == MD_WT3_LEVEL || dst == MD_SUB_LEVEL || dst == MD_NOISE_LEVEL) lfoLvl[dst] += e;
                    else modOff[dst] += e;
                }
            }
        }

        /* ---- the LFOs, and the routes that carry them to the same destinations ----
         * Each runs at the block rate. A route adds lfo * amount to its destination: Uni with the LFO swinging 0..1, Bi with it
         * swinging -1..+1 (the polarity is the route's own, as Serum stores it). */
        {
            float lfoOut[kLfos];
            for (int l = 0; l < kLfos; l++) {
                const int rate = TB_P_LFO1_RATE + l * (TB_P_LFO2_RATE - TB_P_LFO1_RATE);
                if (blockSize > 0) {
                    lfoPhase[l] += potLfoHz(P[rate]) * (float) blockSize / (float) sampleRate;
                    lfoPhase[l] -= std::floor(lfoPhase[l]);
                }
                lfoOut[l] = lfoShapeAt(P, l, lfoPhase[l]);                    // 0..1
            }
            const int stride = TB_P_LR2_SRC - TB_P_LR1_SRC;
            for (int r = 0; r < kLfoRoutes; r++) {
                const int src = (int) P[TB_P_LR1_SRC + r * stride];
                const int dst = (int) P[TB_P_LR1_DST + r * stride];
                if (src >= 1 && src <= kLfos && dst > MD_NONE && dst < MD_COUNT) {
                    const float u = lfoOut[src - 1];
                    const float v = P[TB_P_LR1_BIP + r * stride] >= 1.0f ? 2.0f * u - 1.0f : u;
                    const float amt = v * potBipolar(P[TB_P_LR1_AMT + r * stride]);
                    /* Serum moves the volume KNOB, and the knob's taper comes after: a level route is added to the knob
                     * position here and squared with it below. The spare envelopes' level routes are added to the knob
                     * the same way (above). */
                    if (dst == MD_WT1_LEVEL || dst == MD_WT2_LEVEL || dst == MD_WT3_LEVEL || dst == MD_SUB_LEVEL || dst == MD_NOISE_LEVEL)
                        lfoLvl[dst] += amt;
                    else if (dst == MD_WT1_TUNE || dst == MD_WT2_TUNE)
                        /* Serum's pitch routes run from a fraction of a semitone (vibrato) to octaves: the amount is squared, so the
                         * pot's fine steps are fine and its end is 4 x 12 = 48 semitones */
                        modOff[dst] += std::copysign(amt * amt, amt) * 4.0f;
                    else
                        modOff[dst] += amt;
                }
            }
        }

        /* ---- oscillators ---- */
        float baseNote = noteSmoother.getCurrentValue() * 127.0f;
        if (glissando) baseNote = (float) (int) (baseNote + 0.5f);
        baseNote += c.pitchBendSemis;

        /* WT3 is the plain one: no bend or formant pots, and only position,
         * level and sync can be modulated. MD_NONE's offset is always zero,
         * so it stands in for a destination the oscillator does not have. */
        static const struct OscKeys {
            int pos, level, tune, uni, det, spread, pan, sync, bend, form;
            int dPos, dLvl, dTun, dBnd, dFrm, dPan, dSync;
        } kOsc[kOscs] = {
            { TB_P_WT1_POS, TB_P_WT1_LEVEL, TB_P_WT1_TUNE, TB_P_WT1_UNI, TB_P_WT1_DETUNE,
              TB_P_WT1_SPREAD, TB_P_WT1_PAN, TB_P_WT1_SYNC, TB_P_WT1_BEND, TB_P_WT1_FORMANT,
              MD_WT1_POS, MD_WT1_LEVEL, MD_WT1_TUNE, MD_WT1_BEND, MD_WT1_FORMANT, MD_WT1_PAN,
              MD_WT1_SYNC },
            { TB_P_WT2_POS, TB_P_WT2_LEVEL, TB_P_WT2_TUNE, TB_P_WT2_UNI, TB_P_WT2_DETUNE,
              TB_P_WT2_SPREAD, TB_P_WT2_PAN, TB_P_WT2_SYNC, TB_P_WT2_BEND, TB_P_WT2_FORMANT,
              MD_WT2_POS, MD_WT2_LEVEL, MD_WT2_TUNE, MD_WT2_BEND, MD_WT2_FORMANT, MD_WT2_PAN,
              MD_WT2_SYNC },
            { TB_P_WT3_POS, TB_P_WT3_LEVEL, TB_P_WT3_TUNE, TB_P_WT3_UNI, TB_P_WT3_DETUNE,
              TB_P_WT3_SPREAD, TB_P_WT3_PAN, TB_P_WT3_SYNC, -1, -1,
              MD_WT3_POS, MD_WT3_LEVEL, MD_NONE, MD_NONE, MD_NONE, MD_NONE, MD_WT3_SYNC },
        };

        int unison[kOscs];
        for (int o = 0; o < kOscs; o++) {
            const OscKeys &k = kOsc[o];
            VoicedWTParams &v = vp[o];

            /* tune modulation reads as an octave at full amount */
            oscNote[o] = baseNote + P[k.tune] + modOff[k.dTun] * 12.0f;

            v.position = std::clamp(pot01(P[k.pos]) + modOff[k.dPos], 0.0f, 1.0f);
            {
                const float knob = std::clamp(pot01(P[k.level]) + lfoLvl[k.dLvl], 0.0f, 1.0f);
                v.gain = std::clamp(knob * knob + modOff[k.dLvl], 0.0f, 1.0f);
            }
            v.pan      = std::clamp(potBipolar(P[k.pan]) + modOff[k.dPan], -1.0f, 1.0f);
            v.detune   = potDetune(P[k.det]);
            v.spread   = pot01(P[k.spread]);
            v.sync     = std::clamp(pot01(P[k.sync]) + modOff[k.dSync], 0.0f, 1.0f);
            v.bend     = k.bend < 0 ? 0.0f
                       : std::clamp(potBipolar(P[k.bend]) + modOff[k.dBnd], -1.0f, 1.0f);
            v.formant  = k.form < 0 ? 0.0f
                       : std::clamp(potBipolar(P[k.form]) + modOff[k.dFrm], -1.0f, 1.0f);
            oscGain[o] = v.gain;

            /* An oscillator counts from its pot and its routing, not its live
             * gain: an envelope crossing zero must not move anyone's unison. */
            bool on = P[k.level] > 0.0f || (int) P[TB_P_ME1_DST] == k.dLvl
                                        || (int) P[TB_P_ME2_DST] == k.dLvl;
            for (int r = 0; r < kLfoRoutes && !on; r++)
                on = P[TB_P_LR1_SRC + r * (TB_P_LR2_SRC - TB_P_LR1_SRC)] >= 1.0f &&
                     (int) P[TB_P_LR1_DST + r * (TB_P_LR2_SRC - TB_P_LR1_SRC)] == k.dLvl;
            unison[o] = on ? std::clamp((int) P[k.uni], 1, kMaxUnison) : 0;
        }
        fitUnison(unison, c.streamBudget);
        for (int o = 0; o < kOscs; o++)
            vp[o].voices = std::max(unison[o], 1);

        /* ---- sub + noise ---- */
        subNote = baseNote + P[TB_P_SUB_TUNE];
        const float subKnob = std::clamp(pot01(P[TB_P_SUB_LEVEL]) + lfoLvl[MD_SUB_LEVEL], 0.0f, 1.0f);
        float subLvl = std::clamp(subKnob * subKnob + modOff[MD_SUB_LEVEL],
                                  0.0f, 1.0f);
        float subPan = 0.0f;                 /* pan control cut: centered */
        switch ((int) P[TB_P_SUB_WAVE]) {
        case 0: subParams.wave = Wave::sine; break;
        case 1: subParams.wave = Wave::triangle; break;
        case 2: subParams.wave = Wave::sawUp; break;
        case 3: subParams.wave = Wave::pulse; subParams.pw = 0.50f; break;
        case 4: subParams.wave = Wave::pulse; subParams.pw = 0.25f; break;
        default: subParams.wave = Wave::pulse; subParams.pw = 0.125f; break;
        }
        subParams.leftGain  = subLvl * (1.0f - subPan);
        subParams.rightGain = subLvl * (1.0f + subPan);

        const float nzKnob = std::clamp(pot01(P[TB_P_NOISE_LEVEL]) + lfoLvl[MD_NOISE_LEVEL], 0.0f, 1.0f);
        float nzLvl = std::clamp(nzKnob * nzKnob + modOff[MD_NOISE_LEVEL],
                                 0.0f, 1.0f);
        const float nzPan = noisePan;        /* no pan control; the random per-note imbalance */
        const int nzType = (int) P[TB_P_NOISE_TYPE];
        noiseParams.wave = nzType == 0 ? Wave::whiteNoise : nzType == 4 ? Wave::brownNoise : Wave::pinkNoise;
        const float nzHpPot = P[TB_P_NOISE_HP], nzLpPot = P[TB_P_NOISE_LP];
        noiseHpHz = nzType == 2 ? 950.0f : nzType == 3 ? 1190.0f : nzHpPot >= 1.0f ? 20.0f * std::pow(1000.0f, nzHpPot / 127.0f) : 0.0f;
        noiseLpHz = nzLpPot < 126.5f ? 20.0f * std::pow(1000.0f, nzLpPot / 127.0f) : 0.0f;
        if (noiseHpHz > 0.0f && noiseHpHz != noiseHpSet) {
            noiseHp.setType(Filter::highpass);
            noiseHp.setSlope(Filter::db12);
            noiseHp.setParams(noiseHpHz, kFilterQ);
            noiseHpSet = noiseHpHz;
        }
        if (noiseLpHz > 0.0f && noiseLpHz != noiseLpSet) {
            noiseLp.setType(Filter::lowpass);
            noiseLp.setSlope(Filter::db12);
            noiseLp.setParams(noiseLpHz, kFilterQ);
            noiseLpSet = noiseLpHz;
        }
        noiseParams.leftGain  = nzLvl * (1.0f - nzPan);
        noiseParams.rightGain = nzLvl * (1.0f + nzPan);

        /* ---- filter drive ---- */
        driveGainTarget = potDriveGain(P[TB_P_FLT_DRIVE]);
        driveMixTarget = potDriveMix(P[TB_P_FLT_DRIVE]);
        drive2GainTarget = potDriveGain(P[TB_P_FLT2_DRIVE]);
        drive2MixTarget = potDriveMix(P[TB_P_FLT2_DRIVE]);
        if (blockSize == 0) {               /* a new note starts where the knob is */
            driveGain = driveGainTarget;
            driveMix = driveMixTarget;
            drive2Gain = drive2GainTarget;
            drive2Mix = drive2MixTarget;
        }

        /* ---- amp ---- */
        ampVelTrack = pot01(P[TB_P_VCA_VEL]);
        ampModGain = std::clamp(1.0f + modOff[MD_AMP], 0.0f, 2.0f);

        /* ---- filter (the original's law, verbatim) ---- */
        {
            const float filterWidth = 12.0f * std::log2(20000.0f / 440.0f) + 69.0f; /* midi(20k) */
            float env = filterADSR.getOutput();
            float sens = pot01(P[TB_P_FLT_VEL]);
            sens = velocity * sens + 1.0f - sens;

            float n = potFilterNote(P[TB_P_FLT_FREQ]);
            n += ((float) midiNote - 60.0f) * pot01(P[TB_P_FLT_KEY]);
            n += env * sens * potBipolar(P[TB_P_FLT_ENV]) * filterWidth;
            n += modOff[MD_FLT_FREQ] * filterWidth;

            float f = midiNoteToHz(n);
            float maxFreq = std::min(20000.0f, (float) (sampleRate / 2.0));
            f = std::clamp(f, 4.0f, maxFreq);

            float res = std::clamp(pot01(P[TB_P_FLT_RES]) + modOff[MD_FLT_RES],
                                   0.0f, 1.0f);
            float q = kFilterQ / (1.0f - res * 0.99f);

            static const Filter::Type kTypes[8] = {
                Filter::lowpass, Filter::lowpass, Filter::highpass, Filter::highpass,
                Filter::bandpass, Filter::bandpass, Filter::notch, Filter::notch };
            int t = (int) P[TB_P_FLT_TYPE] & 7;
            filter.setType(kTypes[t]);
            filter.setSlope((t & 1) ? Filter::db24 : Filter::db12);
            fltTarget = f; fltQ = q;           /* applied in steps across the block (see runFilter) */

            /* Filter 2: the same law with its own knobs (and its own share of the filter envelope), and the destination the LFOs and
             * the spare envelopes can point at. */
            float n2 = potFilterNote(P[TB_P_FLT2_FREQ]);
            n2 += ((float) midiNote - 60.0f) * pot01(P[TB_P_FLT2_KEY]);
            n2 += env * sens * potBipolar(P[TB_P_FLT2_ENV]) * filterWidth;
            n2 += modOff[MD_FLT2_FREQ] * filterWidth;
            fltTarget2 = std::clamp(midiNoteToHz(n2), 4.0f, maxFreq);
            fltQ2 = kFilterQ / (1.0f - std::clamp(pot01(P[TB_P_FLT2_RES]), 0.0f, 1.0f) * 0.99f);
            const int t2 = (int) P[TB_P_FLT2_TYPE] & 7;
            filter2.setType(kTypes[t2]);
            filter2.setSlope((t2 & 1) ? Filter::db24 : Filter::db12);
        }
    }

    /* Scale the stacks to fit the budget, keeping their proportions and every
     * oscillator that is on. Rounding can leave the sum over by one or two when
     * a small stack is held at 1, so the largest gives way until it fits. */
    static void fitUnison(int *unison, int budget)
    {
        int want = 0;
        for (int o = 0; o < kOscs; o++) want += unison[o];
        if (want <= budget) return;
        int have = 0;
        for (int o = 0; o < kOscs; o++) {
            if (unison[o] > 0) unison[o] = std::max(1, unison[o] * budget / want);
            have += unison[o];
        }
        while (have > budget) {
            int *largest = std::max_element(unison, unison + kOscs);
            if (*largest <= 1) break;
            (*largest)--;
            have--;
        }
    }

    VoicedWTOscillator osc[kOscs] = { VoicedWTOscillator(kMaxUnison),
                                      VoicedWTOscillator(kMaxUnison),
                                      VoicedWTOscillator(kMaxUnison) };
    VoicedWTParams vp[kOscs];
    StereoOscillator sub, noise;
    StereoOscillator::Params subParams, noiseParams;
    /* The filter's cutoff moves with the envelope and the LFOs, and the coefficients were set once per block: a step at each block edge,
     * which at a low cutoff on a loud low note is a click (a one-sample spike at the first block edge of a note, and at every edge while the
     * envelope is moving). Now the cutoff is stepped in kFltSub pieces across the block, in log frequency. */
    static constexpr int kFltSub = 4;
    void runFilter(Filter &flt, float &prev, float target, float q, float *l, float *r, int n)
    {
        if (prev < 0.0f) prev = target;
        const float lp = std::log(prev), lt = std::log(target);
        int done = 0;
        for (int k = 1; k <= kFltSub; k++) {
            const int end = k == kFltSub ? n : (n * k) / kFltSub;
            if (end <= done) continue;
            flt.setParams(std::exp(lp + (lt - lp) * (float) k / (float) kFltSub), q);
            flt.process(l + done, r + done, end - done);
            done = end;
        }
        prev = target;
    }
    /* The drive stage before a filter (tanh of the gained signal, mixed in) and after it (tanh alone), both ramped across the block. */
    static void driveIn(float *l, float *r, int n, float g0, float gT, float m0, float mT)
    {
        const float gStep = (gT - g0) / (float) n, mStep = (mT - m0) / (float) n;
        float g = g0, m = m0;
        for (int i = 0; i < n; i++) {
            l[i] += m * (fastTanh(g * l[i]) - l[i]);
            r[i] += m * (fastTanh(g * r[i]) - r[i]);
            g += gStep; m += mStep;
        }
    }
    static void driveOut(float *l, float *r, int n, float m0, float mT)
    {
        const float mStep = (mT - m0) / (float) n;
        float m = m0;
        for (int i = 0; i < n; i++) {
            l[i] += m * (fastTanh(l[i]) - l[i]);
            r[i] += m * (fastTanh(r[i]) - r[i]);
            m += mStep;
        }
    }
    float fltTarget = 1000.0f, fltPrev = -1.0f, fltQ = kFilterQ;
    float fltTarget2 = 1000.0f, fltPrev2 = -1.0f, fltQ2 = kFilterQ;
    float drive2Gain = 1.0f, drive2Mix = 0.0f, drive2GainTarget = 1.0f, drive2MixTarget = 0.0f;

    Filter filter, filter2, noiseHp, noiseLp;
    float noiseHpHz = 0.0f, noiseHpSet = 0.0f, noiseLpHz = 0.0f, noiseLpSet = 0.0f, noisePan = 0.0f;
    SamplePlayer smp;
    AnalogADSR filterADSR, adsr;
    AnalogADSR modEnv[kModEnvs];        /* the two spare, routable envelopes */
    float modOff[MD_COUNT] = {};        /* their summed offset per destination */
    float lfoPhase[kLfos] = {};
    float lfoLvl[MD_COUNT] = {};         /* LFO offsets to the level KNOBS (before the taper) */
    ValueSmoother<float> noteSmoother;
    const Wavetable *lastTable[kOscs] = {};

    double sampleRate = 44100.0;
    float oscNote[kOscs] = { 60.0f, 60.0f, 60.0f }, oscGain[kOscs] = {};
    float subNote = 48.0f;
    float velocity = 0.0f, ampVelTrack = 1.0f, ampModGain = 1.0f;
    float driveGain = 1.0f, driveMix = 0.0f, driveGainTarget = 1.0f, driveMixTarget = 0.0f;
    int midiNote = -1;
    uint32_t serial = 0;
    bool active = false, fastKill = false, glissando = false, portamento = false;
};

} // namespace tb
