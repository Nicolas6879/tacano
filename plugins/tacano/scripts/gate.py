"""UserPromptSubmit hook. Decides, before Opus spends a token, whether to:
  1. block and suggest /clear (new topic in a huge context) or /compact (context too big / cache cold), or
  2. let the prompt through with a short delegation hint for Opus, or
  3. stay silent (most prompts).
Fails open: any error -> exit 0 with no output."""
import json
import sys
import time

import jevlib as J

MSG = {
    "es": {
        "clear": ("[tacano] 🪙 Modo tacaño: esto parece un tema nuevo y la sesión ya carga {ctx}k tokens{cold}. "
                  "Seguir aquí cuesta ~${now:.2f} solo en releer el contexto, y cada respuesta lo vuelve a leer. "
                  "Usa /clear (o una sesión nueva) y vuelve a enviar el mensaje (flecha ↑). "
                  "Para seguir aquí igualmente, reenvía el mismo mensaje."),
        "compact": ("[tacano] 🪙 Modo tacaño: {why}la sesión carga {ctx}k tokens{cold}; cada respuesta relee todo eso (~${now:.2f} por llamada{cw}). "
                    "Ejecuta /compact y luego reenvía tu mensaje (flecha ↑). "
                    "Para seguir sin compactar, reenvía el mismo mensaje."),
        "cold": " y el caché expiró (reanudar reescribe todo)",
        "cw": "; esta primera, al estar frío, ~${first:.2f}",
        "why_boundary": "buen momento para compactar, parece que empiezas una tarea nueva. ",
        "kept": " Al compactar guardo extractos literales de lo reciente y de las decisiones clave.",
        "hint": ("[tacano] Jev estima trabajo de ejecución {vol} (P={p:.2f}), apto para {worker}. Política: decide/planea tú; "
                 "delega la ejecución con Agent(subagent_type=\"tacano:{worker}-worker\") y un brief autocontenido "
                 "(rutas, objetivo, criterios de aceptación); revisa el resultado. Ahorro esperado ≈ ${ev:.2f}. "
                 "Si la petición es sobre todo opinión o decisión, ignora esta pista."),
    },
    "en": {
        "clear": ("[tacano] 🪙 Being stingy: this looks like a new topic and the session already carries {ctx}k tokens{cold}. "
                  "Continuing here costs ~${now:.2f} just to re-read context, on every response. "
                  "Run /clear (or open a new session) and resend your message (up arrow). "
                  "To continue here anyway, resend the same message."),
        "compact": ("[tacano] 🪙 Being stingy: {why}the session carries {ctx}k tokens{cold}; every response re-reads it (~${now:.2f} per call{cw}). "
                    "Run /compact, then resend your message (up arrow). "
                    "To continue without compacting, resend the same message."),
        "cold": " and the cache expired (resuming rewrites all of it)",
        "cw": "; this first one, being cold, ~${first:.2f}",
        "why_boundary": "good moment to compact, you seem to be starting a new task. ",
        "kept": " On compaction I keep verbatim excerpts of recent history and key decisions.",
        "hint": ("[tacano] Jev estimates {vol} execution work (P={p:.2f}), suitable for {worker}. Policy: you decide/plan; "
                 "delegate execution with Agent(subagent_type=\"tacano:{worker}-worker\") and a self-contained brief "
                 "(paths, goal, acceptance criteria); review the result. Expected saving ≈ ${ev:.2f}. "
                 "If the request is mostly opinion or a decision, ignore this hint."),
    },
}


def main():
    t0 = time.perf_counter()
    inp = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    cfg = J.config()
    if not cfg.get("enabled", True):
        return
    prompt, sid = inp.get("prompt") or "", inp.get("session_id") or "nosession"
    if not prompt.strip() or prompt.lstrip().startswith("/"):
        return
    m = MSG.get(cfg["lang"], MSG["en"])
    info = J.read_transcript(inp.get("transcript_path"))
    ctx = info["ctx"]
    cold = info["idle_min"] is not None and info["idle_min"] > cfg["cold_after_minutes"]
    st = J.load_state(sid)
    rec = {"session": sid, "ctx": ctx, "cold": cold, "prompt_hash": J.h(prompt), "prompt_len": len(prompt)}

    # Same prompt resent shortly after a block = explicit override.
    override = (st.get("blocked_hash") == J.h(prompt)
                and time.time() - st.get("blocked_at", 0) < cfg["override_window_minutes"] * 60)
    if override:
        st.update(blocked_hash=None, snooze_until_ctx=ctx + cfg["snooze_tokens"])
        rec["override"] = True

    answers, err = None, None
    try:
        r = J.ask_jev(J.jev_state(prompt, info), J.QUESTIONS, cfg["jev_timeout_s"])
        answers, rec["jev_ms"], rec["jev_tokens"] = r["answers"], r.get("_ms"), r.get("usage", {}).get("input_tokens")
        rec["answers"] = {"volume": answers["volume"]["probabilities"], "intent": answers["intent"]["choice"],
                          "model_fit": answers["model_fit"]["probabilities"], "sensitive": answers["sensitive"]["noul"],
                          "fresh": answers["fresh"]["probabilities"].get("fresh_ok"), "boundary": answers["boundary"]["noul"]}
    except Exception as e:  # network, auth, schema: degrade to code-only rules
        err = rec["jev_error"] = str(e)[:200]

    out = {}
    # A sharp drop in context means a compaction happened: remember its size for the block gap.
    if ctx:
        if ctx < 0.6 * st.get("last_ctx", 0):
            st["floor"] = ctx
        st["last_ctx"] = ctx
    dec = None if override else J.compact_decision(cfg, ctx, cold, answers, st.get("snooze_until_ctx", 0), st.get("floor", 0))
    if dec:
        kind, why = dec
        now = J.resume_cost(cfg, ctx, False)
        first = J.resume_cost(cfg, ctx, True)
        reason = m[kind].format(ctx=ctx // 1000, cold=m["cold"] if cold else "", now=now,
                                cw=m["cw"].format(first=first) if cold else "",
                                why=m["why_boundary"] if why == "boundary" else "")
        if kind == "compact":
            reason += m["kept"]
        out = {"decision": "block", "reason": reason}
        st.update(blocked_hash=J.h(prompt), blocked_at=time.time(), pending_prompt=prompt[:4000],
                  pending_at=time.time(), cwd=inp.get("cwd"))
        rec.update(action="block_" + kind, why=why)
    elif answers:
        worker, ev, why = J.route(cfg, answers, ctx)
        rec.update(action="hint_" + worker if worker else "silent", ev=round(ev, 3), why=why)
        if worker:
            vol = max(answers["volume"]["probabilities"].items(), key=lambda kv: kv[1])
            out = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                   "additionalContext": m["hint"].format(vol=vol[0], p=vol[1], worker=worker, ev=ev)}}
    else:
        rec["action"] = "silent_nojev"
    J.save_state(sid, st)
    rec["hook_ms"] = round((time.perf_counter() - t0) * 1000)
    J.log(rec)
    if out:
        print(json.dumps(out))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # fail open
        try:
            J.log({"action": "crash", "error": repr(e)[:300]})
        except Exception:
            pass
    sys.exit(0)
