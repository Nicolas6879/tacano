"""Does a Jev-selected verbatim handoff preserve what is needed after compaction?
Ground truth: specific items (paths, URLs, ids, identifiers, numbers) that appeared BEFORE the cut and were used AFTER it
(in the next K post-cut user prompts / assistant messages / tool inputs). Recall = share of those items present in preserved text.
Cut points: (a) the 10 real compactions, with Claude's real summary; (b) synthetic cuts at ~400k in long uncompacted sessions."""
import json, glob, pathlib, re, collections, concurrent.futures as cf, sys, hashlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import jev
ROOT = pathlib.Path.home() / ".claude/projects"
ITEM = re.compile(r"(?:https?://[^\s\"'<>)\]]+|[A-Za-z]:[\\/][^\s\"'<>|]+|(?:\.{0,2}/)?(?:[\w\-]+/)+[\w\-.]+|\b0\.0\.\d{3,}\b|\b[\w\-]+\.(?:py|js|ts|tsx|jsx|json|md|html|css|sql|php|java|go|rs|env|yml|yaml|toml|csv|pdf|sh|ps1)\b|\b[a-z]+(?:[A-Z][a-z0-9]+)+\b|\b[a-z0-9]+(?:_[a-z0-9]+){1,}\b|\b\d{5,}\b)")
K = 15; BUDGET = 24000  # handoff budget in chars (~3k tokens)

def text(c):
    if isinstance(c, str): return c
    return "\n".join(b.get("text", "") for b in c or [] if isinstance(b, dict) and b.get("type") == "text")

def norm(s): return s.rstrip(".,;:)").replace("\\\\", "\\")

def segments_and_after(ev, cut):
    """segments: (kind, text) before cut; after: concatenated text used after cut."""
    segs = []
    for typ, e in ev[:cut]:
        c = (e.get("message") or {}).get("content")
        if typ == "user" and (e.get("origin") or {}).get("kind") == "human":
            t = text(c)
            if t.strip() and not t.startswith("<"): segs.append(("user", t[:2500]))
        elif typ == "assistant":
            t = text(c)
            if t.strip(): segs.append(("assistant", t[:2500]))
            for b in c or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    segs.append(("tool", (b["name"] + " " + json.dumps(b.get("input"), ensure_ascii=False))[:600]))
    after = []; n = 0
    for typ, e in ev[cut:]:
        c = (e.get("message") or {}).get("content")
        if typ == "user" and (e.get("origin") or {}).get("kind") == "human" and not e.get("isCompactSummary"):
            after.append(text(c)); n += 1
        elif typ == "assistant":
            after.append(text(c)); after += [json.dumps(b.get("input"), ensure_ascii=False) for b in c or [] if isinstance(b, dict) and b.get("type") == "tool_use"]
            n += 1
        if n >= 2 * K: break
    return segs, "\n".join(after)

def load(f):
    ev = []
    for l in open(f, encoding="utf-8", errors="ignore"):
        try: e = json.loads(l)
        except ValueError: continue
        if e.get("isSidechain"): continue
        if e.get("type") in ("user", "assistant") or (e.get("type") == "system" and e.get("subtype") == "compact_boundary"):
            ev.append((e.get("type"), e))
    return ev

Q = {"type": "score",
     "instructions": "The agent's memory of this long work session is about to be wiped and replaced by a short summary. `segment` is one piece of the history; `current_task` is what the user asked most recently. How important is it to keep `segment` VERBATIM so the agent can continue the work correctly?",
     "criteria": ["Irrelevant now: chit-chat, superseded attempts, progress narration, resolved errors.",
                  "Minor: background that is easy to rediscover by looking at files.",
                  "Useful: concrete details likely needed soon (paths, commands, ids, URLs, config values).",
                  "Critical: a decision, constraint, user preference/requirement, credential location, pending task or exact value the agent must not lose."]}

def jev_scores(segs, current):
    out = [0.0] * len(segs)
    def batch(lo):
        qs = {f"s{j}": {**Q, "instructions": Q["instructions"].replace("`segment`", f"`segments[{j - lo}]`")} for j in range(lo, min(lo + 20, len(segs)))}
        r = jev.ask({"current_task": current[:600], "segments": [s[1][:900] for s in segs[lo:lo + 20]]}, qs)
        return {int(k[1:]): v["score"] for k, v in r["answers"].items()}
    with cf.ThreadPoolExecutor(6) as ex:
        for d in ex.map(batch, range(0, len(segs), 20)):
            for j, s in d.items(): out[j] = s
    return out

