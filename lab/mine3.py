"""Add hook-visible context (last assistant text, gap minutes) to each turn; redact secrets."""
import json, glob, os, pathlib, re, datetime as dt
ROOT = pathlib.Path.home()/".claude/projects"
SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{10,}|apify_api_\w+|apikey\w{8,}|ghp_\w{20,}|AKIA[0-9A-Z]{12,}|eyJ[\w\-]{20,}\.[\w\-]+\.[\w\-]+|0x[a-fA-F0-9]{60,}|(?i:(?:token|key|secret|password)\s*[=:]\s*)\S{8,})")
def red(s): return SECRET.sub("[REDACTED]", s or "")
def text_of(c):
    if isinstance(c,str): return c
    return " ".join(b.get("text","") for b in c if isinstance(b,dict) and b.get("type")=="text")
def ts(s): return dt.datetime.fromisoformat(s.replace("Z",""))
T=json.load(open("turns2.json",encoding="utf-8"))
idx={}
for f in glob.glob(str(ROOT/"*/*.jsonl")):
    last_asst=""; last_ts=None; prev_user=None; seen=set(); n=0
    for line in open(f,encoding="utf-8",errors="ignore"):
        try:e=json.loads(line)
        except:continue
        if e.get("type")=="user" and not e.get("isSidechain") and (e.get("origin") or {}).get("kind")=="human":
            t=text_of(e["message"]["content"])
            if t.strip() and not t.startswith("<"):
                gap=(ts(e["timestamp"])-ts(last_ts)).total_seconds()/60 if last_ts else None
                idx[(os.path.basename(f),n)]={"last_assistant":red(last_asst[-700:]),"gap_min":gap}
                n+=1
        elif e.get("type")=="assistant":
            t=text_of(e["message"].get("content") or [])
            if t.strip(): last_asst=t
            last_ts=e.get("timestamp")
for t in T:
    t.update(idx.get((t["session"],t["turn_idx"]),{"last_assistant":"","gap_min":None}))
    t["prompt"]=red(t["prompt"]); t["prev_prompt"]=red(t["prev_prompt"])
json.dump(T,open("turns3.json","w",encoding="utf-8"),ensure_ascii=False)
print(sum(1 for t in T if t["last_assistant"]), "of", len(T), "have assistant context")
