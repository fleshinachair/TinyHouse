#pragma once

#include "AudioFilterTypes.h"
#include <memory>

namespace AudioFilter
{

// This processes a block of samples using the given state and parameters for a biquad (thus first or second order).
// If necessary, the MZTi compensation will also be processed.
template <class DataType>
void processFilter(FilterState<DataType>& state, const FilterParams<DataType>& params, DataType* out, const DataType* in, int numSamples)
{
    for (auto i = 0; i < numSamples; ++i)
    {
        const auto x0 = in[i];

        const auto hp = (x0 - (params.f + params.r)*state.z1 - state.z2) * params.g;
        const auto bp = state.z1 + params.f*hp;
        const auto lp = state.z2 + params.f*bp;

        state.z1 = 2 * bp - state.z1;
        state.z2 = 2 * lp - state.z2;

        out[i] = params.h * hp + params.b * bp + params.l * lp;
    }

    if (params.useCompensation)
    {
        for (auto i = 0; i < numSamples; ++i)
        {
            const auto x0 = out[i];
            const auto y = x0 * params.c0 + state.x1 * params.c1 + state.x2 * params.c2;
            state.x2 = state.x1;
            state.x1 = x0;
            out[i] = y;
        }
    }
}

// Two channels through one biquad stage in the same loop. Each channel's arithmetic is exactly processFilter's, but the two recurrences are independent, so the
// CPU runs one while it waits on the other (the single-channel loop is bound by the latency of its z1 / z2 chain). Used for a stereo pair with one stage.
template <class DataType>
void processFilterStereo(FilterState<DataType>& sa, FilterState<DataType>& sb, const FilterParams<DataType>& params,
                         DataType* outA, DataType* outB, const DataType* inA, const DataType* inB, int numSamples)
{
    /* The same filter as processFilter with the bp and lp lines folded into the state update: z1' = z1 + 2 f hp, z2' = z2 + 2 f z1 + 2 f^2 hp, and the output
     * h hp + b bp + l lp = ch hp + c1 z1 + c2 z2 (ch = h + b f + l f^2, c1 = b + l f, c2 = l). From one sample's state to the next the chain is three dependent
     * fused multiply-adds (hp, then the two updates), where the textbook form chains five or six through hp, bp and lp, and the filter is bound by that chain.
     * The output no longer sits on it. Mathematically the same; rounding at the 1e-7 level (measured against a double precision run, as accurate as the textbook
     * form: tests/test_dsp.cpp). The state and the coefficients live in locals (a memory round trip per sample otherwise). */
    const DataType f = params.f, g = params.g, h = params.h, b = params.b, l = params.l;
    const DataType k1 = -g * (params.f + params.r), k2 = -g;                          /* hp = g x + k1 z1 + k2 z2 */
    const DataType f2 = 2 * f, ff2 = 2 * f * f;
    const DataType ch = h + b * f + l * f * f, c1 = b + l * f, c2 = l;
    DataType az1 = sa.z1, az2 = sa.z2, bz1 = sb.z1, bz2 = sb.z2;
    for (auto i = 0; i < numSamples; ++i)
    {
        const auto xa = inA[i], xb = inB[i];
        const auto hpa = (g * xa + k1 * az1) + k2 * az2;
        const auto hpb = (g * xb + k1 * bz1) + k2 * bz2;
        outA[i] = ch * hpa + c1 * az1 + c2 * az2;
        outB[i] = ch * hpb + c1 * bz1 + c2 * bz2;
        const auto na2 = (az2 + f2 * az1) + ff2 * hpa;
        const auto nb2 = (bz2 + f2 * bz1) + ff2 * hpb;
        az1 = az1 + f2 * hpa;
        bz1 = bz1 + f2 * hpb;
        az2 = na2; bz2 = nb2;
    }
    sa.z1 = az1; sa.z2 = az2; sb.z1 = bz1; sb.z2 = bz2;

    if (params.useCompensation)
    {
        const DataType c0 = params.c0, c1 = params.c1, c2 = params.c2;
        DataType ax1 = sa.x1, ax2 = sa.x2, bx1 = sb.x1, bx2 = sb.x2;
        for (auto i = 0; i < numSamples; ++i)
        {
            const auto xa = outA[i], xb = outB[i];
            const auto ya = xa * c0 + ax1 * c1 + ax2 * c2;
            const auto yb = xb * c0 + bx1 * c1 + bx2 * c2;
            ax2 = ax1; bx2 = bx1;
            ax1 = xa;  bx1 = xb;
            outA[i] = ya;
            outB[i] = yb;
        }
        sa.x1 = ax1; sa.x2 = ax2; sb.x1 = bx1; sb.x2 = bx2;
    }
}

// Two compensated stages in series, stereo, in ONE loop: each sample goes through stage a (the state-variable step, then its compensation FIR) and then stage b.
// Each stage does exactly the operations processFilterStereo gives it, in the same order; the difference is that the four recurrences (two channels, two stages) now
// run together, so the CPU overlaps their latency chains (a stage's next sample does not wait for the later stage to finish the last one). Both stages must have
// useCompensation set; the caller checks.
template <class DataType>
void processFilterStereoPair(FilterState<DataType>& sa1, FilterState<DataType>& sb1, const FilterParams<DataType>& p1,
                             FilterState<DataType>& sa2, FilterState<DataType>& sb2, const FilterParams<DataType>& p2,
                             DataType* outA, DataType* outB, const DataType* inA, const DataType* inB, int numSamples)
{
    struct Coef { DataType f, g, h, b, l, k1, k2, f2, ff2, ch, c1, c2, q0, q1, q2; };
    auto coef = [](const FilterParams<DataType>& p) {
        Coef c;
        c.f = p.f; c.g = p.g; c.h = p.h; c.b = p.b; c.l = p.l;
        c.k1 = -p.g * (p.f + p.r); c.k2 = -p.g;
        c.f2 = 2 * p.f; c.ff2 = 2 * p.f * p.f;
        c.ch = p.h + p.b * p.f + p.l * p.f * p.f; c.c1 = p.b + p.l * p.f; c.c2 = p.l;
        c.q0 = p.c0; c.q1 = p.c1; c.q2 = p.c2;
        return c;
    };
    const Coef A = coef(p1), B = coef(p2);
    DataType az1 = sa1.z1, az2 = sa1.z2, bz1 = sb1.z1, bz2 = sb1.z2;
    DataType ax1 = sa1.x1, ax2 = sa1.x2, bx1 = sb1.x1, bx2 = sb1.x2;
    DataType cz1 = sa2.z1, cz2 = sa2.z2, dz1 = sb2.z1, dz2 = sb2.z2;
    DataType cx1 = sa2.x1, cx2 = sa2.x2, dx1 = sb2.x1, dx2 = sb2.x2;
    for (auto i = 0; i < numSamples; ++i)
    {
        const auto xa = inA[i], xb = inB[i];
        /* stage 1 */
        const auto hpa = (A.g * xa + A.k1 * az1) + A.k2 * az2;
        const auto hpb = (A.g * xb + A.k1 * bz1) + A.k2 * bz2;
        const auto oa = A.ch * hpa + A.c1 * az1 + A.c2 * az2;
        const auto ob = A.ch * hpb + A.c1 * bz1 + A.c2 * bz2;
        const auto na2 = (az2 + A.f2 * az1) + A.ff2 * hpa;
        const auto nb2 = (bz2 + A.f2 * bz1) + A.ff2 * hpb;
        az1 = az1 + A.f2 * hpa;
        bz1 = bz1 + A.f2 * hpb;
        az2 = na2; bz2 = nb2;
        const auto ya = oa * A.q0 + ax1 * A.q1 + ax2 * A.q2;
        const auto yb = ob * A.q0 + bx1 * A.q1 + bx2 * A.q2;
        ax2 = ax1; bx2 = bx1;
        ax1 = oa;  bx1 = ob;
        /* stage 2 */
        const auto hpc = (B.g * ya + B.k1 * cz1) + B.k2 * cz2;
        const auto hpd = (B.g * yb + B.k1 * dz1) + B.k2 * dz2;
        const auto oc = B.ch * hpc + B.c1 * cz1 + B.c2 * cz2;
        const auto od = B.ch * hpd + B.c1 * dz1 + B.c2 * dz2;
        const auto nc2 = (cz2 + B.f2 * cz1) + B.ff2 * hpc;
        const auto nd2 = (dz2 + B.f2 * dz1) + B.ff2 * hpd;
        cz1 = cz1 + B.f2 * hpc;
        dz1 = dz1 + B.f2 * hpd;
        cz2 = nc2; dz2 = nd2;
        outA[i] = oc * B.q0 + cx1 * B.q1 + cx2 * B.q2;
        outB[i] = od * B.q0 + dx1 * B.q1 + dx2 * B.q2;
        cx2 = cx1; dx2 = dx1;
        cx1 = oc;  dx1 = od;
    }
    sa1.z1 = az1; sa1.z2 = az2; sb1.z1 = bz1; sb1.z2 = bz2; sa1.x1 = ax1; sa1.x2 = ax2; sb1.x1 = bx1; sb1.x2 = bx2;
    sa2.z1 = cz1; sa2.z2 = cz2; sb2.z1 = dz1; sb2.z2 = dz2; sa2.x1 = cx1; sa2.x2 = cx2; sb2.x1 = dx1; sb2.x2 = dx2;
}

// Processes a higher order filter cascade. Of course, it expects the order for state and parameters to be the same.
template <class DataType>
void processFilterCascade(FilterStateCascade<DataType>& state, const FilterParamsCascade<DataType>& params, DataType* out, const DataType* in, int numSamples)
{
    assert(state.size() == params.size());

    if (out == nullptr || in == nullptr || state.size() != params.size())
        return;

    const auto numStages = state.size();

    if (numStages > 0)
        processFilter(state[0], params[0], out, in, numSamples);

    for (size_t i = 1; i < numStages; ++i)
        processFilter(state[i], params[i], out, out, numSamples);
}

// A basic implementation of a multichannel filter that can also be of higher order. All channels will process the same parameters.
template <class DataType>
class FilterInstance
{
public:

