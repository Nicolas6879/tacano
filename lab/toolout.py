"""Size of tool results entering context, by tool, from real transcripts (deduped by tool_use_id)."""
import json, glob, pathlib, collections
ROOT=pathlib.Path.home()/".claude/projects"
name_of={}; sizes=collections.defaultdict(list); seen=set(); samples=collections.defaultdict(list)
def blen(c):
    if isinstance(c,str): return len(c)
    n=0
    for b in c or []:
        if isinstance(b,dict):
            if b.get("type")=="text": n+=len(b.get("text",""))
            elif b.get("type")=="image": n+=6000  # ~1.5k tokens
    return n
for f in glob.glob(str(ROOT/"*/*.jsonl"))+glob.glob(str(ROOT/"*/*/subagents/*.jsonl")):
    for l in open(f,encoding="utf-8",errors="ignore"):
        try:e=json.loads(l)
        except:continue
        c=(e.get("message") or {}).get("content")
        if not isinstance(c,list): continue
        for b in c:
            if not isinstance(b,dict): continue
            if b.get("type")=="tool_use":
                name_of[b["id"]]=(b["name"], json.dumps(b.get("input"))[:200])
            elif b.get("type")=="tool_result" and b.get("tool_use_id") not in seen:
                seen.add(b.get("tool_use_id"))
                n,inp=name_of.get(b.get("tool_use_id"),("?",""))
                key=n if not n.startswith("mcp__") else "mcp:"+n.split("__")[1][:20]+":"+n.split("__")[-1][:22]
                s=blen(b.get("content")); sizes[key].append(s)
                if s>20000 and len(samples[key])<3: samples[key].append(inp[:120])
tot=sum(sum(v) for v in sizes.values())
print(f"total tool-result chars {tot/1e6:.1f}M (~{tot/3.5/1e6:.1f}M tokens) in {sum(len(v) for v in sizes.values())} results")
for k,v in sorted(sizes.items(),key=lambda kv:-sum(kv[1]))[:18]:
    big=sum(x for x in v if x>15000)
    print(f"{k:45} n={len(v):5} total={sum(v)/1e6:6.2f}M share={sum(v)/tot:5.1%} median={sorted(v)[len(v)//2]:6} >15k-chars share={big/max(1,sum(v)):4.0%}")
json.dump({k:v for k,v in sizes.items()},open("toolsizes.json","w"))
