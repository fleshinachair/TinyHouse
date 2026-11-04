/* Full-engine CPU check: 8-voice chord, everything on. Run on the Move. */
#include "../src/dsp/engine.h"
#include <algorithm>
#include <cstdio>
#include <ctime>
#include <vector>
using namespace tb;
static double nowMs(){ struct timespec ts; clock_gettime(CLOCK_MONOTONIC,&ts);
    return ts.tv_sec*1e3 + ts.tv_nsec/1e6; }

struct Patch { const char *name; int voices, mono, uni1, uni2, uni3, sync, notes, drive, fx; };

static void run(const Patch &p){
    Engine e;
    /* worst realistic patch: every osc on, bend+formant, sub+noise, filter resonant */
    e.pots[TB_P_VOICE_MODE]=p.mono; e.pots[TB_P_VOICES]=p.voices;
    e.pots[TB_P_WT2_LEVEL]=p.uni2?100:0; e.pots[TB_P_WT3_LEVEL]=p.uni3?100:0;
    e.pots[TB_P_WT1_UNI]=p.uni1; e.pots[TB_P_WT2_UNI]=p.uni2?p.uni2:1; e.pots[TB_P_WT3_UNI]=p.uni3?p.uni3:1;
    e.pots[TB_P_WT1_DETUNE]=40; e.pots[TB_P_WT2_DETUNE]=40; e.pots[TB_P_WT3_DETUNE]=40;
    e.pots[TB_P_WT1_BEND]=90;   e.pots[TB_P_WT2_FORMANT]=90;
    e.pots[TB_P_WT1_SYNC]=p.sync; e.pots[TB_P_WT2_SYNC]=p.sync; e.pots[TB_P_WT3_SYNC]=p.sync;
    e.pots[TB_P_SUB_LEVEL]=90;  e.pots[TB_P_NOISE_LEVEL]=60;
    e.pots[TB_P_FLT_RES]=80;    e.pots[TB_P_FLT_FREQ]=90;
    e.pots[TB_P_FLT_DRIVE]=p.drive;
    if (p.fx) {   /* everything the FX block has, at once: a long reverb, ping-pong delay, width, four EQ bands */
        e.pots[TB_P_REV_MIX]=80;  e.pots[TB_P_REV_DECAY]=100; e.pots[TB_P_REV_DAMP]=100;
        e.pots[TB_P_DLY_MIX]=70;  e.pots[TB_P_DLY_MODE]=1;    e.pots[TB_P_DLY_FB]=80;
        e.pots[TB_P_FX_WIDE]=80;
        for (int b=0;b<4;b++) { const int base=TB_P_EQ1_TYPE+b*4; e.pots[base]=1+b; e.pots[base+1]=30+b*20; e.pots[base+2]=90; e.pots[base+3]=70; }
    }
    const uint8_t notes[8]={36,43,48,55,60,64,67,72};
    for (int i=0;i<p.notes;i++){ uint8_t on[]={0x90,notes[i],100}; e.onMidi(on,3); }
    int16_t out[256];
    for (int b=0;b<100;b++) e.renderBlock(out,128);   /* warm */
    const int blocks = 3444;                           /* 10 s of audio */
    /* The average says whether it keeps up. A dropout is one late block, so the
     * deadline question is answered by the slowest ones. */
    std::vector<float> ms((size_t) blocks);
    double t0=nowMs();
    for (int b=0;b<blocks;b++){
        double b0=nowMs();
        e.renderBlock(out,128);
        ms[(size_t) b]=(float)(nowMs()-b0);
    }
    double wall=nowMs()-t0;
    double audio=blocks*128.0/44.1;
    std::sort(ms.begin(),ms.end());
    const float p999=ms[(size_t)(blocks*0.999)], worst=ms.back();
    printf("%-38s %3d streams: %6.2fx realtime | mean %.3f ms (%3.0f%%) | p99.9 %.3f ms (%3.0f%%) | "
           "max %.3f ms (%3.0f%%) of 2.902 ms\n",
           p.name, e.activeStreams(), audio/wall, wall/blocks, 100.0*(wall/blocks)/2.902,
           p999, 100.0*p999/2.902, worst, 100.0*worst/2.902);
}

int main(){
    const Patch patches[] = {
        /* the first line is the upstream benchmark's patch, unchanged */
        { "8 voices, 2 oscs x uni4",            8, 0,  4,  4,  0,   0, 8,  0, 0 },
        { "8 voices, 2 oscs x uni4, drive",     8, 0,  4,  4,  0,   0, 8, 60, 0 },
        { "8 voices, 2 oscs x uni4, sync",      8, 0,  4,  4,  0,  80, 8,  0, 0 },
        { "8 voices, uni4, sync+drive (worst)", 8, 0,  4,  4,  0,  80, 8, 60, 0 },
        { "8 voices, 3 oscs x uni16 (budget)",  8, 0, 16, 16, 16,   0, 8,  0, 0 },
        { "4 voices, 2 oscs x uni7 (chord pad)",4, 0,  7,  7,  0,   0, 4,  0, 0 },
        { "mono, 3 oscs x uni16",               1, 1, 16, 16, 16,   0, 1,  0, 0 },
        /* the FX block on top of the two heaviest patches, and of a plain one */
        { "FX alone: 1 voice, 1 osc",           1, 0,  1,  0,  0,   0, 1,  0, 1 },
        { "8 voices, uni4, sync+drive + FX",    8, 0,  4,  4,  0,  80, 8, 60, 1 },
        { "8 voices, 3 oscs x uni16 + FX",      8, 0, 16, 16, 16,   0, 8,  0, 1 },
    };
    for (const auto &p : patches) run(p);
    return 0;
}
