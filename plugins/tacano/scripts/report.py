"""Measure what Tacaño did: per-action savings (followed delegation hints, compactions), hint follow-through,
block/override rates, plus the raw $ per human prompt before vs after install (confounded by the kind of work).
usage: python report.py [--since YYYY-MM-DD]   (default: date of the first tacano decision)"""
import collections
import datetime as dt
import glob
import json
import pathlib
import statistics as st
import sys

import jevlib as J
import prices as PR

PRICES = PR.PRICES          # single table (prices.py); kept here as a name for old callers
price = PR.price            # (in, out, read, write) for a model id; unknown -> Opus
UNITS = "API-equivalent units, not your Pro quota"


def cost(u, model):
    prompt = sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    i, o, r, w = price(model, prompt)
    return ((u.get("input_tokens") or 0) * i + (u.get("output_tokens") or 0) * o
            + (u.get("cache_read_input_tokens") or 0) * r + (u.get("cache_creation_input_tokens") or 0) * w) / 1e6


def _ts(x):
    return dt.datetime.fromisoformat(x.replace("Z", "+00:00"))


def _usage_ctx(u):
    return sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def load_session(f):
    """Human prompt times, main-thread calls (ts, ctx, usage, model), compaction times, subagent runs."""
    humans, calls, compactions, seen = [], [], [], set()
    for line in open(f, encoding="utf-8", errors="ignore"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("isSidechain") or "timestamp" not in e:
            continue
        if e.get("type") == "user" and (e.get("origin") or {}).get("kind") == "human":
            humans.append(_ts(e["timestamp"]))
        elif e.get("type") == "system" and e.get("subtype") == "compact_boundary":
            compactions.append(_ts(e["timestamp"]))
        elif e.get("type") == "assistant":
            m = e.get("message") or {}
            if m.get("id") in seen:
                continue
            seen.add(m.get("id"))
            u = m.get("usage") or {}
            calls.append((_ts(e["timestamp"]), _usage_ctx(u), u, m.get("model")))
    subs = []
    for sf in glob.glob(str(pathlib.Path(f).with_suffix("")) + "/subagents/*.jsonl"):
        runs, seen2 = [], set()
        for line in open(sf, encoding="utf-8", errors="ignore"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "assistant" and e["message"].get("id") not in seen2:
                seen2.add(e["message"].get("id"))
                u = e["message"].get("usage") or {}
                runs.append((_ts(e["timestamp"]), _usage_ctx(u), u, e["message"].get("model")))
        if runs:
            subs.append(runs)
    return humans, calls, compactions, subs


def opus_cost(u):
    return cost(u, "opus")


def base_model_at(calls, t):
    """Model of the closest main-thread call at or before t (a real model, not '<synthetic>'); None -> Opus later."""
    best = None
    for ts, _, _, m in calls:
        if ts <= t and PR.family(m):
            if best is None or ts >= best[0]:
                best = (ts, m)
    return best[1] if best else None


def _acc(d, fam, n, usd):
    e = d.setdefault(fam, [0, 0.0])
    e[0] += n
    e[1] += usd


def compute_savings(log, sess):
    """Per-action savings against a counterfactual priced at the model the main session actually used
    (nearest earlier main-thread call; Opus if unknown). A delegation that cost MORE than the counterfactual
    shows as negative. sess(sid) -> load_session tuple."""
    hints = [r for r in log if str(r.get("action", "")).startswith("hint_")]
    followed, deleg_saved, by_base_deleg = 0, 0.0, {}
    for r in hints:
        humans, calls, _, subs = sess(r["session"])
        t = dt.datetime.fromisoformat(r["ts"])
        start = next((h for h in humans if h >= t - dt.timedelta(seconds=5)), None)
        if not start:
            continue
        end = next((h for h in humans if h > start), dt.datetime.max.replace(tzinfo=dt.timezone.utc))
        ran = []
        for runs in subs:
            if not (start <= runs[0][0] < end):
                continue
            bm = base_model_at(calls, runs[0][0])
            if price(runs[0][3])[1] < price(bm)[1]:     # worker output rate below the base's: a cheaper worker
                ran.append((runs, bm))
        if not ran:
            continue
        followed += 1
        main_ctx = max((c for ts, c, _, _ in calls if ts <= ran[0][0][0][0]), default=0)
        for runs, bm in ran:
            ctx0 = runs[0][1]
            # Counterfactual: the same calls done by the base model inside the main context (bigger than the worker's).
            inline = sum(cost(u, bm) + max(0, main_ctx - ctx0) * price(bm, main_ctx)[2] / 1e6 for _, _, u, _ in runs)
            saved = inline - sum(cost(u, m) for _, _, u, m in runs)
            deleg_saved += saved
            _acc(by_base_deleg, PR.base_family(bm), 1, saved)

    # ---- compactions Tacano asked for: lighter calls afterwards minus the cost of compacting ----
    blocks = [r for r in log if r.get("action") == "block_compact"]
    done, comp_saved, by_base_comp = 0, 0.0, {}
    for r in blocks:
        humans, calls, compactions, _ = sess(r["session"])
        t = dt.datetime.fromisoformat(r["ts"])
        cb = next((c for c in compactions if t <= c <= t + dt.timedelta(minutes=30)), None)
        if not cb:
            continue
        done += 1
        nxt = next((c for c in compactions if c > cb), dt.datetime.max.replace(tzinfo=dt.timezone.utc))
        after = [c for ts, c, _, _ in calls if cb < ts < nxt]
        if not after:
            continue
        bm = base_model_at(calls, t)
        o_in, o_out, o_read, o_write = price(bm, r["ctx"])
        switch = (r["ctx"] * o_read + 10_000 * o_out + after[0] * o_write) / 1e6
        saved = sum(max(0, r["ctx"] - c) for c in after) * o_read / 1e6 - switch
        comp_saved += saved
        _acc(by_base_comp, PR.base_family(bm), 1, saved)
    return {"hints": len(hints), "followed": followed, "deleg_saved": deleg_saved, "by_base_deleg": by_base_deleg,
            "blocks": len(blocks), "done": done, "comp_saved": comp_saved, "by_base_comp": by_base_comp}


def main():
    # Windows consoles default to cp1252; never crash on a character it can't print.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    log = []
    try:
        log = [json.loads(l) for l in (J.DATA / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    except OSError:
        pass
    if not log:
        print("No decisions logged yet.")
        return
    installed = dt.datetime.fromisoformat(log[0]["ts"])
    since = installed
    if "--since" in sys.argv:
        since = dt.datetime.fromisoformat(sys.argv[sys.argv.index("--since") + 1]).replace(tzinfo=dt.timezone.utc)
    log = [r for r in log if dt.datetime.fromisoformat(r["ts"]) >= since]
    files = {pathlib.Path(f).stem: f for f in glob.glob(str(pathlib.Path.home() / ".claude" / "projects" / "*" / "*.jsonl"))}
    sessions = {}

    def sess(sid):
        if sid not in sessions:
            sessions[sid] = load_session(files[sid]) if sid in files else ([], [], [], [])
        return sessions[sid]

    sv = compute_savings(log, sess)
    hints, followed, deleg_saved = sv["hints"], sv["followed"], sv["deleg_saved"]
    blocks, done, comp_saved = sv["blocks"], sv["done"], sv["comp_saved"]
    clears = [r for r in log if r.get("action") == "block_clear"]
    cfg = J.config()
    clear_model = sum(J.switch_saving(cfg, r["ctx"], "clear", None, r.get("base", "opus")) for r in clears)

    # ---- raw before/after (all Claude Code spend; depends on what you worked on) ----
    usd = {"before": 0.0, "after": 0.0}
    prompts = {"before": 0, "after": 0}
    for sid in files:
        humans, calls, _, subs = sess(sid)
        for h in humans:
            if h < installed or h >= since:
                prompts["after" if h >= since else "before"] += 1
        for ts, _, u, m in calls + [c for runs in subs for c in runs]:
            if ts < installed or ts >= since:
                usd["after" if ts >= since else "before"] += cost(u, m)

    acts = collections.Counter(r.get("action") for r in log)
    print(f"tacano report — since {since:%Y-%m-%d %H:%M} UTC (installed {installed:%Y-%m-%d})")
    print("decisions:", dict(acts))
    print(f"overrides: {sum(1 for r in log if r.get('override'))}  |  Jev errors: {sum(1 for r in log if 'jev_error' in r)}")
    hm = [r["hook_ms"] for r in log if "hook_ms" in r]
    if hm:
        print(f"hook latency p50 {st.median(hm):.0f} ms, p90 {sorted(hm)[int(.9 * len(hm))]} ms")
    print()
    print("What Tacano saved (estimates, per action):")
    print(f"  delegation hints: {hints}, followed {followed} -> ~${deleg_saved:,.2f} vs doing that work in the session's own model")
    print(f"  /compact blocks:  {blocks}, compacted {done} -> ~${comp_saved:,.2f} in lighter calls after, net of compaction cost")
    print(f"  /clear blocks:    {len(clears)} -> ~${clear_model:,.2f} modeled (the new session isn't linked to the old one)")
    print(f"  total ~${deleg_saved + comp_saved + clear_model:,.2f}   ({UNITS})")
    fams = sorted(set(sv["by_base_deleg"]) | set(sv["by_base_comp"]))
    if fams:
        print("  by base model (the model the session was running):")
        for f in fams:
            dn, dv = sv["by_base_deleg"].get(f, [0, 0.0])
            cn, cv = sv["by_base_comp"].get(f, [0, 0.0])
            print(f"    {f:>8}: delegations {dn} -> ${dv:,.2f}  |  compactions {cn} -> ${cv:,.2f}")
    print()
    print(f"Raw spend per human prompt (all Claude Code use, not attributable to Tacano alone; {UNITS}):")
    for k in ("before", "after"):
        label = "before install" if k == "before" else "since"
        print(f"  {label:>14}: ${usd[k]:,.2f} over {prompts[k]} prompts -> ${usd[k] / max(1, prompts[k]):.3f} per prompt")
    print("  (only meaningful when the work before and after is similar)")


if __name__ == "__main__":
    main()
