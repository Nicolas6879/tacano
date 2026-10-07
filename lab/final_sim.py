"""End-to-end replay of real sessions with the REAL plugin code (jevlib.compact_decision / route / select_handoff)
and real Jev answers to the plugin's exact questions. Measures $ saved, block frequency and context loss per compaction.
usage: python final_sim.py fetch   -> cache Jev answers for all turns (plugin questions + plugin state builder)
       python final_sim.py grid    -> tune thresholds / handoff budget"""
import json, glob, pathlib, sys, collections, concurrent.futures as cf, itertools, os, re
LAB = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(LAB.parent / "plugins" / "tacano" / "scripts"))
os.environ.setdefault("CLAUDE_PLUGIN_DATA", str(LAB / "_plugindata"))
import jevlib as J
from costsim import P, SUB_BASE, DISPATCH_OUT, RESULT, ALPHA
o = P["opus"]
T = json.load(open(LAB / "turns3.json", encoding="utf-8"))
CACHE = LAB / "cache_final.json"
ROOT = pathlib.Path.home() / ".claude/projects"
def key(t): return f'{t["session"]}#{t["turn_idx"]}'


def fetch():
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = [t for t in T if key(t) not in cache]
    def go(t):
        info = {"prev_prompt": t["prev_prompt"] or "", "last_assistant": t["last_assistant"]}
        try: return key(t), J.ask_jev(J.jev_state(t["prompt"], info), J.QUESTIONS, 20)["answers"]
        except Exception as e: return key(t), {"error": str(e)}
    with cf.ThreadPoolExecutor(6) as ex:
        for k, a in ex.map(go, todo): cache[k] = a
    CACHE.write_text(json.dumps(cache)); print("cached", len(cache), "errors", sum("error" in v for v in cache.values()))


# ---- map each turn to its raw transcript events (for handoff quality) ----
def text(c):
    if isinstance(c, str): return c
    return "\n".join(b.get("text", "") for b in c or [] if isinstance(b, dict) and b.get("type") == "text")

EVENTS = {}
def events(session):
    if session in EVENTS: return EVENTS[session]
    f = next(iter(glob.glob(str(ROOT / "*" / session))), None)
    ev, turn_pos = [], []
    if f:
        for l in open(f, encoding="utf-8", errors="ignore"):
            try: e = json.loads(l)
            except ValueError: continue
            if e.get("isSidechain"): continue
            if e.get("type") == "user" and (e.get("origin") or {}).get("kind") == "human":
                tx = text((e.get("message") or {}).get("content"))
                if tx.strip() and not tx.startswith("<"): turn_pos.append(len(ev))
            if e.get("type") in ("user", "assistant"): ev.append(e)
    EVENTS[session] = (ev, turn_pos); return EVENTS[session]

def segs_of(ev, lo, hi):
    out = []
    for e in ev[lo:hi]:
        c = (e.get("message") or {}).get("content")
        if e.get("type") == "user" and (e.get("origin") or {}).get("kind") == "human":
            tx = text(c)
            if tx.strip() and not tx.startswith("<"): out.append(("user", tx[:2500]))
        elif e.get("type") == "assistant":
            tx = text(c)
            if tx.strip(): out.append(("assistant", tx[:2500]))
            for b in c or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    out.append(("tool", (b.get("name", "") + " " + json.dumps(b.get("input"), ensure_ascii=False))[:400]))
    return out

def after_text(ev, hi, k=30):
    out = []
    for e in ev[hi:hi + 200]:
        c = (e.get("message") or {}).get("content")
        if e.get("type") == "assistant":
            out.append(text(c)); out += [json.dumps(b.get("input"), ensure_ascii=False) for b in c or [] if isinstance(b, dict) and b.get("type") == "tool_use"]
        elif (e.get("origin") or {}).get("kind") == "human":
            out.append(text(c))
        if len(out) > k: break
    return "\n".join(out)

def norm(s): return s.rstrip(".,;:)")

def quality(session, turn_idx, prev_cut_idx, budget, share):
    ev, pos = events(session)
    if turn_idx >= len(pos): return None
    hi = pos[turn_idx]; lo = pos[prev_cut_idx] if prev_cut_idx is not None and prev_cut_idx < len(pos) else 0
    segs = segs_of(ev, lo, hi)
    if not segs: return None
    pre = "\n".join(s[1] for s in segs)
    need = {norm(x) for x in J._ITEM.findall(after_text(ev, hi))} & {norm(x) for x in J._ITEM.findall(pre)}
    need = {x for x in need if len(x) >= 5}
    keep = J.select_handoff(segs, budget, share) if budget else []
    kept = "\n".join(segs[j][1] for j in keep)
    return len(need), sum(x not in kept for x in need)


