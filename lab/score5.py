import json, sys, hashlib, itertools
from costsim import inline_cost, delegated_cost
from policy import expected_saving
import gold; GOLD=gold.GOLD
ver=sys.argv[1]; split=sys.argv[2]
cache=json.load(open(f"cache_{ver}.json")); T=json.load(open("turns3.json",encoding="utf-8"))
def sid(t): return int(hashlib.md5(t["session"].encode()).hexdigest(),16)%3
rows=[(f'{t["session"]}#{t["turn_idx"]}',t) for t in T if (sid(t)!=0)==(split=="dev")]
rows=[(k,t,cache[k]["answers"]) for k,t in rows if k in cache and "answers" in cache[k]]
def decide(a,t,P):
    mf=a["model_fit"]["probabilities"]; it=a["intent"]["choice"]
    if a["sensitive"]["noul"]>=P["sens"] or mf["keep_opus"]>=P["opus"]: return "inline"
    if P["chat_inline"] and it=="chat": return "inline"
    m="haiku" if (mf["haiku"]>=P["h"] and (not P["h_intent"] or it in ("continue_plan","lookup"))) else "sonnet"
    ev=expected_saving(a["volume"]["probabilities"], t["calls"][0][0], m, t["calls"][0][1])
    return m if ev>P["margin"] else "inline"
base=sum(inline_cost(t) for _,t,_ in rows); res=[]
for sens,opus,chat,h,hi,margin in itertools.product((0.3,0.5),(0.3,0.4,0.5),(True,False),(0.7,0.8,0.9,1.01),(True,False),(0,0.05)):
    P=dict(sens=sens,opus=opus,chat_inline=chat,h=h,h_intent=hi,margin=margin)
    cost=0;nd=nh=0;err=0;unl=[]
    for k,t,a in rows:
        d=decide(a,t,P)
        if d=="inline": cost+=inline_cost(t); continue
        cost+=delegated_cost(t,d); nd+=1; nh+=d=="haiku"; g=GOLD.get(k)
        if g is None: unl.append(k)
        elif g=="X" or (d=="haiku" and g=="S"): err+=1
    res.append((1-cost/base,err,nd,nh,len(unl),P,unl))
if split=="dev":
    for s,e,nd,nh,u,P,_ in sorted(res,key=lambda r:(-r[0]))[:400]:
        if e<=2: print(f"saved {s:+.1%} err {e}/{nd} haiku {nh} unl {u} {P}")
