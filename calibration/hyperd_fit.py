#!/usr/bin/env python3
"""Serum's HyperD alone, against the engine's Hyper stage (schwung-tablor/src/dsp/fx.h class Hyper).

  hyperd_fit.py render   render the Serum references (needs the Serum plugin; cached in calibration/refs/hyperd_sine.pkl and hyperd_saw.pkl, never committed):
                         a pure sine through HyperD at several Detune / Rate / Unison / Wet, and Lead - Rollin's oscillator (a single-cycle table) through its own HyperD
  hyperd_fit.py fit      fit the numpy model (triangle-moved delay voices) to those references; prints the parameter set the engine's constants were taken from
  hyperd_fit.py check    the ENGINE (tablor_cli, hyd_* pots) against the sine references: line-spread histogram, gain, AM

What was measured (2026-11-08): with 2 voices a channel holds lines whose offset from the note is constant for seconds and then flips sign, so each voice's delay is a
TRIANGLE wave (a constant slope is a constant pitch offset); the offset scales with the harmonic number (a time-scaled copy, not a frequency shift); peak-to-peak swing
= 20 ms x Detune; the triangle's frequency grows as Rate^~2 (0.25 Hz at 50 %); voices differ in phase and speed; the cloud is not normalised by its voice count (7 voices at
Wet 100 are +8.4 dB re the input, 2 voices +0.8, 4 voices +4.9).  Score: line-spread histogram (energy by |offset| bin), AM std of the narrow band, level, and on the saw the
band profile near h1..h3 and AM / FM against Serum's.
"""
import copy, os, pickle, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "calibration"))
os.chdir(ROOT)
import numpy as np
REFS = ROOT / "calibration/refs"
SINE_PKL, SAW_PKL = REFS / "hyperd_sine.pkl", REFS / "hyperd_saw.pkl"
SR = 44100

sm=lambda x:np.clip(x,0,1)**2*(3-2*np.clip(x,0,1))
wetG=lambda w:sm(w); dryG=lambda w:1.0 if w<=0.25 else 1-sm((w-0.25)/0.75)
def tri(p): return 4*np.abs((p%1.0)-0.5)-1.0           # -1..1 triangle, phase 0 -> +1
def frac_delay(x,d):
    n=len(x); pos=np.arange(n)-d; i0=np.floor(pos).astype(int); fr=pos-i0; a=np.clip(i0,0,n-1); b=np.clip(i0+1,0,n-1); return x[a]*(1-fr)+x[b]*fr
def model_tri(x,uni,det,rate,wet,prm,seed=1):
    """voices: delay = dmin + P/2 (1 + shape(phase + f_v t)); P = P100 ms * (det/100)^p ; f = F50 * (rate/50)^q * s_v ; level g per voice"""
    P100,p,F50,q,slo,shi,g=prm["P100"],prm["p"],prm["F50"],prm["q"],prm["slo"],prm["shi"],prm["g"]
    rng=np.random.default_rng(seed); n=x.shape[1]; t=np.arange(n)/SR; out=np.zeros_like(x); w=wet/100.0
    P=P100*1e-3*SR*(det/100.0)**p; f=F50*(rate/50.0)**q
    shape=prm.get("shape","tri")
    for c in range(2):
        acc=np.zeros(n)
        for v in range(uni):
            sv=slo+(shi-slo)*rng.random(); ph=rng.random()
            ph_t=ph+f*sv*t
            lfo=tri(ph_t) if shape=="tri" else np.sin(2*np.pi*ph_t)
            d=1+P/2*(1+lfo)
            acc+=frac_delay(x[c],d)
        out[c]=x[c]*dryG(w)+acc*g*wetG(w)
    return out

# ---------------- sine data
NOTE_S=84; F0S=440*2**((NOTE_S-69)/12)
SWS={"det10":(7,10,50,100),"det25":(7,25,50,100),"det50":(7,50,50,100),"det100":(7,100,50,100),"rate5":(7,50,5,100),"rate25":(7,50,25,100),"rate75":(7,50,75,100),"rate100":(7,50,100,100),
     "uni2":(2,50,50,100),"uni3":(3,50,50,100),"uni4":(4,50,50,100),"uni8":(8,50,50,100),"wet50":(7,50,50,50)}
