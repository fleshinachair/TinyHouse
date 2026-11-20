/* Analog-modeled ADSR — de-JUCE'd port of gin::AnalogADSR (BSD-3, Roland
 * Rabien; after Will Pirkle's "Designing Software Synthesizer Plug-Ins").
 * Exponential curves via one-pole coefficient + offset per stage.
 * Behaviour preserved exactly, including the attack==0 fast path and the
 * noteOn-from-idle output reset.
 */
#pragma once

#include <cmath>

namespace tb {

class AnalogADSR {
public:
    enum class State { idle, attack, decay, sustain, release, delay, hold };

    AnalogADSR()
    {
        setAttack(0.2f);
        setDecay(0.2f);
        setRelease(0.2f);
        setSustainLevel(0.8f);
        reset();
    }

    void setSampleRate(double sr)
    {
        sampleRate = sr;
        calculateAttack(); calculateDecay(); calculateRelease();
    }

    float getOutput() const { return output; }
    State getState()  const { return state;  }

    void noteOn()
    {
        calculateRelease();
        State orig = state;
        if (delaySec > 0.0f) {                      /* Vital's Delay stage: silent until it is up, then the attack starts */
            state = State::delay;
            output = 0.0f;
            stageLeft = (float) (sampleRate * delaySec);
            return;
        }
        if (attack == 0.0f && !controlRate) {
            output = 1.0f;
            decayPos = 0.0f;
            enterHoldOrDecay();
        } else {
            state = State::attack;
            if (orig == State::idle)
                output = 0.0f;
            startAttack();
        }
    }

    void noteOff()
    {
        state = (output > 0.0f) ? State::release : State::idle;
        if (state == State::release && relPow != 0.0f) {                  /* Vital's own release curve: start * (1 - powerScale(x, p)) over the release time, from the level (square-rooted for the gain) it is at */
            relStart = relSq ? std::sqrt(output) : output;
            relPos = 0.0f;
            const float samples = (float) (sampleRate * release);
            relStep = samples > 1.0f ? 1.0f / samples : 1.0f;
        }
    }
    /* p not 0: the release stage is Vital's curve (powerScale(x, p) = (exp(p x) - 1) / (exp(p) - 1), x over the release time); squared into the gain for the amplitude envelope. 0 keeps the analog fall. */
    void setReleasePower(float p, bool squared) { relPow = p; relSq = squared; }

    /* Vital's Delay and Hold stages, in seconds (0 = off, which leaves the envelope exactly as it was). */
    void setDelay(float d) { delaySec = d; }
    void setHold(float h) { holdSec = h; }

    void setAttack(float a)
    {
        if (attack != a) { attack = a; calculateAttack(); }
    }
    void setDecay(float d)
    {
        if (decay != d) { decay = d; calculateDecay(); }
    }
    void setRelease(float r)
    {
        if (release != r) { release = r; calculateRelease(); }
    }
    /* Vital's own decay shape for the amplitude envelope: when `p` is not 0 the decay stage is the curve Vital draws, 1 - (1 - sustain_gain) * (exp(p x) - 1) / (exp(p) - 1) over x = 0..1 of
     * the decay time, squared into the gain as Vital squares its amplitude envelope (the sustain level is the gain, so the curve ends on it). 0 keeps the analog one-pole stage. */
    void setDecayPower(float p, bool squared = true) { decayPow = p; decaySq = squared; }          /* squared: the amplitude envelope's gain (Vital squares it); a modulation envelope is not */

    /* Vital's own attack shape: when on, the attack stage is the curve Vital draws, (exp(-p x) - 1) / (exp(-p) - 1) over x = 0..1 of the attack time (a negative p starts slowly, a positive one
     * rises at once, 0 is a straight line), squared into the gain for the amplitude envelope as Vital squares it. Off keeps the analog one-pole stage. */
    void setAttackPower(float p, bool on, bool squared) { attackPow = p; attackOn = on; attackSq = squared; }

    void setSustainLevel(float s)
    {
        if (sustain != s) {
            sustain = s;
            calculateDecay();
            if (state != State::release)
                calculateRelease();
        }
    }

    inline float process()
    {
        switch (state) {
        case State::idle: break;
        case State::attack:
            if (attackOn) {
                attackPos += attackStep;
                if (attackPos >= 1.0f) { output = 1.0f; decayPos = 0.0f; enterHoldOrDecay(); }
                else { const float v = attackCurve(attackPos); output = attackSq ? v * v : v; }
                break;
            }
            output = attackOffset + output * attackCoeff;
            if (output >= 1.0f || attack == 0.0f) { output = 1.0f; decayPos = 0.0f; enterHoldOrDecay(); }
            break;
        case State::delay:
            if (--stageLeft <= 0.0f) {
                if (attack == 0.0f) { output = 1.0f; decayPos = 0.0f; enterHoldOrDecay(); }
                else { state = State::attack; startAttack(); }
            }
            break;
        case State::hold:
            if (--stageLeft <= 0.0f) { state = State::decay; decayPos = 0.0f; }
            break;
        case State::decay:
            if (decayPow != 0.0f) {
                decayPos += decayStep;
                if (decayPos >= 1.0f) { output = sustain; state = State::sustain; }
                else {
                    const float sl = decaySq ? std::sqrt(sustain) : sustain;
                    const float v = sl + (1.0f - sl) * (1.0f - (std::exp(decayPow * decayPos) - 1.0f) / (std::exp(decayPow) - 1.0f));
                    output = decaySq ? v * v : v;
                }
                break;
            }
            output = decayOffset + output * decayCoeff;
            if (output <= sustain) state = State::sustain;
            break;
        case State::sustain: break;
        case State::release:
            if (relPow != 0.0f) {
                relPos += relStep;
                if (relPos >= 1.0f) { output = 0.0f; state = State::idle; }
                else { const float v = relStart * (1.0f - (std::exp(relPow * relPos) - 1.0f) / (std::exp(relPow) - 1.0f)); output = relSq ? v * v : v; }
                break;
            }
            output = releaseOffset + output * releaseCoeff;
            if (output <= 0.0f || release == 0.0f) { output = 0.0f; state = State::idle; }
            break;
        }
        return output;
    }

