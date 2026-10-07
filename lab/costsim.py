"""Cost model calibrated on real transcripts. Prices $/MTok (claude-api skill, cached 2026-09-25); 1h cache writes = 2x input."""
import json, statistics as st
P = {"opus":   dict(inp=4, out=20, read=0.20, write=8),
     "sonnet": dict(inp=2, out=10, read=0.20, write=4),
     "haiku":  dict(inp=1, out=5,  read=0.10, write=2)}
SUB_BASE = 30_000     # measured: restricted-tool agents (Explore) start at 31-36k; general-purpose 48-53k
DISPATCH_OUT = 700    # Opus tokens to write the subagent brief (+thinking)
RESULT = 1_500        # report tokens returned into Opus context
ALPHA = 1.3           # subagent needs ~30% more calls (cold start, re-reading)
FUTURE_CAP = 150      # context persists ~this many later calls before compaction/session end

def inline_cost(t, m="opus"):
    p=P[m]; c=0
    for ctx,cw,out,_ in t["calls"]:
        c += (ctx-cw)*p["read"] + cw*p["write"] + out*p["out"]
    growth = max(0, t["calls"][-1][0] - t["calls"][0][0])
    c += growth * min(t["future_calls"],FUTURE_CAP) * P["opus"]["read"]
    return c/1e6

def delegated_cost(t, m):
    o=P["opus"]; s=P[m]; calls=t["calls"]; c0=calls[0][0]
    ctx0,cw0,_,_ = calls[0]
    c = (ctx0-cw0)*o["read"] + cw0*o["write"] + c0*o["read"] + DISPATCH_OUT*o["out"] + RESULT*o["write"] + calls[-1][2]*o["out"]
    c += SUB_BASE*s["write"]
    k = max(1, round(len(calls)*ALPHA))
    prev=c0
    for j in range(k):
        ctx,cw,out,_ = calls[min(int(j/ALPHA), len(calls)-1)]
        c += (SUB_BASE+max(0,ctx-c0))*s["read"] + max(0,ctx-prev)*s["write"] + out*s["out"]; prev=max(prev,ctx)
    c += RESULT * min(t["future_calls"],FUTURE_CAP) * o["read"]
    return c/1e6

if __name__=="__main__":
    T=json.load(open("turns2.json",encoding="utf-8"))
    tot={k:0 for k in ("inline","best_sonnet","best_haiku")}
    buckets={}
    for t in T:
        i=inline_cost(t); s=delegated_cost(t,"sonnet"); h=delegated_cost(t,"haiku")
        tot["inline"]+=i; tot["best_sonnet"]+=min(i,s); tot["best_haiku"]+=min(i,h)
        n=len(t["calls"]); b="1" if n==1 else "2-3" if n<=3 else "4-8" if n<=8 else "9-20" if n<=20 else "21+"
        x=buckets.setdefault(b,[0,0,0,0]); x[0]+=1; x[1]+=i; x[2]+=s; x[3]+=h
    print({k:round(v,2) for k,v in tot.items()})
    print("calls  n  inline$  sonnet$  haiku$  (delegating whole bucket)")
    for b in ("1","2-3","4-8","9-20","21+"):
        n,i,s,h=buckets[b]; print(f"{b:>5} {n:4} {i:8.2f} {s:8.2f} {h:8.2f}  win={'sonnet' if s<i else 'inline'}/{'haiku' if h<i else 'inline'}")