# ---- replay ----
def simulate(cfg, delegate=True, quality_on=True, policy=True):
    A = json.loads(CACHE.read_text())
    S = collections.defaultdict(list)
    for t in T: S[t["session"]].append(t)
    tot = 0; st = collections.Counter(); misses = []; needs = []
    after_base = 60_000 + cfg["handoff_budget_chars"] // 3.5 + 25_000
    for sid, sess in S.items():
        sess.sort(key=lambda t: t["turn_idx"]); offset = 0; prev_cut = None; floor = 0
        for t in sess:
            calls = t["calls"]; ctx0, cw0 = calls[0][0], calls[0][1]
            cold = cw0 > 0.5 * ctx0
            if ctx0 - offset < 45_000: offset = ctx0 - 45_000
            eff0 = ctx0 - offset
            a = A.get(key(t)) if policy else None
            a = a if a and "error" not in a else None
            dec = J.compact_decision(cfg, eff0, cold, a, 0, floor) if policy else None
            if dec:
                kind, why = dec; st["block_" + kind] += 1; st["why_" + why] += 1
                if kind == "clear":
                    tot += 45_000 * o["write"]; offset = ctx0 - 45_000; prev_cut = t["turn_idx"]; floor = 45_000
                else:
                    tot += (eff0 * o["write"] if cold else eff0 * o["read"]) + 10_000 * o["out"] + after_base * o["write"]
                    if quality_on:
                        q = quality(sid, t["turn_idx"], prev_cut, cfg["handoff_budget_chars"], cfg["handoff_recent_share"])
                        if q and q[0]: needs.append(q[0]); misses.append(q[1])
                    offset = ctx0 - after_base; prev_cut = t["turn_idx"]; floor = after_base
                cold = False; eff0 = ctx0 - offset
            worker = None
            if delegate and a:
                worker, ev_, _ = J.route(cfg, a, eff0)
            growth = max(0, calls[-1][0] - ctx0)
            if worker:
                s = P[worker]; st["deleg_" + worker] += 1
                tot += (eff0 * o["write"] if cold else eff0 * o["read"]) + DISPATCH_OUT * o["out"] + SUB_BASE * s["write"]
                prev = ctx0
                for j in range(max(1, round(len(calls) * ALPHA))):
                    ctx, cw, out, _ = calls[min(int(j / ALPHA), len(calls) - 1)]
                    tot += (SUB_BASE + max(0, ctx - ctx0)) * s["read"] + max(0, ctx - prev) * s["write"] + out * s["out"]; prev = max(prev, ctx)
                tot += (eff0 + DISPATCH_OUT) * o["read"] + RESULT * o["write"] + calls[-1][2] * o["out"]
                offset += growth - RESULT - DISPATCH_OUT
            else:
                for i, (ctx, cw, out, _) in enumerate(calls):
                    if ctx - offset < 45_000: offset = ctx - 45_000
                    eff = ctx - offset; w = eff if (i == 0 and cold) else min(cw, eff)
                    tot += (eff - w) * o["read"] + w * o["write"] + out * o["out"]
    return tot / 1e6, st, needs, misses


def grid():
    base_cfg = J.config()
    b, _, _, _ = simulate(base_cfg, delegate=False, quality_on=False, policy=False)
    n = len(T)
    print(f"baseline ${b:.1f} over {n} prompts")
    rows = []
    for soft, hard, bth, budget in itertools.product((250_000, 300_000, 350_000), (400_000, 450_000, 550_000), (0.6, 0.7, 0.8), (12_000, 24_000, 36_000)):
        if hard <= soft: continue
        cfg = {**base_cfg, "compact_at_tokens": soft, "hard_compact_at_tokens": hard, "boundary_threshold": bth, "handoff_budget_chars": budget}
        c, st, needs, misses = simulate(cfg)
        blocks = st["block_compact"] + st["block_clear"]
        rows.append(dict(soft=soft // 1000, hard=hard // 1000, bth=bth, budget=budget, saved=1 - c / b, blocks=blocks, every=n / max(1, blocks),
                         compactions=st["block_compact"], boundary=st["why_boundary"], miss_per=sum(misses) / max(1, len(misses)),
                         miss_total=sum(misses), need_per=sum(needs) / max(1, len(needs)), deleg=st["deleg_sonnet"] + st["deleg_haiku"]))
    json.dump(rows, open(LAB / "grid_final.json", "w"))
    print(f"{'soft':>4} {'hard':>4} {'bth':>4} {'budget':>6} {'saved':>6} {'1 block/':>8} {'compact':>7} {'@bound':>6} {'need/c':>6} {'miss/c':>6} {'missTot':>7}")
    for r in sorted(rows, key=lambda r: (-round(r["saved"], 2), r["miss_total"]))[:25]:
        print(f"{r['soft']:4} {r['hard']:4} {r['bth']:4} {r['budget']:6} {r['saved']:6.1%} {r['every']:8.0f} {r['compactions']:7} {r['boundary']:6} {r['need_per']:6.1f} {r['miss_per']:6.1f} {r['miss_total']:7}")


if __name__ == "__main__":
    {"fetch": fetch, "grid": grid}[sys.argv[1]]()