EDG=[0,1.5,3,6,12,24,48,1e9]
def sine_metrics(y,f0=F0S,t0=0.5,t1=3.5):
    out=[]; lv=[]; am=[]
    for c in (0,1):
        s=y[c][int(t0*SR):int(t1*SR)]; n=1<<18; sp=np.abs(np.fft.rfft(s*np.hanning(len(s)),n))**2; fr=np.fft.rfftfreq(n,1/SR)
        tot=sp[(fr>f0-200)&(fr<f0+200)].sum()+1e-20; off=np.abs(fr-f0)
        out.append([10*np.log10(sp[(off>=a)&(off<b)&(fr>f0-1000)&(fr<f0+1000)].sum()/tot+1e-12) for a,b in zip(EDG[:-1],EDG[1:])])
        lv.append(10*np.log10((y[c][int(t0*SR):int(t1*SR)]**2).mean()+1e-12))
        X=np.fft.fft(y[c]); frr=np.fft.fftfreq(len(y[c]),1/SR); Z=np.zeros_like(X); m=(frr>f0-60)&(frr<f0+60); Z[m]=2*X[m]; z=np.abs(np.fft.ifft(Z))[int(t0*SR):int(t1*SR)]
        am.append(20*np.log10(z+1e-9).std())
    return np.mean(out,axis=0),float(np.mean(lv)),float(np.mean(am))
def load_sine():
    return pickle.load(open(SINE_PKL,"rb"))
def score_sine(prm,D=None,seeds=(1,2),verbose=False,keys=None):
    D=D or load_sine(); xin=D["in"]; tot=0; n=0; rows=[]
    lin=10*np.log10((xin[:,int(.5*SR):int(3.5*SR)]**2).mean()+1e-12)
    for k,(uni,det,rate,wet) in SWS.items():
        if keys and k not in keys: continue
        ts,tl,ta=sine_metrics(D[k]); ts=np.maximum(ts,-35)
        ms=[];ml=[];ma=[]
        for sd in seeds:
            y=model_tri(xin,uni,det,rate,wet,prm,seed=sd); s_,l_,a_=sine_metrics(y); ms.append(np.maximum(s_,-35)); ml.append(l_); ma.append(a_)
        ms=np.mean(ms,axis=0); ml=np.mean(ml); ma=np.mean(ma)
        e=np.sqrt(np.mean((ms-ts)**2)); tot+=e+abs(ml-tl)*0.5+abs(ma-ta)*0.5; n+=1
        rows.append((k,e,ml-tl,ma,ta,ms,ts))
    if verbose:
        for k,e,dl,ma,ta,ms,ts in rows: print("  %-7s hist rms %4.1f dB | level %+4.1f dB | AM %.1f (serum %.1f) | model %s | serum %s"%(k,e,dl,ma,ta," ".join("%4.0f"%v for v in ms)," ".join("%4.0f"%v for v in ts)))
    return tot/max(n,1)
# ---------------- saw data (Lead - Rollin's oscillator through its HyperD, note 67)
NOTE_W=67; F0W=440*2**((NOTE_W-69)/12)
SWPW={"saved":(7,43.3,39.1,48.7),"det10":(7,10,39.1,48.7),"det100":(7,100,39.1,48.7),"rate5":(7,43.3,5,48.7),"rate20":(7,43.3,20,48.7),"rate60":(7,43.3,60,48.7),"rate100":(7,43.3,100,48.7),"uni2":(2,43.3,39.1,48.7),"uni4":(4,43.3,39.1,48.7),"wet100":(7,43.3,39.1,100)}
def prof(y,h=(1,2,3),w=90,step=6,f0=F0W):
    y=y.mean(axis=0); s=y[int(0.6*SR):int(2.6*SR)]; n=1<<18; sp=np.abs(np.fft.rfft(s*np.hanning(len(s)),n))**2; fr=np.fft.rfftfreq(n,1/SR); res=[]
    for hh in h:
        c=hh*f0; res+= [10*np.log10(sp[(fr>=a)&(fr<a+step)].sum()+1e-20) for a in np.arange(c-w*hh,c+w*hh,step*hh)]
    return np.array(res)
