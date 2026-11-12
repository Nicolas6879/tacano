"""Read-only calibration helper for worker_base_tokens (the subagent cold-start size).
For every subagents/*.jsonl under the projects folder, take the FIRST assistant call and report the median of
input_tokens + cache_creation_input_tokens per agent type (from the sibling .meta.json) and per model family.
Prints aggregates only: never prompt or message content. Writes nothing.
usage: python coldstart.py [projects_dir]   (default ~/.claude/projects)"""
import glob
import json
import pathlib
import statistics as st
import sys

import prices as PR


def first_call_tokens(path):
    """input + cache_creation (+ separately cache_read) of the first assistant usage in a subagent transcript."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("type") == "assistant":
                    m = e.get("message") or {}
                    u = m.get("usage") or {}
                    return ((u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0),
                            u.get("cache_read_input_tokens") or 0, m.get("model"))
    except OSError:
        pass
    return None


def agent_type(path):
    try:
        return json.loads(pathlib.Path(path).with_suffix(".meta.json").read_text(encoding="utf-8")).get("agentType") or "?"
    except (OSError, ValueError):
        return "?"


def collect(root):
    by_type, by_fam = {}, {}
    for f in glob.glob(str(pathlib.Path(root) / "*" / "*" / "subagents" / "*.jsonl")):
        r = first_call_tokens(f)
        if not r or not (r[0] or r[1]):
            continue
        by_type.setdefault(agent_type(f), []).append(r[0])
        by_fam.setdefault(PR.base_family(r[2]), []).append(r[0])
    return by_type, by_fam


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else str(pathlib.Path.home() / ".claude" / "projects")
    by_type, by_fam = collect(root)
    for title, d in (("agent type", by_type), ("model family", by_fam)):
        print(f"first-call input+cache_creation tokens by {title} (config default worker_base_tokens = 30000):")
        for k, v in sorted(d.items()):
            print(f"  {k:<32} n={len(v):<4} median={st.median(v):>9,.0f}  p10={sorted(v)[len(v) // 10]:>9,}  p90={sorted(v)[min(len(v) - 1, int(len(v) * .9))]:>9,}")


if __name__ == "__main__":
    main()
