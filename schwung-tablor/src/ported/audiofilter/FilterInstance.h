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
    /* The state and the coefficients in locals for the loop: written through the references, every sample stored and reloaded them (the output might alias
     * them as far as the compiler knows), a memory round trip inside the filter's recursion. The same operations in the same order (output-identical). */
    const DataType f = params.f, r = params.r, g = params.g, h = params.h, b = params.b, l = params.l, fr = params.f + params.r;
    DataType az1 = sa.z1, az2 = sa.z2, bz1 = sb.z1, bz2 = sb.z2;
    for (auto i = 0; i < numSamples; ++i)
    {
        const auto xa = inA[i], xb = inB[i];

        const auto hpa = (xa - fr*az1 - az2) * g;
        const auto hpb = (xb - fr*bz1 - bz2) * g;
        const auto bpa = az1 + f*hpa;
        const auto bpb = bz1 + f*hpb;
        const auto lpa = az2 + f*bpa;
        const auto lpb = bz2 + f*bpb;

        az1 = 2 * bpa - az1;
        bz1 = 2 * bpb - bz1;
        az2 = 2 * lpa - az2;
        bz2 = 2 * lpb - bz2;

        outA[i] = h * hpa + b * bpa + l * lpa;
        outB[i] = h * hpb + b * bpb + l * lpb;
    }
    sa.z1 = az1; sa.z2 = az2; sb.z1 = bz1; sb.z2 = bz2;
    (void) r;

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