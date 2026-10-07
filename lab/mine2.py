"""Per human turn: real per-call context sizes, so we can simulate inline vs delegated cost."""
import json, glob, os, pathlib
ROOT = pathlib.Path.home()/".claude/projects"
out=[]
def text_of(c):
    if isinstance(c,str): return c
    return " ".join(b.get("text","") for b in c if isinstance(b,dict) and b.get("type")=="text")
for f in glob.glob(str(ROOT/"*/*.jsonl")):
    sess=[]; cur=None; seen=set(); prev_prompt=None
    for line in open(f,encoding="utf-8",errors="ignore"):
        try: e=json.loads(line)
        except: continue
        if e.get("type")=="user" and not e.get("isSidechain") and (e.get("origin") or {}).get("kind")=="human":
            t=text_of(e["message"]["content"])
            if t.strip() and not t.startswith("<"):
                cur={"project":pathlib.Path(f).parent.name,"session":os.path.basename(f),"prompt":t[:2000],"prev_prompt":prev_prompt,"calls":[],"tools":[],"spawns":0}
                prev_prompt=t[:300]; sess.append(cur)
        elif e.get("type")=="assistant" and cur is not None:
            m=e.get("message",{})
            if m.get("id") not in seen:
                seen.add(m.get("id")); u=m.get("usage") or {}
                ctx=(u.get("input_tokens") or 0)+(u.get("cache_read_input_tokens") or 0)+(u.get("cache_creation_input_tokens") or 0)
                cur["calls"].append([ctx,u.get("cache_creation_input_tokens") or 0,u.get("output_tokens") or 0,m.get("model","?")])
            for b in m.get("content") or []:
                if isinstance(b,dict) and b.get("type")=="tool_use":
                    cur["tools"].append(b.get("name"))
                    if b.get("name") in ("Agent","Task"): cur["spawns"]+=1
    total=sum(len(t["calls"]) for t in sess); done=0
    for i,t in enumerate(sess):
        done+=len(t["calls"]); t["future_calls"]=total-done; t["turn_idx"]=i
    out+= [t for t in sess if t["calls"]]
json.dump(out,open("turns2.json","w",encoding="utf-8"),ensure_ascii=False)
print(len(out))