def pick(segs, order):
    chosen, used = [], 0
    for j in dict.fromkeys(order):
        t = segs[j][1]
        if used + len(t) > BUDGET: continue
        chosen.append(j); used += len(t)
    return "\n".join(segs[j][1] for j in sorted(chosen))

DEC = re.compile(r"(?i)\b(no |nunca|siempre|usa |use |prefer|quiero|decid|important|must|don't|do not|no pongas|clave|key|token|\.env|deadline|plazo)")

def main():
    cases = []; seenkey = set()
    for f in glob.glob(str(ROOT / "*/*.jsonl")):
        ev = load(f)
        idx = [i for i, (t, e) in enumerate(ev) if t == "system"]
        for i in idx:  # real compactions
            summ = ""
            for t, e in ev[i + 1:i + 4]:
                if e.get("isCompactSummary"): summ = text((e.get("message") or {}).get("content")); break
            segs, after = segments_and_after(ev, i)
            key = hashlib.md5((segs[-1][1] if segs else "").encode()).hexdigest()
            if summ and key not in seenkey: seenkey.add(key); cases.append(("real", f, segs, after, summ))
        if not idx:  # synthetic cut at ~400k
            for i, (t, e) in enumerate(ev):
                u = (e.get("message") or {}).get("usage") if t == "assistant" else None
                if u and (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) > 400_000:
                    segs, after = segments_and_after(ev, i + 1)
                    key = hashlib.md5((segs[-1][1] if segs else "").encode()).hexdigest()
                    if key not in seenkey and len(after) > 2000: seenkey.add(key); cases.append(("synthetic", f, segs, after, ""))
                    break
    print("cases:", collections.Counter(c[0] for c in cases))

    rows = []
    for kind, f, segs, after, summ in cases:
        if len(segs) < 10: continue
        segs = segs[-400:]
        pre = "\n".join(s[1] for s in segs)
        need = {norm(x) for x in ITEM.findall(after)} & {norm(x) for x in ITEM.findall(pre)}
        need = {x for x in need if len(x) >= 5}
        if len(need) < 3: continue
        current = next((s[1] for s in reversed(segs) if s[0] == "user"), "")
        sc = jev_scores(segs, current)
        n = len(segs)
        methods = {
            "recency": list(range(n - 1, -1, -1)),
            "heuristic": sorted(range(n), key=lambda j: -(len(ITEM.findall(segs[j][1])) + 3 * bool(DEC.search(segs[j][1])) + (segs[j][0] == "user") * 2 + j / n)),
            "jev": sorted(range(n), key=lambda j: -(sc[j] + 0.3 * j / n)),
            "jev+items": sorted(range(n), key=lambda j: -(sc[j] + 0.25 * min(4, len(ITEM.findall(segs[j][1]))) + 0.3 * j / n)),
        }
        def hybrid(frac, use_jev=True):
            rb = int(BUDGET * frac); ch = []; used = 0
            for j in range(n - 1, -1, -1):
                if used + len(segs[j][1]) > rb: break
                ch.append(j); used += len(segs[j][1])
            rest = [j for j in (sorted(range(n), key=lambda j: -(sc[j] + 0.25 * min(4, len(ITEM.findall(segs[j][1]))))) if use_jev else
                                sorted(range(n), key=lambda j: -(len(ITEM.findall(segs[j][1])) + 3 * bool(DEC.search(segs[j][1])))) ) if j not in ch]
            for j in rest:
                if used + len(segs[j][1]) > BUDGET: continue
                ch.append(j); used += len(segs[j][1])
            return ch
        for fr in (0.5, 0.7):
            methods[f"hyb{int(fr*100)}-jev"] = hybrid(fr) + list(range(n))
            methods[f"hyb{int(fr*100)}-heur"] = hybrid(fr, False) + list(range(n))
        rec = {"kind": kind, "need": len(need)}
        rec["summary_only"] = sum(x in summ for x in need) / len(need) if summ else None
        for name, order in methods.items():
            h = pick(segs, order)
            rec[name] = sum(x in (summ + "\n" + h) for x in need) / len(need)
        rows.append(rec); print(rec)
    print("\nMEAN RECALL of items needed after the cut (handoff budget ~3k tokens):")
    for kind in ("real", "synthetic"):
        R = [r for r in rows if r["kind"] == kind]
        if not R: continue
        keys = (["summary_only"] if kind == "real" else []) + ["recency", "jev", "hyb50-jev", "hyb50-heur", "hyb70-jev", "hyb70-heur"]
        label = "summary + " if kind == "real" else "handoff only: "
        print(f"{kind} cuts (n={len(R)}): " + "  ".join(f"{('' if k == 'summary_only' else label) + k} {sum(r[k] for r in R) / len(R):.0%}" for k in keys))


if __name__ == "__main__":
    main()
