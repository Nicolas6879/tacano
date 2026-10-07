"""Measure real effect after install: $ per human prompt before vs after, block/override rates, hint follow-through.
usage: python report.py [--since YYYY-MM-DD]   (default: date of the first tacano decision)"""
import collections
import datetime as dt
import glob
import json
import pathlib
import statistics as st
import sys

import jevlib as J

PRICES = {"opus": (4, 20, 0.20, 8), "sonnet": (2, 10, 0.20, 4), "haiku-4": (1, 5, 0.10, 2),
          "haiku": (0.10, 0.50, 0.01, 0.20), "fable": (10, 50, 0.25, 20)}  # in,out,read,write(1h)
HAIKU_LONG = (0.50, 2.50, 0.05, 1.00)  # Haiku 5.5, prompts over 100k tokens


def price(model, prompt_tokens=0):
    m = model or ""
    if "haiku-4" in m:
        return PRICES["haiku-4"]
    if "haiku" in m:
        return HAIKU_LONG if prompt_tokens > 100_000 else PRICES["haiku"]
    for k, v in PRICES.items():
        if k in m:
            return v
    return PRICES["opus"]


def cost(u, model):
    prompt = sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    i, o, r, w = price(model, prompt)
    return ((u.get("input_tokens") or 0) * i + (u.get("output_tokens") or 0) * o
            + (u.get("cache_read_input_tokens") or 0) * r + (u.get("cache_creation_input_tokens") or 0) * w) / 1e6


def main():
    log = []
    try:
        log = [json.loads(l) for l in (J.DATA / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    except OSError:
        pass
    since = None
    if "--since" in sys.argv:
        since = dt.datetime.fromisoformat(sys.argv[sys.argv.index("--since") + 1]).replace(tzinfo=dt.timezone.utc)
    elif log:
        since = dt.datetime.fromisoformat(log[0]["ts"])
    if not since:
        print("No decisions logged yet.")
        return
    usd = {"before": 0.0, "after": 0.0}
    prompts = {"before": 0, "after": 0}
    followed = collections.Counter()
    hinted = {r["session"] for r in log if str(r.get("action", "")).startswith("hint_")}
    for f in glob.glob(str(pathlib.Path.home() / ".claude" / "projects" / "*" / "*.jsonl")):
        sid = pathlib.Path(f).stem
        cur, seen = None, set()
        for line in open(f, encoding="utf-8", errors="ignore"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "user" and (e.get("origin") or {}).get("kind") == "human" and not e.get("isSidechain"):
                ts = dt.datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
                cur = "after" if ts >= since else "before"
                prompts[cur] += 1
            elif e.get("type") == "assistant" and cur:
                m = e.get("message") or {}
                if m.get("id") in seen:
                    continue
                seen.add(m.get("id"))
                usd[cur] += cost(m.get("usage") or {}, m.get("model"))
                if sid in hinted:
                    for b in m.get("content") or []:
                        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("Agent", "Task") \
                                and "worker" in json.dumps(b.get("input", {})):
                            followed[sid] += 1
        # subagent transcripts count toward their session's cost
        for sf in glob.glob(str(pathlib.Path(f).with_suffix("")) + "/subagents/*.jsonl"):
            seen2 = set()
            for line in open(sf, encoding="utf-8", errors="ignore"):
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("type") == "assistant" and e["message"].get("id") not in seen2:
                    seen2.add(e["message"].get("id"))
                    ts = dt.datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
                    usd["after" if ts >= since else "before"] += cost(e["message"].get("usage") or {}, e["message"].get("model"))
    acts = collections.Counter(r.get("action") for r in log)
    print(f"tacano report — since {since:%Y-%m-%d %H:%M} UTC")
    print("decisions:", dict(acts))
    print(f"overrides: {sum(1 for r in log if r.get('override'))}  |  Jev errors: {sum(1 for r in log if 'jev_error' in r)}")
    hm = [r["hook_ms"] for r in log if "hook_ms" in r]
    if hm:
        print(f"hook latency p50 {st.median(hm):.0f} ms, p90 {sorted(hm)[int(.9 * len(hm))]} ms")
    print(f"sessions with a hint: {len(hinted)}, of which spawned a worker: {len(followed)}")
    for k in ("before", "after"):
        print(f"{k:>6}: ${usd[k]:,.2f} over {prompts[k]} human prompts -> ${usd[k] / max(1, prompts[k]):.3f} per prompt")
    print("(compare per-prompt cost on similar work; small 'after' samples are noisy)")


if __name__ == "__main__":
    main()
