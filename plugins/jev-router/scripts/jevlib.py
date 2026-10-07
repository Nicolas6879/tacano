"""Shared logic for the jev-router hooks: config, Jev client, transcript reading, cost model, policy, state, logs.
Stdlib only. Every public entry point must fail open: a bug here must never block the user."""
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = pathlib.Path(os.environ.get("CLAUDE_PLUGIN_DATA") or (pathlib.Path.home() / ".claude" / "jev-router-data"))
DATA.mkdir(parents=True, exist_ok=True)

DEFAULTS = {
    "lang": "es",
    "compact_at_tokens": 250_000,      # warm context: from here, suggest /compact only at a task boundary (Jev)
    "hard_compact_at_tokens": 550_000, # warm context: suggest /compact regardless of boundary
    "cold_compact_at_tokens": 300_000, # cache expired (>55 min idle): suggest above this
    # ^ tuned in lab/final_sim.py on 500 real turns: 39.1% saved, 1 block per ~38 prompts, fewest lost items
    "cold_after_minutes": 55,
    "boundary_threshold": 0.7,         # Jev P(new task/phase) to treat this prompt as a good compaction point
    "fresh_threshold": 0.8,            # Jev P(fresh session handles it) to suggest /clear instead of /compact
    "handoff_budget_chars": 48_000,    # verbatim handoff kept across /compact (~13k tokens)
    "handoff_recent_share": 0.5,       # share of the budget for the most recent history; rest = older critical bits
    "sensitive_threshold": 0.3,
    "keep_opus_threshold": 0.3,
    "haiku_threshold": 0.8,
    "min_expected_saving_usd": 0.05,
    "override_window_minutes": 20,     # resend the same prompt within this window to bypass a block
    "snooze_tokens": 100_000,          # after a bypass, don't block again until context grows this much
    "jev_timeout_s": 4.0,
    "enabled": True,
    # $/MTok; 1h cache writes = 2x input. Source: Anthropic pricing (claude-api skill, 2026-09-25).
    "prices": {"opus": {"out": 20, "read": 0.20, "write": 8},
               "sonnet": {"out": 10, "read": 0.20, "write": 4},
               "haiku": {"out": 5, "read": 0.10, "write": 2}},
    # Calibrated on 500 real turns (lab/): calls per volume bucket, ctx growth per call, worker overheads.
    "calls_per_bucket": {"none": 1, "few": 2, "moderate": 5, "heavy": 17},
    "growth_per_call": 1600, "out_per_call": 450, "future_calls_cap": 52,
    "worker_base_tokens": 30_000, "dispatch_tokens": 700, "result_tokens": 1500, "worker_call_factor": 1.3,
}


def config():
    cfg = dict(DEFAULTS)
    for p in (ROOT / "config.json", DATA / "config.json"):
        try:
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return cfg


# ---------- secrets ----------
def api_key():
    k = os.environ.get("TYPESAFE_API_KEY")
    if k:
        return k.strip()
    for p in (DATA / "typesafe.env", pathlib.Path.home() / ".typesafe.env", pathlib.Path.home() / ".typesafe.env.txt"):
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("TYPESAFE_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return None


_SECRET = re.compile(
    r"(sk-[A-Za-z0-9_\-]{10,}|apify_api_\w+|apikey\w{8,}|gh[pousr]_\w{20,}|AKIA[0-9A-Z]{12,}|xox[abp]-[\w-]{10,}"
    r"|eyJ[\w\-]{20,}\.[\w\-]+\.[\w\-]+|0x[a-fA-F0-9]{60,}|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END"
    r"|(?i:(?:token|api[_-]?key|secret|password|passwd|private[_-]?key)\s*[=:]\s*)\S{8,})")


def redact(s):
    return _SECRET.sub("[REDACTED]", s or "")


# ---------- transcript ----------
def _text(content):
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def read_transcript(path, tail_bytes=3_000_000):
    """Return ctx tokens of the last API call, idle minutes, last assistant text, previous human prompt."""
    info = {"ctx": 0, "idle_min": None, "last_assistant": "", "prev_prompt": "", "compacted": False}
    if not path or not os.path.exists(path):
        return info
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - tail_bytes))
        lines = f.read().decode("utf-8", "ignore").splitlines()
    last_ts = None
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t = e.get("type")
        if t == "assistant" and not e.get("isSidechain"):
            m = e.get("message") or {}
            u = m.get("usage") or {}
            ctx = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
            if ctx:
                info["ctx"], info["compacted"] = ctx, False
            txt = _text(m.get("content"))
            if txt.strip():
                info["last_assistant"] = txt
            last_ts = e.get("timestamp") or last_ts
        elif t == "system" and e.get("subtype") == "compact_boundary":
            info["compacted"] = True
        elif t == "user" and not e.get("isSidechain") and (e.get("origin") or {}).get("kind") == "human":
            txt = _text((e.get("message") or {}).get("content"))
            if txt.strip() and not txt.startswith("<"):
                info["prev_prompt"] = txt
    if info["compacted"]:
        info["ctx"] = 0  # unknown but small until the next call reports usage
    if last_ts:
        try:
            then = dt.datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            info["idle_min"] = (dt.datetime.now(dt.timezone.utc) - then).total_seconds() / 60
        except ValueError:
            pass
    return info