def narrow(y,w=60,f0=F0W):
    y=y.mean(axis=0); X=np.fft.fft(y); fr=np.fft.fftfreq(len(y),1/SR); Z=np.zeros_like(X); m=(abs(fr-f0)<w)&(fr>0); Z[m]=2*X[m]; z=np.fft.ifft(Z)[int(0.6*SR):int(2.6*SR)]
    a=20*np.log10(np.abs(z)+1e-9); inst=np.diff(np.unwrap(np.angle(z)))*SR/(2*np.pi); return a.std(), (inst-inst.mean()).std()
_W=None
def load_saw():
    global _W
    if _W is None:
        D=pickle.load(open(SAW_PKL,"rb")); ref=prof(D["in"]).max()
        _W=(D,ref,{k:(prof(D[k])-ref,narrow(D[k])) for k in SWPW})
    return _W
def score_saw(prm,model=None,seeds=(1,2),verbose=False):
    D,ref,TG=load_saw(); model=model or model_tri; L=0; rows=[]
    for k,(uni,det,rate,wet) in SWPW.items():
        es=[];ams=[];fms=[]
        for sd in seeds:
            y=model(D["in"],uni,det,rate,wet,prm,seed=sd); pr=prof(y)-ref; am,fm=narrow(y)
            pt=TG[k][0]; es.append(np.sqrt(np.mean((np.maximum(pr,-70)-np.maximum(pt,-70))**2))); ams.append(am); fms.append(fm)
        e=np.mean(es); am=np.mean(ams); fm=np.mean(fms); amt,fmt=TG[k][1]
        L+=e; rows.append((k,e,am,amt,fm,fmt))
    if verbose:
        for r in rows: print("  %-8s prof rms %4.1f dB | AM %.2f (serum %.2f) | FM %.2f (serum %.2f)"%r)
    return L/len(rows), rows


# ---------------- Serum renders
def render_refs():
    import fx_probe as P
    import test_render_calibration as H
    REFS.mkdir(parents=True, exist_ok=True)
    serum = H.Serum()
    _, src = H.load_preset("Bass - Gravity")
    base = H.init_tree(src, table="Analog/Basic Shapes.wav")          # frame 0 is a sine
    D = pickle.load(open(SINE_PKL, "rb")) if SINE_PKL.exists() else {}
    for k, v in [("in", None)] + list(SWS.items()):
        if k in D: continue
        pp = {"kParamEnable": 0.0} if v is None else {"kParamUnison": float(v[0]), "kParamDetune": float(v[1]), "kParamRate": float(v[2]), "kParamWet": float(v[3])}
        D[k] = np.asarray(serum.render(P.fx_tree(base, "HyperD", pp, 9), [NOTE_S], 4.0, 3.9, 100)); pickle.dump(D, open(SINE_PKL, "wb")); print("sine", k, flush=True)
    _, tree = H.load_preset("Lead - Rollin")
    for k in ("Oscillator1", "Oscillator2", "Oscillator3"):
        tree[k].setdefault("plainParams", {}); tree[k]["plainParams"]["kParamEnable"] = 0.0
    def off(t, idx):
        t2 = copy.deepcopy(t)
        for i in idx:
            n = list(t2["FXRack1"]["FX"][i].values())[0]; pp = n.get("plainParams")
            if isinstance(pp, dict): pp["kParamEnable"] = 0.0
            else: n["plainParams"] = {"kParamEnable": 0.0}
        return t2
    def hp(t, **kw):
        t2 = copy.deepcopy(t); n = list(t2["FXRack1"]["FX"][0].values())[0]; n["plainParams"].update(kw); return t2
    W = pickle.load(open(SAW_PKL, "rb")) if SAW_PKL.exists() else {}
    if "in" not in W:
        W["in"] = np.asarray(serum.render(off(tree, [0, 1, 2]), [NOTE_W], 3.0, 2.9, 100))
    kw_of = {"saved": {}, "det10": dict(kParamDetune=10.0), "det100": dict(kParamDetune=100.0), "rate5": dict(kParamRate=5.0), "rate20": dict(kParamRate=20.0), "rate60": dict(kParamRate=60.0),
             "rate100": dict(kParamRate=100.0), "uni2": dict(kParamUnison=2.0), "uni4": dict(kParamUnison=4.0), "wet100": dict(kParamWet=100.0)}
    for k, kw in kw_of.items():
        if k in W: continue
        W[k] = np.asarray(serum.render(hp(off(tree, [1, 2]), **kw), [NOTE_W], 3.0, 2.9, 100)); pickle.dump(W, open(SAW_PKL, "wb")); print("saw", k, flush=True)


