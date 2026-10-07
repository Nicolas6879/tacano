"""Replay real sessions call-by-call under the full policy. No future-tax approximation: future is replayed."""
import json, hashlib, collections, sys
from costsim import P, SUB_BASE, DISPATCH_OUT, RESULT, ALPHA
from policy import expected_saving
o=P["opus"]
T=json.load(open("turns3.json",encoding="utf-8"))
V4={**json.load(open("cache_v4.json"))}; V5=json.load(open("cache_v5.json"))
def sid(t): return int(hashlib.md5(t["session"].encode()).hexdigest(),16)%3
BASE_NEW=45_000; BASE_AFTER=60_000; REREAD=25_000; SUMMARY_OUT=10_000
def key(t): return f'{t["session"]}#{t["turn_idx"]}'
def route(a, ctx0, cw0, fut=52):
    mf=a["model_fit"]["probabilities"]
    if a["sensitive"]["noul"]>=0.3 or mf["keep_opus"]>=0.3: return None
    m="haiku" if (mf["haiku"]>=0.8 and a["intent"]["choice"] in ("continue_plan","lookup")) else "sonnet"
    return m if expected_saving(a["volume"]["probabilities"], ctx0, m, cw0, fut)>0.05 else None
def simulate(split, compact_th=None, hygiene=False, delegate=False, fresh_th=0.8, log=None):
    S=collections.defaultdict(list)
    for t in T:
        if split=="all" or (sid(t)!=0)==(split=="dev"): S[t["session"]].append(t)
    tot=0; stats=collections.Counter()
    for sess in S.values():
        sess.sort(key=lambda t:t["turn_idx"]); offset=0
        for t in sess:
            calls=t["calls"]; ctx0,cw0=calls[0][0],calls[0][1]
            cold = cw0>0.5*ctx0
            eff0=max(ctx0-offset, BASE_NEW)
            if ctx0-offset<BASE_NEW: offset=ctx0-BASE_NEW
            fr=V5.get(key(t),{}).get("answers",{}).get("fresh",{}).get("probabilities",{}).get("fresh_ok",0)
            if hygiene and eff0>150_000 and fr>=fresh_th:
                tot+=BASE_NEW*o["write"]; offset=ctx0-BASE_NEW; stats["clear"]+=1; cold=False
            elif compact_th and eff0>compact_th:
                tot+=(eff0*o["write"] if cold else eff0*o["read"]) + SUMMARY_OUT*o["out"] + (BASE_AFTER+REREAD)*o["write"]
                offset=ctx0-(BASE_AFTER+REREAD); stats["compact"]+=1; cold=False
            eff0=ctx0-offset
            a=V4.get(key(t),{}).get("answers")
            fut=52 if not compact_th else max(0,min(52,(compact_th-eff0)/1600))
            m=route(a, eff0, eff0 if cold else 0, fut) if (delegate and a) else None
            growth=max(0,calls[-1][0]-ctx0)
            if m:
                s=P[m]; stats["deleg_"+m]+=1
                if log is not None: log.append((key(t),m))
                tot+=(eff0*o["write"] if cold else eff0*o["read"]) + DISPATCH_OUT*o["out"]
                tot+=SUB_BASE*s["write"]
                k=max(1,round(len(calls)*ALPHA))
                prev=ctx0
                for j in range(k):
                    ctx,cw,out,_=calls[min(int(j/ALPHA),len(calls)-1)]
                    tot+=(SUB_BASE+max(0,ctx-ctx0))*s["read"]+max(0,ctx-prev)*s["write"]+out*s["out"]; prev=max(prev,ctx)
                tot+=(eff0+DISPATCH_OUT)*o["read"]+RESULT*o["write"]+calls[-1][2]*o["out"]
                offset+=growth-RESULT-DISPATCH_OUT
            else:
                for i,(ctx,cw,out,_) in enumerate(calls):
                    if ctx-offset<BASE_NEW: offset=ctx-BASE_NEW; stats["neg_clamp"]+=1
                    eff=ctx-offset
                    w = eff if (i==0 and cold) else min(cw,eff)
                    tot+=(eff-w)*o["read"]+w*o["write"]+out*o["out"]
    return tot/1e6, stats
if __name__=="__main__":
    for split in ("dev","test","all"):
        b,_=simulate(split)
        print(f"== {split}: baseline ${b:.1f}")
        for name,kw in [("routing only",dict(delegate=True)),("compact@200k",dict(compact_th=200_000)),
                        ("compact@200k + hygiene",dict(compact_th=200_000,hygiene=True)),
                        ("ALL: compact@200k + hygiene + routing",dict(compact_th=200_000,hygiene=True,delegate=True)),
                        ("ALL: compact@160k + hygiene + routing",dict(compact_th=160_000,hygiene=True,delegate=True))]:
            c,st=simulate(split,**kw); print(f"  {name:42} ${c:7.1f}  {1-c/b:+.1%}  {dict(st)}")