# ---------- Jev ----------
QUESTIONS = {
    "volume": {"type": "choice",
        "instructions": "A coding agent (Claude Code) with file, shell, web and browser tools receives `new_user_message` after `conversation`. Estimate how many tool actions it will take to fully handle it. If the message approves, answers or continues a plan proposed in `conversation.last_assistant_message` (e.g. 'sí', 'dale', a pasted value or path the agent asked for), count the tool actions of executing that plan.",
        "criteria": {
            "none": "No tool use: answer from conversation or knowledge (opinion, explanation, short draft text, acknowledgement, stop).",
            "few": "1 to 3 tool actions: check one thing, run one command, one quick lookup or one small edit.",
            "moderate": "4 to 8 tool actions: small fix across a couple of files, short investigation, a few web lookups.",
            "heavy": "9 or more tool actions: implementing or building a feature, multi-file changes, research across many sources, iterative debugging, testing loops, browsing many pages."}},
    "intent": {"type": "choice",
        "instructions": "What is the main kind of work `new_user_message` asks for, given `conversation`?",
        "criteria": {
            "chat": "Opinion, explanation, advice, or drafting a reply/text without needing to inspect anything.",
            "continue_plan": "Approves, confirms or supplies missing info so the agent continues work it already proposed or started.",
            "lookup": "Check, find, read or run something specific and report back.",
            "build": "Create or modify code, files, documents or configuration.",
            "research": "Investigate a topic across multiple sources, docs or web pages and synthesize.",
            "debug": "Diagnose and fix something that is failing or behaving wrong.",
            "review": "Review or audit existing code, PR, document or data."}},
    "model_fit": {"type": "choice",
        "instructions": "A strong orchestrator model (Opus) can hand the work in `new_user_message` to a helper with a written brief, then review the result. Which is the cheapest helper that would do this work well? Consider what the work actually requires, given `conversation`.",
        "criteria": {
            "haiku": "Pure execution with no real judgment: run or start commands/services, drive a browser through known steps, fetch/collect/search and report information, add items to a tool, generate media in an app, or trivial edits (change a color, text, label, tooltip, add a simple category) where what to do is fully specified.",
            "sonnet": "Engineering work needing code understanding: implement features, build or modify UI with logic, debug something failing, analyze code, data or documents, write scripts, investigate and synthesize findings.",
            "keep_opus": "Should not be delegated: the user wants the orchestrator's own opinion, advice, decision, strategy, plan, evaluation of trade-offs or a status summary of the conversation; or it is a short answer that needs no work."}},
    "sensitive": {"type": "noul",
        "instructions": "Does handling `new_user_message` involve secrets or high-stakes actions: private keys, API keys, passwords, wallets or signing transactions, payments or money movements, security threats or scams, or publishing/sending something on the user's behalf?"},
    "fresh": {"type": "choice",
        "instructions": "The user sends `new_user_message` in a very long chat session. Starting a brand-new session would lose the chat history but keep files, the project folder and saved memories. Would a fresh session handle `new_user_message` just as well?",
        "criteria": {
            "fresh_ok": "Yes: it is a new, self-contained request (new topic, new document or message pasted in full, general question) that does not rely on what was discussed.",
            "needs_history": "No: it continues, refers to, replies to or depends on something from the conversation (a thread, a plan, a previous answer, 'it', 'this', 'continue', an ongoing negotiation or task)."}},
    "boundary": {"type": "noul",
        "instructions": "Is `new_user_message` the start of a new task or phase (the previous piece of work looks finished, or the user switches to something different), so that this is a good moment to summarize and wipe the detailed history? Answer no if it continues, fixes or refines what was just being done."},
}