    /* Vital runs its modulation envelopes once per block and takes one stage at most in a block: a stage that ends part way through gives the rest of the block away, so an attack and decay far
     * shorter than a block still hold the full level for one whole block. `controlRate` turns that on (the amplitude envelope runs at audio rate and does not use it). */
    void setControlRate(bool on) { controlRate = on; }
    float processControl(int num)
    {
        for (int i = num; --i >= 0;) { const State s0 = state; process(); if (state != s0) break; }
        return output;
    }

    float process(int num)
    {
        for (int i = num; --i >= 0;) process();
        return output;
    }

    /* VCA use: multiply stereo audio in place. */
    void processMultiplying(float *l, float *r, int n)
    {
        for (int i = 0; i < n; i++) {
            process();
            l[i] *= output;
            r[i] *= output;
        }
    }

    void reset()
    {
        state = State::idle;
        output = 0.0f;
    }

private:
    float attackCurve(float x) const
    {
        return std::fabs(attackPow) < 0.01f ? x : (std::exp(-attackPow * x) - 1.0f) / (std::exp(-attackPow) - 1.0f);
    }
    /* where on the power curve the stage starts: at the beginning, or (a note on over a sounding envelope) at the point that matches the level it is at */
    void startAttack()
    {
        attackPos = 0.0f;
        attackStep = (float) (sampleRate * attack) > 1.0f ? 1.0f / (float) (sampleRate * attack) : 1.0f;
        if (!attackOn || output <= 0.0f) return;
        const float v = std::fmin(1.0f, attackSq ? std::sqrt(output) : output);
        attackPos = std::fabs(attackPow) < 0.01f ? v : std::log(1.0f + v * (std::exp(-attackPow) - 1.0f)) / -attackPow;
    }
    void enterHoldOrDecay()
    {
        if (holdSec > 0.0f) { state = State::hold; stageLeft = (float) (sampleRate * holdSec); }
        else state = State::decay;
    }
    void calculateAttack()
    {
        float samples = (float) (sampleRate * attack);
        float tco = std::exp(-0.5f);
        attackCoeff = std::exp(-std::log((1.0f + tco) / tco) / samples);
        attackOffset = (1.0f + tco) * (1.0f - attackCoeff);
        attackStep = samples > 1.0f ? 1.0f / samples : 1.0f;
    }
    void calculateDecay()
    {
        float samples = (float) (sampleRate * decay);
        float tco = std::exp(-5.0f);
        decayCoeff = std::exp(-std::log((1.0f + tco) / tco) / samples);
        decayOffset = (sustain - tco) * (1.0f - decayCoeff);
        decayStep = samples > 1.0f ? 1.0f / samples : 1.0f;
    }
    void calculateRelease()
    {
        float samples = (float) (sampleRate * release);
        float tco = std::exp(-5.0f);
        releaseCoeff = std::exp(-std::log((1.0f + tco) / tco) / samples);
        releaseOffset = -tco * (1.0f - releaseCoeff);
    }

    State state = State::idle;
    bool controlRate = false;
    float relPow = 0.0f, relPos = 0.0f, relStep = 0.0f, relStart = 0.0f;
    bool relSq = true;
    double sampleRate = 44100.0;
    float attack = 0.0f, decay = 0.0f, sustain = 0.0f, release = 0.0f;
    float attackCoeff = 0.0f, decayCoeff = 0.0f, releaseCoeff = 0.0f;
    float attackOffset = 0.0f, decayOffset = 0.0f, releaseOffset = 0.0f;
    float output = 0.0f;
    float decayPow = 0.0f, decayPos = 0.0f, decayStep = 1.0f;
    bool decaySq = true;
    float attackPow = 0.0f, attackPos = 0.0f, attackStep = 1.0f;
    bool attackOn = false, attackSq = false;
    float delaySec = 0.0f, holdSec = 0.0f, stageLeft = 0.0f;
};

/* One-pole value smoother — port of gin::ValueSmoother (glide/noteSmoother). */
template <class T>
class ValueSmoother {
public:
    void setSampleRate(double sr) { sampleRate = sr; reset(); }
    void setTime(double seconds)
    {
        time = seconds;
        reset();
    }
    void reset()
    {
        /* per-sample step for a linear ramp across `time` seconds */
        delta = (time > 0.0) ? (T) (1.0 / (sampleRate * time)) : (T) 1;
    }
    void snapToValue() { current = target; }

    T getCurrentValue() const { return current; }
    T getTargetValue()  const { return target;  }

    void setValue(T v) { target = v; }
    void setValueUnsmoothed(T v) { target = current = v; }

    inline void process(int n)
    {
        if (target != current)
            for (int i = 0; i < n; i++) updateValue();
    }
    inline void updateValue()
    {
        if (current < target)
            current = (T) std::fmin((double) target, (double) (current + delta));
        else if (current > target)
            current = (T) std::fmax((double) target, (double) (current - delta));
    }

private:
    double sampleRate = 44100.0, time = 0.1;
    T delta = (T) 0;
    T current = (T) 0, target = (T) 0;
};

} // namespace tb