def check_engine():
    import test_render_calibration as H
    _, src = H.load_preset("Bass - Gravity")
    tree = H.init_tree(src, table="Analog/Basic Shapes.wav")
    _, m0 = H.tablor_render(tree, [NOTE_S], 0.3, 0.2)
    m0["params"]["fx_on"] = 1; m0["params"]["volume"] = 22
    def eng(uni, det, rate, wet):
        m = copy.deepcopy(m0); m["params"].update({"hyd_uni": uni, "hyd_det": round(det * 1.27), "hyd_rate": round(rate * 1.27), "hyd_wet": round(wet * 1.27)})
        return H.run_cli(H.d2m.blob(m), [NOTE_S], 4.0, 3.9, 100)
    D = load_sine(); xin = eng(1, 0, 0, 0); tot = 0
    print("engine Hyper vs Serum, pure sine at note 84 (energy by |offset| bin, Hz: " + " ".join(str(e) for e in EDG[:-1]) + ")")
    for k, (uni, det, rate, wet) in SWS.items():
        ts, tl, ta = sine_metrics(D[k]); ts = np.maximum(ts, -35)
        y = eng(uni, det, rate, wet); s_, l_, a_ = sine_metrics(y); s_ = np.maximum(s_, -35)
        e = np.sqrt(np.mean((s_ - ts) ** 2)); tot += e
        print("  %-7s hist rms %4.1f | gain re input: engine %+5.1f serum %+5.1f | AM %.1f (serum %.1f)" % (k, e, l_ - sine_metrics(xin)[1], tl - sine_metrics(D["in"])[1], a_, ta))
    print("mean histogram rms %.2f dB" % (tot / len(SWS)))


def fit():
    Ds = load_sine()
    def joint(prm):
        s = score_sine(prm, Ds, seeds=(1, 2), keys=["det10", "det25", "det50", "det100", "rate25", "rate75", "rate100", "uni3", "uni4", "uni8", "wet50"])
        l, rows = score_saw(prm, seeds=(1, 2))
        pen = np.mean([abs(np.log((r[2] + .3) / (r[3] + .3))) + abs(np.log((r[4] + .3) / (r[5] + .3))) for r in rows])
        return s + 0.7 * l + 3.0 * pen
    prm = dict(P100=20.0, p=1.0, F50=0.25, q=1.8, slo=0.3, shi=1.8, g=0.95, shape="tri")
    grid = {"P100": [14, 17, 20, 23, 27], "p": [0.85, 1.0, 1.15], "F50": [0.17, 0.21, 0.25, 0.3, 0.36], "q": [1.5, 1.8, 2.1], "slo": [0.1, 0.3, 0.5, 0.7], "shi": [1.2, 1.5, 1.8, 2.2, 2.6], "g": [0.8, 0.9, 1.0, 1.1]}
    best = joint(prm); print("start", best, flush=True)
    for rnd in range(2):
        for k, vals in grid.items():
            for v in vals:
                p2 = dict(prm); p2[k] = v
                if p2["slo"] >= p2["shi"]: continue
                s = joint(p2)
                if s < best - 1e-6: best, prm = s, p2; print(rnd, k, v, "->", round(best, 3), flush=True)
    print("final", prm, best)
    print("sine:"); score_sine(prm, Ds, seeds=(1, 2, 3), verbose=True)
    print("saw:"); l, rows = score_saw(prm, seeds=(1, 2, 3), verbose=True); print("saw profile rms", l)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    {"render": render_refs, "fit": fit, "check": check_engine}[cmd]()
    sys.stdout.flush()
    os._exit(0)