    typedef FilterParamsCascade<DataType> FilterParam;
    typedef FilterStateCascade<DataType> FilterState;

    // ctor, with given number of channels and order (maxSize = int((order+1)/2).
    FilterInstance(size_t numChannels, size_t maxSize = 1)
        : params(static_cast<int> (maxSize)), states(numChannels)
    {
        // No sense in creating a filter with zero order.
        assert(maxSize > 0);

        for (auto& s : states)
            s = std::make_unique<FilterStateCascade<DataType> >(static_cast<int> (maxSize));
    }

    // Resets all states.
    void clear()
    {
        for (auto& state : states)
            for (size_t i = 0; i < state->size(); ++i)
                (*state)[i].clear();
    }
    
    // Sets the first element to be used directly for first or second order filters without
    // the need to use a BiquadParamCascade. This uses direct Biquad parameters (b0, b1, b2, a1, a2).
    void setParams(const BiquadParam& biquadParams)
    {
        params.resize(1);
        params[0].setFromBiquadParams(biquadParams);
        matchStateSize();
    }

    // Sets the parameters from a (digital) BiquadParamCascade which can for example be set by ButterworthFilterParams.
    // This uses direct Biquad parameters (b0, b1, b2, a1, a2).
    void setParams(const BiquadParamCascade& biquadParamCascade)
    {
        assert(biquadParamCascade.size() <= params.maxSize());

        if (biquadParamCascade.size() <= params.maxSize())
        {
            const auto numStages = biquadParamCascade.size();
            params.resize(numStages);

            for (size_t i = 0; i < numStages; ++i)
                params[i].setFromBiquadParams(biquadParamCascade[i]);

            matchStateSize();
        }
    }