def jev_state(prompt, info):
    return {"conversation": {"previous_user_message": redact(info["prev_prompt"])[:300],
                             "last_assistant_message": redact(info["last_assistant"])[-900:]},
            "new_user_message": redact(prompt)[:1500]}


def ask_jev(state, questions, timeout):
    key = api_key()
    if not key:
        raise RuntimeError("no TYPESAFE_API_KEY")
    body = json.dumps({"model": "jev-latest", "state": state, "questions": questions}).encode()
    req = urllib.request.Request("https://api.typesafe.ai/v1/systemone", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    out["_ms"] = round((time.perf_counter() - t) * 1000)
    return out


# ---------- cost model ----------
def _turn_cost(cfg, ctx0, k, model, delegated):
    P = cfg["prices"]
    o, g, out = P["opus"], cfg["growth_per_call"], cfg["out_per_call"]
    fut = cfg["_fut"]
    if not delegated:
        c = sum((ctx0 + i * g) * o["read"] + g * o["write"] + out * o["out"] for i in range(k))
        return (c + g * max(0, k - 1) * fut * o["read"]) / 1e6
    s = P[model]
    c = 2 * ctx0 * o["read"] + cfg["dispatch_tokens"] * o["out"] + cfg["result_tokens"] * o["write"] + out * o["out"]
    c += cfg["worker_base_tokens"] * s["write"]
    for j in range(max(1, round(k * cfg["worker_call_factor"]))):
        c += (cfg["worker_base_tokens"] + j * g) * s["read"] + g * s["write"] + out * s["out"]
    return (c + cfg["result_tokens"] * fut * o["read"]) / 1e6


def expected_saving(cfg, probs, ctx0, model):
    ks = cfg["calls_per_bucket"]
    return sum(p * (_turn_cost(cfg, ctx0, ks[b], model, False) - _turn_cost(cfg, ctx0, ks[b], model, True))
               for b, p in probs.items() if b in ks)


def route(cfg, answers, ctx):
    """Return (worker or None, expected $ saving, reason)."""
    mf = answers["model_fit"]["probabilities"]
    if answers["sensitive"]["noul"] >= cfg["sensitive_threshold"]:
        return None, 0.0, "sensitive"
    if mf.get("keep_opus", 0) >= cfg["keep_opus_threshold"]:
        return None, 0.0, "keep_opus"
    model = "haiku" if (mf.get("haiku", 0) >= cfg["haiku_threshold"]
                        and answers["intent"]["choice"] in ("continue_plan", "lookup")) else "sonnet"
    cfg["_fut"] = max(0.0, min(cfg["future_calls_cap"], (cfg["compact_at_tokens"] - ctx) / cfg["growth_per_call"]))
    ev = expected_saving(cfg, answers["volume"]["probabilities"], max(ctx, 45_000), model)
    return (model if ev > cfg["min_expected_saving_usd"] else None), ev, "ev"


def resume_cost(cfg, ctx, cold):
    """$ to send the current context once (rewrite if cold, read if warm)."""
    o = cfg["prices"]["opus"]
    return ctx * (o["write"] if cold else o["read"]) / 1e6


# ---------- compaction policy ----------
def compact_decision(cfg, ctx, cold, answers, snooze_until=0):
    """Return None, or (kind, why) with kind in {"compact","clear"} and why in {"cold","hard","boundary"}."""
    if ctx < snooze_until:
        return None
    a = answers or {}
    fresh = a.get("fresh", {}).get("probabilities", {}).get("fresh_ok", 0.0)
    boundary = a.get("boundary", {}).get("noul", 0.0)
    kind = "clear" if fresh >= cfg["fresh_threshold"] else "compact"
    if cold and ctx >= cfg["cold_compact_at_tokens"]:
        return kind, "cold"
    if ctx >= cfg["hard_compact_at_tokens"]:
        return kind, "hard"
    if ctx >= cfg["compact_at_tokens"] and (boundary >= cfg["boundary_threshold"] or kind == "clear"):
        return kind, "boundary"
    return None


# ---------- verbatim handoff across /compact ----------
_ITEM = re.compile(r"(?:https?://[^\s\"'<>)\]]+|[A-Za-z]:[\\/][^\s\"'<>|]+|(?:\.{0,2}/)?(?:[\w\-]+/)+[\w\-.]+|\b0\.0\.\d{3,}\b"
                   r"|\b[\w\-]+\.(?:py|js|ts|tsx|jsx|json|md|html|css|sql|php|java|go|rs|env|yml|yaml|toml|csv|pdf|sh|ps1)\b"
                   r"|\b[a-z]+(?:[A-Z][a-z0-9]+)+\b|\b[a-z0-9]+(?:_[a-z0-9]+){1,}\b|\b\d{5,}\b)")
_DECISION = re.compile(r"(?i)\b(no |nunca|siempre|usa |use |prefer|quiero|decid|important|must|don't|do not|no pongas|clave|key|token|\.env|deadline|plazo)")


def transcript_segments(path):
    """Segments of the CURRENT context (after the last compact boundary): (kind, text)."""
    segs = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("isSidechain"):
                continue
            t = e.get("type")
            if t == "system" and e.get("subtype") == "compact_boundary":
                segs = []
                continue
            c = (e.get("message") or {}).get("content")
            if t == "user" and (e.get("origin") or {}).get("kind") == "human":
                txt = _text(c)
                if txt.strip() and not txt.startswith("<"):
                    segs.append(("user", txt[:2500]))
            elif t == "assistant":
                txt = _text(c)
                if txt.strip():
                    segs.append(("assistant", txt[:2500]))
                for b in c or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        segs.append(("tool", (b.get("name", "") + " " + json.dumps(b.get("input"), ensure_ascii=False))[:400]))
    return segs


def select_handoff(segs, budget, recent_share):
    """Indices to keep: newest history up to recent_share of budget, then older segments rich in
    concrete items / decisions / user requests. Returned in chronological order."""
    chosen, used = [], 0
    for j in range(len(segs) - 1, -1, -1):
        if used + len(segs[j][1]) > budget * recent_share:
            break
        chosen.append(j)
        used += len(segs[j][1])
    picked = set(chosen)

    def score(j):
        k, t = segs[j]
        return min(4, len(_ITEM.findall(t))) + 3 * bool(_DECISION.search(t)) + 2 * (k == "user")
    for j in sorted((j for j in range(len(segs)) if j not in picked), key=score, reverse=True):
        if score(j) < 3:
            break
        if used + len(segs[j][1]) <= budget:
            chosen.append(j)
            used += len(segs[j][1])
    return sorted(chosen)


def build_handoff(path, cfg, lang="es"):
    segs = transcript_segments(path)
    if not segs:
        return ""
    keep = select_handoff(segs, cfg["handoff_budget_chars"], cfg["handoff_recent_share"])
    lab = {"user": "usuario", "assistant": "asistente", "tool": "herramienta"} if lang == "es" else \
          {"user": "user", "assistant": "assistant", "tool": "tool"}
    head = ("[jev-router] Extractos LITERALES de la conversación previa a la compactación (lo más reciente + decisiones, "
            "restricciones y datos concretos anteriores). Úsalos como fuente exacta; el resumen puede omitir detalles."
            if lang == "es" else
            "[jev-router] VERBATIM excerpts from before compaction (most recent history + earlier decisions, constraints "
            "and concrete values). Treat them as exact; the summary may omit details.")
    body, last = [], -2
    for j in keep:
        if j != last + 1:
            body.append("…")
        body.append(f"[{lab[segs[j][0]]}] {redact(segs[j][1])}")
        last = j
    return head + "\n\n" + "\n\n".join(body)


# ---------- state & log ----------
def h(s):
    return hashlib.sha256((s or "").strip().encode()).hexdigest()[:16]


def load_state(session_id):
    try:
        return json.loads((DATA / f"state-{session_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(session_id, st):
    try:
        (DATA / f"state-{session_id}.json").write_text(json.dumps(st), encoding="utf-8")
    except OSError:
        pass


def log(rec):
    rec["ts"] = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        with open(DATA / "decisions.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
