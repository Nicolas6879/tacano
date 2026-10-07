"""When to compact: fixed threshold vs Jev-detected task boundary.
Metric: after the cut, how many specific items from BEFORE the cut are needed in the next K messages (dependency),
and how many of them a 6k-token recency handoff + nothing else would miss. Lower = better place to cut."""
import json, glob, pathlib, re, collections, concurrent.futures as cf, sys, hashlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import jev
from handoff_sim import load, segments_and_after, ITEM, norm, text
ROOT = pathlib.Path.home() / ".claude/projects"
LO, HI, FIXED = 250_000, 450_000, 300_000
BQ = {"boundary": {"type": "noul", "instructions": "Is `new_user_message` the start of a new task or phase (the previous piece of work looks finished, or the user switches to something different), so that this is a good moment to summarize and wipe the detailed history? Answer no if it continues, fixes or refines what was just being done."},
      "fresh": {"type": "noul", "instructions": "Could `new_user_message` be handled correctly with no memory of the conversation at all?"}}

def ctx_of(e):
    u = (e.get("message") or {}).get("usage") or {}
    return (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)

def dependency(ev, cut):
    segs, after = segments_and_after(ev, cut)
    pre = "\n".join(s[1] for s in segs)
    need = {norm(x) for x in ITEM.findall(after)} & {norm(x) for x in ITEM.findall(pre)}
    need = {x for x in need if len(x) >= 5}
    rec, used = [], 0
    for s in reversed(segs):
        if used + len(s[1]) > 24000: break
        rec.append(s[1]); used += len(s[1])
    missed = {x for x in need if x not in "\n".join(rec)}
    return len(need), len(missed)

rows = []; seen = set()
for f in glob.glob(str(ROOT / "*/*.jsonl")):
    ev = load(f); ctx = 0; cands = []
    key = hashlib.md5(json.dumps(ev[:3], default=str)[:2000].encode()).hexdigest()
    if key in seen: continue
    seen.add(key)
    last_asst = ""; prev_user = ""
    for i, (t, e) in enumerate(ev):
        if t == "system": ctx = 0
        if t == "assistant":
            c = ctx_of(e); ctx = c or ctx
            tx = text((e.get("message") or {}).get("content"))
            if tx.strip(): last_asst = tx
        if t == "user" and (e.get("origin") or {}).get("kind") == "human" and not e.get("isCompactSummary"):
            tx = text((e.get("message") or {}).get("content"))
            if tx.strip() and not tx.startswith("<") and LO <= ctx <= HI:
                cands.append((i, ctx, tx, last_asst, prev_user))
            if tx.strip(): prev_user = tx
        if ctx > HI: break
    if len(cands) < 3: continue
    fixed = next((c for c in cands if c[1] >= FIXED), cands[-1])
    def ask(c):
        i, cx, tx, la, pu = c
        r = jev.ask({"conversation": {"previous_user_message": pu[:300], "last_assistant_message": la[-700:]}, "new_user_message": tx[:1200]}, BQ)
        return r["answers"]["boundary"]["noul"], r["answers"]["fresh"]["noul"]
    with cf.ThreadPoolExecutor(6) as ex: probs = list(ex.map(ask, cands))
    jb = next((c for c, p in zip(cands, probs) if p[0] >= 0.7 and c[1] >= FIXED), cands[-1])
    best_rand = None
    nf, mf = dependency(ev, fixed[0]); nj, mj = dependency(ev, jb[0])
    rows.append((nf, mf, nj, mj, fixed[1], jb[1]))
    print(f"{pathlib.Path(f).stem[:8]} cands {len(cands):3} | fixed@{fixed[1]//1000}k needs {nf:3} (recency-handoff misses {mf:3}) | jev@{jb[1]//1000}k needs {nj:3} (misses {mj:3})")
rows = list({r: None for r in rows})  # drop duplicated sessions (identical outcomes)
if rows:
    n = len(rows)
    print(f"\nsessions {n}: mean items needed from history after cut: fixed {sum(r[0] for r in rows)/n:.1f} vs jev-boundary {sum(r[2] for r in rows)/n:.1f}")
    print(f"mean items a 6k recency handoff would still miss: fixed {sum(r[1] for r in rows)/n:.1f} vs jev-boundary {sum(r[3] for r in rows)/n:.1f}")
    print(f"jev better in {sum(r[3] < r[1] for r in rows)}, equal {sum(r[3] == r[1] for r in rows)}, worse {sum(r[3] > r[1] for r in rows)}")