    // Sets the parameters from the internal (SVF) parameter format instead of 
    // normal biquad parameters. This again is only for a first or second order filter and only
    // sets the first element.
    void setParams(const FilterParams<DataType>& newParams)
    {
        params.resize(1);
        params[0] = newParams;
        matchStateSize();
    }

    // Sets the parameters from the internal (SVF) parameter format instead of 
    // normal biquad parameters. Sets a whole cascade.
    void setParams(const FilterParam& newParamCascade)
    {
        assert(newParamCascade.size() <= params.maxSize());

        if (newParamCascade.size() <= params.maxSize())
        {
            const auto numStages = newParamCascade.size();
            params.resize(numStages);

            for (size_t i = 0; i < numStages; ++i)
                params[i] = newParamCascade[i];

            matchStateSize();
        }
    }

    // Processes a block of multichannel samples. Parameters in and out must be arrays,
    // for example: DataType* in[numChannels] = {inL, inR};
    void processBlock(DataType** out, const DataType** in, int numSamples)
    {
        if (states.size() == 2 && params.size() == 1 && states[0]->size() == 1 && states[1]->size() == 1 && out[0] && out[1] && in[0] && in[1])
        {
            processFilterStereo<DataType>((*states[0])[0], (*states[1])[0], params[0], out[0], out[1], in[0], in[1], numSamples);
            return;
        }
        for (size_t ch = 0; ch < states.size(); ++ch)
            processFilterCascade<DataType>(*states[ch], params, out[ch], in[ch], numSamples);
    }

    // Two single-stage stereo filters in series (a 24 dB voice filter) in one loop; false when either is not that shape or lacks the compensation stage.
    static bool processPair(FilterInstance& a, FilterInstance& b, DataType** out, const DataType** in, int numSamples)
    {
        if (a.states.size() != 2 || b.states.size() != 2 || a.params.size() != 1 || b.params.size() != 1 || a.states[0]->size() != 1 || a.states[1]->size() != 1 ||
            b.states[0]->size() != 1 || b.states[1]->size() != 1 || !a.params[0].useCompensation || !b.params[0].useCompensation || !out[0] || !out[1] || !in[0] || !in[1])
            return false;
        processFilterStereoPair<DataType>((*a.states[0])[0], (*a.states[1])[0], a.params[0], (*b.states[0])[0], (*b.states[1])[0], b.params[0], out[0], out[1], in[0], in[1], numSamples);
        return true;
    }

private:

    // Updates the state order to match the params order. If the state order is changed, the states will be
    // cleared to avoid glitches.
    void matchStateSize()
    {
        const auto statesSize = states.size() > 0 ? states[0]->size() : 0;

        if (statesSize != params.size())
        {
            for (auto& state : states)
                state->resize(params.size());

            clear();
        }
    }

    FilterParam params;
    std::vector<std::unique_ptr<FilterState> > states;
};

} // namespace AudioFilter