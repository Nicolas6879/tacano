"""Run the real gate.py on real transcripts cut right before each human prompt. Measures latency, block/hint rates."""
import json, glob, os, pathlib, random, subprocess, sys, tempfile, time, collections, statistics as st
PLUG = pathlib.Path(__file__).resolve().parent.parent / "plugins" / "tacano" / "scripts"
ROOT = pathlib.Path.home() / ".claude" / "projects"
TMP = pathlib.Path(tempfile.mkdtemp(prefix="jevreplay-"))
N = int(sys.argv[1]) if len(sys.argv) > 1 else 120

def text_of(c):
    if isinstance(c, str): return c
    return " ".join(b.get("text", "") for b in c or [] if isinstance(b, dict) and b.get("type") == "text")

points = []; seen = set()
for f in glob.glob(str(ROOT / "*/*.jsonl")):
    lines = open(f, encoding="utf-8", errors="ignore").read().splitlines()
    for i, line in enumerate(lines):
        try: e = json.loads(line)
        except ValueError: continue
        if e.get("type") == "user" and not e.get("isSidechain") and (e.get("origin") or {}).get("kind") == "human":
            t = text_of(e["message"]["content"])
            if t.strip() and not t.startswith("<") and t[:200] not in seen:
                seen.add(t[:200]); points.append((f, i, t, e.get("timestamp")))
random.seed(3); random.shuffle(points); points = points[:N]

res = collections.Counter(); ms = []; hook_ms = []; rows = []
for k, (f, i, prompt, ts) in enumerate(points):
    lines = open(f, encoding="utf-8", errors="ignore").read().splitlines()[:i]
    # shift timestamps so that idle time = real gap before this prompt (hook measures against "now")
    last_ts = None
    for l in reversed(lines):
        try:
            e = json.loads(l)
            if e.get("type") == "assistant": last_ts = e.get("timestamp"); break
        except ValueError: pass
    import datetime as dt
    shift = 0
    if last_ts and ts:
        gap = (dt.datetime.fromisoformat(ts.replace("Z", "+00:00")) - dt.datetime.fromisoformat(last_ts.replace("Z", "+00:00"))).total_seconds()
        shift = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(last_ts.replace("Z", "+00:00"))).total_seconds() - gap
    out_lines = []
    for l in lines:
        try:
            e = json.loads(l)
            if shift and e.get("timestamp"):
                e["timestamp"] = (dt.datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")) + dt.timedelta(seconds=shift)).isoformat().replace("+00:00", "Z")
            out_lines.append(json.dumps(e))
        except ValueError: pass
    tp = TMP / f"t{k}.jsonl"; tp.write_text("\n".join(out_lines), encoding="utf-8")
    env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(TMP / "data"), "PYTHONIOENCODING": "utf-8"}
    t = time.perf_counter()
    p = subprocess.run([sys.executable, str(PLUG / "gate.py")], input=json.dumps({"prompt": prompt, "session_id": f"r{k}", "transcript_path": str(tp), "cwd": "x"}),
                       capture_output=True, text=True, env=env, encoding="utf-8", timeout=30)
    ms.append((time.perf_counter() - t) * 1000)
    rec = json.loads((TMP / "data" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    res[rec.get("action")] += 1; hook_ms.append(rec.get("hook_ms", 0))
    rows.append((rec.get("action"), rec.get("ctx"), rec.get("cold"), prompt[:90].replace("\n", " ")))
    tp.unlink()
print("decisions:", dict(res))
print(f"wall latency incl. python start: p50 {st.median(ms):.0f}ms p90 {sorted(ms)[int(.9*len(ms))]:.0f}ms max {max(ms):.0f}ms")
print(f"in-hook latency: p50 {st.median(hook_ms):.0f}ms p90 {sorted(hook_ms)[int(.9*len(hook_ms))]:.0f}ms")
for r in rows:
    if r[0] != "silent": print(r)
