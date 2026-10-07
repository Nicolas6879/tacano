"""SessionStart hook.
- After /compact (manual or auto): re-inject the verbatim handoff saved by precompact.py.
- After /compact or /clear triggered by the gate: restore the user's pending message so they only type 'sigue'.
- On resume with an expired cache and a big context: warn the user before they spend the rewrite."""
import json
import sys
import time

import jevlib as J


def main():
    inp = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    cfg = J.config()
    if not cfg.get("enabled", True):
        return
    sid, src = inp.get("session_id") or "nosession", inp.get("source")
    es = cfg["lang"] == "es"
    out = {}
    if src in ("compact", "clear"):
        # /clear starts a new session id; fall back to the most recent pending prompt of any session.
        st = J.load_state(sid)
        if not st.get("pending_prompt") and src == "clear" and inp.get("cwd"):
            best = None
            for p in J.DATA.glob("state-*.json"):
                try:
                    s = json.loads(p.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                newer = best is None or s.get("pending_at", 0) > best[0].get("pending_at", 0)
                if s.get("pending_prompt") and s.get("cwd") == inp.get("cwd") and newer:
                    best = (s, p.stem[len("state-"):])
            if best:
                st, sid = best
        parts = []
        hf = J.DATA / f"handoff-{inp.get('session_id') or 'nosession'}.md"
        if src == "compact" and hf.exists() and time.time() - hf.stat().st_mtime < 6 * 3600:
            parts.append(hf.read_text(encoding="utf-8"))
            hf.unlink()
            J.log({"action": "handoff_injected", "chars": len(parts[-1])})
        if st.get("pending_prompt") and time.time() - st.get("pending_at", 0) < 3600:
            note = ("[tacano] Justo antes de este {src}, el usuario envió este mensaje (quedó en espera). "
                    "Si su próximo mensaje es corto ('sigue', 'dale', 'ok'), atiende este pedido:\n\n{p}"
                    if es else
                    "[tacano] Right before this {src}, the user sent this message (held back). "
                    "If their next message is short ('continue', 'ok'), handle this request:\n\n{p}")
            parts.append(note.format(src="/" + src, p=st["pending_prompt"]))
            st.update(pending_prompt=None, blocked_hash=None)
            J.save_state(sid, st)
            J.log({"action": "restore_pending", "source": src})
        if parts:
            out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n\n---\n\n".join(parts)}}
    elif src == "resume" and inp.get("prompt_cache_likely_expired"):
        ctx = inp.get("context_tokens") or 0
        usd = inp.get("estimated_cache_write_usd")
        if ctx >= cfg["cold_compact_at_tokens"]:
            msg = (f"[tacano] 🪙 Modo tacaño: esta sesión reanuda {ctx // 1000}k tokens con el caché vencido"
                   f"{f' (~${usd:.2f} solo en reescribirlo)' if usd else ''}. Si vas a seguir el mismo tema, /compact primero; "
                   "si es otro tema, abre una sesión nueva."
                   if es else
                   f"[tacano] 🪙 Being stingy: resuming {ctx // 1000}k tokens with an expired cache"
                   f"{f' (~${usd:.2f} just to rewrite it)' if usd else ''}. Same topic: /compact first; new topic: new session.")
            out = {"systemMessage": msg}
            J.log({"action": "warn_cold_resume", "ctx": ctx, "usd": usd})
    if out:
        print(json.dumps(out))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try:
            J.log({"action": "crash_session", "error": repr(e)[:300]})
        except Exception:
            pass
    sys.exit(0)
