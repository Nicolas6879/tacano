"""Run a question-set version over turns, cache answers. usage: python evalrun.py v1 dev|test [limit]"""
import json, sys, hashlib, concurrent.futures as cf, importlib, pathlib, random
import jev
ver, split = sys.argv[1], sys.argv[2]; limit = int(sys.argv[3]) if len(sys.argv)>3 else 10**9
Q = importlib.import_module(f"qs_{ver}")
T = json.load(open("turns3.json",encoding="utf-8"))
def sid(t): return int(hashlib.md5(t["session"].encode()).hexdigest(),16)%3
T = [t for t in T if (sid(t)!=0) == (split=="dev")]          # ~2/3 dev, 1/3 test, split by session
random.seed(7); random.shuffle(T); T=T[:limit]
cache_f = pathlib.Path(f"cache_{ver}.json"); cache = json.loads(cache_f.read_text()) if cache_f.exists() else {}
def key(t): return f'{t["session"]}#{t["turn_idx"]}'
todo=[t for t in T if key(t) not in cache]
def run(t):
    try: return key(t), jev.ask(Q.state(t), Q.QUESTIONS)
    except Exception as e: return key(t), {"error": str(e)}
with cf.ThreadPoolExecutor(6) as ex:
    for k,r in ex.map(run, todo): cache[k]=r
cache_f.write_text(json.dumps(cache))
errs=sum(1 for t in T if "error" in cache[key(t)])
ms=[cache[key(t)].get("_ms",0) for t in T if "_ms" in cache[key(t)]]
tok=[cache[key(t)]["usage"]["input_tokens"] for t in T if "usage" in cache[key(t)]]
print(f"{ver}/{split}: {len(T)} turns, {len(todo)} new calls, {errs} errors, median {sorted(ms)[len(ms)//2] if ms else 0}ms, p90 {sorted(ms)[9*len(ms)//10] if ms else 0}ms, median in-tokens {sorted(tok)[len(tok)//2] if tok else 0}")
