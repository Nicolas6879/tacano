import json, os, subprocess, sys, tempfile, time, pathlib
PLUG = pathlib.Path(__file__).resolve().parent.parent / "plugins" / "tacano" / "scripts"
TMP = pathlib.Path(tempfile.mkdtemp(prefix="jevtest-"))


def run(script, payload, env_extra=None, raw=None):
    env = {k: v for k, v in {**os.environ, "CLAUDE_PLUGIN_DATA": str(TMP / "data"), **(env_extra or {})}.items() if k != "PYTHONIOENCODING"}
    t = time.perf_counter()
    p = subprocess.run([sys.executable, str(PLUG / script)], input=(raw if raw is not None else json.dumps(payload, ensure_ascii=False)),
                       capture_output=True, text=True, env=env, encoding="utf-8", timeout=30)
    ms = (time.perf_counter() - t) * 1000
    out = json.loads(p.stdout) if p.stdout.strip() else None
    return p.returncode, out, ms, p.stderr


def transcript(ctx, idle_min=1, assistant="Listo. ¿Quieres que implemente el dashboard completo con tests?", compacted=False, prev="revisa el repo"):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - idle_min * 60))
    lines = [{"type": "user", "origin": {"kind": "human"}, "message": {"role": "user", "content": prev}, "timestamp": ts},
             {"type": "assistant", "message": {"id": "m1", "model": "claude-opus-5-5",
              "usage": {"input_tokens": 5, "cache_read_input_tokens": ctx - 100, "cache_creation_input_tokens": 95, "output_tokens": 300},
              "content": [{"type": "text", "text": assistant}]}, "timestamp": ts}]
    if compacted:
        lines.append({"type": "system", "subtype": "compact_boundary", "timestamp": ts})
    f = TMP / f"t{len(list(TMP.glob('t*.jsonl')))}.jsonl"
    f.write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")
    return str(f)


def last_log():
    return json.loads((TMP / "data" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()[-1])


ok = fail = 0


def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1; print("PASS", name, info)
    else:
        fail += 1; print("FAIL", name, info)


rc, out, ms, _ = run("gate.py", {"prompt": "sí, dale con todo 🚀 implementa la sección de pagos con ñandú_config y tests", "session_id": "u1", "transcript_path": transcript(150_000)})
check("unicode/emoji prompt under cp1252 console -> valid output", rc == 0 and (out is None or isinstance(out, dict)) and last_log().get("action") not in ("crash", None), str(last_log().get("action")))
rc, out, ms, _ = run("gate.py", {"prompt": "/compact", "session_id": "s1", "transcript_path": transcript(300_000)})
check("slash command silent", rc == 0 and out is None)

rc, out, ms, err = run("gate.py", None, raw="{not json")
check("malformed stdin fails open", rc == 0 and out is None)

rc, out, ms, _ = run("gate.py", {"prompt": "sí, dale, implementa todo el dashboard con tests y gráficos interactivos", "session_id": "s2", "transcript_path": transcript(150_000)})
check("heavy continue -> hint", rc == 0 and out and "additionalContext" in json.dumps(out), f"{ms:.0f}ms {last_log().get('action')} ev={last_log().get('ev')}")

rc, out, ms, _ = run("gate.py", {"prompt": "¿qué opinas, vale la pena aceptar esa oferta o sigo en mi trabajo actual?", "session_id": "s3", "transcript_path": transcript(150_000)})
check("opinion -> silent", rc == 0 and out is None, f"{ms:.0f}ms {last_log().get('action')} {last_log().get('why')}")

p5 = "sí, sigue con lo que propusiste"
rc, out, ms, _ = run("gate.py", {"prompt": p5, "session_id": "s4b", "transcript_path": transcript(420_000)})
check("420k mid-task (no boundary) -> no block", not (out and out.get("decision") == "block"), f"boundary={last_log().get('answers', {}).get('boundary')}")

rc, out, ms, _ = run("gate.py", {"prompt": p5, "session_id": "s4", "transcript_path": transcript(560_000)})
check("560k hard limit -> block compact", out and out.get("decision") == "block" and "/compact" in out["reason"], f"{ms:.0f}ms")
rc, out2, ms, _ = run("gate.py", {"prompt": p5 + " ya", "session_id": "s4lang", "transcript_path": transcript(560_000)}, {"CLAUDE_PLUGIN_OPTION_LANG": "es"})
check("install-time choice es -> Spanish", out2 and "Modo tacaño" in out2["reason"])
check("default language English with stingy tone", out and "Being stingy" in out["reason"] and "🪙" in out["reason"])

rc, out, ms, _ = run("gate.py", {"prompt": p5, "session_id": "s4", "transcript_path": transcript(560_000)})
check("resend same prompt overrides", not (out and out.get("decision") == "block"), str(last_log().get("action")))

rc, out, ms, _ = run("gate.py", {"prompt": "y agrega también el filtro por fecha", "session_id": "s4", "transcript_path": transcript(570_000)})
check("snooze after override", not (out and out.get("decision") == "block"))

rc, out, ms, _ = run("gate.py", {"prompt": "ayúdame a responder este correo:\n\nHola Juan, te escribo de Acme por una vacante de backend remoto, ¿tienes disponibilidad para una llamada el jueves?", "session_id": "s5", "cwd": "C:/proj",
                                  "transcript_path": transcript(500_000, assistant="El dashboard quedó listo y desplegado en Vercel.")})
check("big ctx new topic -> block clear", out and out.get("decision") == "block" and "/clear" in out["reason"], f"fresh={last_log().get('fresh')}")

rc, out, ms, _ = run("gate.py", {"prompt": "sigue con el plan", "session_id": "s6", "transcript_path": transcript(320_000, idle_min=180)})
check("cold 320k -> block", out and out.get("decision") == "block" and "cach" in out["reason"])

rc, out, ms, _ = run("gate.py", {"prompt": "sigue", "session_id": "s7", "transcript_path": transcript(800_000, compacted=True)})
check("after compact boundary no block", not (out and out.get("decision") == "block"))

rc, out, ms, _ = run("gate.py", {"prompt": "otra cosa", "session_id": "s8b", "transcript_path": transcript(350_000)}, {"TYPESAFE_API_KEY": "bad"})
check("bad key 350k -> no boundary info -> no block", rc == 0 and not (out and out.get("decision") == "block"))
rc, out, ms, _ = run("gate.py", {"prompt": "otra cosa", "session_id": "s8", "transcript_path": transcript(560_000)}, {"TYPESAFE_API_KEY": "bad"})
check("bad key 560k -> code-only hard block, logged error", rc == 0 and out and out.get("decision") == "block" and "jev_error" in last_log(), last_log().get("jev_error", "")[:40])

rc, out, ms, _ = run("gate.py", {"prompt": "implementa el login completo", "session_id": "s9", "transcript_path": transcript(80_000)}, {"TYPESAFE_API_KEY": "bad"})
check("bad key small ctx -> silent", rc == 0 and out is None)

cfgp = TMP / "data" / "config.json"
cfgp.write_text(json.dumps({"jev_timeout_s": 0.001}))
rc, out, ms, _ = run("gate.py", {"prompt": "implementa el login completo con tests", "session_id": "s10", "transcript_path": transcript(80_000)})
check("jev timeout -> fail open", rc == 0 and out is None and "jev_error" in last_log(), f"{ms:.0f}ms")
cfgp.unlink()

rc, out, ms, _ = run("gate.py", {"prompt": "firma la transacción con mi private key de la wallet y envía 50 HBAR a la cuenta 0.0.1234, haz todo el flujo", "session_id": "s11", "transcript_path": transcript(120_000)})
check("sensitive -> no hint", out is None, f"sens={last_log().get('answers', {}).get('sensitive')}")

rc, out, ms, _ = run("gate.py", {"prompt": "listo, eso ya quedó funcionando. Ahora pasemos a otra parte: configura el pipeline de CI con GitHub Actions para correr los tests en cada PR", "session_id": "s13",
                                  "transcript_path": transcript(350_000, assistant="Terminé: el dashboard está desplegado y todos los tests pasan.")})
check("350k task boundary -> block", out and out.get("decision") == "block", f"why={last_log().get('why')} boundary={last_log().get('answers', {}).get('boundary')}")
rc, out, ms, _ = run("gate.py", {"prompt": "el gráfico de barras que acabas de hacer no filtra por fecha, arréglalo", "session_id": "s14",
                                  "transcript_path": transcript(350_000, assistant="Listo, agregué el gráfico de barras al dashboard.")})
check("350k mid-task fix -> no block", not (out and out.get("decision") == "block"), f"boundary={last_log().get('answers', {}).get('boundary')}")

# ---- handoff across compaction ----
def long_transcript(n=300, boundary_at=None):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    L = []
    for i in range(n):
        if boundary_at is not None and i == boundary_at:
            L.append({"type": "system", "subtype": "compact_boundary", "timestamp": ts})
        if i == 5:
            u = "Regla importante: no pongas a Claude como autor en los commits. La API key está en C:/Users/x/.env y es " + "sk-" + "ant-api03-SECRETSECRETSECRET"
        elif i == 7:
            u = "OLD_BEFORE_BOUNDARY marker"
        else:
            u = f"paso {i}: revisa el módulo de pagos y ajusta lo que falte " + "texto de relleno " * 30
        L.append({"type": "user", "origin": {"kind": "human"}, "message": {"role": "user", "content": u}, "timestamp": ts})
        L.append({"type": "assistant", "message": {"id": f"m{i}", "usage": {"input_tokens": 1, "cache_read_input_tokens": 100000 + i * 1000, "cache_creation_input_tokens": 0, "output_tokens": 10},
                  "content": [{"type": "text", "text": f"Hecho el paso {i}. " + "detalle " * 60},
                              {"type": "tool_use", "id": f"t{i}", "name": "Edit", "input": {"file_path": f"src/payments/step_{i}.py"}}]}, "timestamp": ts})
    L.append({"type": "user", "origin": {"kind": "human"}, "message": {"role": "user", "content": "LAST_REQUEST_MARKER sigue con el último paso"}, "timestamp": ts})
    f = TMP / f"long{len(list(TMP.glob('long*.jsonl')))}.jsonl"
    f.write_text("\n".join(json.dumps(l) for l in L), encoding="utf-8")
    return str(f)

tp = long_transcript()
rc, out, ms, _ = run("precompact.py", {"session_id": "h1", "transcript_path": tp, "trigger": "manual"})
hf = TMP / "data" / "handoff-h1.md"
ho = hf.read_text(encoding="utf-8") if hf.exists() else ""
check("precompact saves handoff", rc == 0 and hf.exists(), f"{len(ho)} chars, {ms:.0f}ms")
check("handoff within budget", 0 < len(ho) <= 48_000 + 2_000, str(len(ho)))
check("handoff keeps recent + old decision", "LAST_REQUEST_MARKER" in ho and "no pongas a Claude como autor" in ho)
check("handoff redacts secrets", "SECRETSECRET" not in ho and ".env" in ho)
rc, out, ms, _ = run("session.py", {"session_id": "h1", "source": "compact", "cwd": "C:/h"})
check("handoff injected after compact", out and "LAST_REQUEST_MARKER" in json.dumps(out, ensure_ascii=False) and not hf.exists())
tp2 = long_transcript(boundary_at=100)
run("precompact.py", {"session_id": "h2", "transcript_path": tp2, "trigger": "auto"})
ho2 = (TMP / "data" / "handoff-h2.md").read_text(encoding="utf-8")
check("handoff ignores history before previous compaction", "OLD_BEFORE_BOUNDARY" not in ho2 and "LAST_REQUEST_MARKER" in ho2)

rc, out, ms, _ = run("session.py", {"session_id": "s6", "source": "compact"})
check("restore pending after /compact", out and "sigue con el plan" in json.dumps(out, ensure_ascii=False))

rc, out, ms, _ = run("session.py", {"session_id": "brand-new", "source": "clear", "cwd": "C:/proj"})
check("restore pending after /clear (new sid)", out and "Acme" in json.dumps(out, ensure_ascii=False))

rc, out, ms, _ = run("session.py", {"session_id": "s6", "source": "compact"})
check("restore only once", out is None)
rc, out, ms, _ = run("session.py", {"session_id": "other", "source": "clear", "cwd": "C:/otherproj"})
check("no cross-project restore", out is None)

rc, out, ms, _ = run("session.py", {"session_id": "s12", "source": "resume", "prompt_cache_likely_expired": True, "context_tokens": 450_000, "estimated_cache_write_usd": 3.6})
check("cold resume warns", out and "systemMessage" in out)

sys.path.insert(0, str(PLUG))
import jevlib
s = jevlib.jev_state("usa esta key " + "sk-" + "ant-api03-AAAABBBBCCCCDDDD y TYPESAFE_API_KEY=" + "apikeyXYZ12345678 y " + "ghp_" + "abcdefghijklmnopqrstuvwxyz123",
                     {"prev_prompt": "", "last_assistant": "password: hunter2hunter2"})
blob = json.dumps(s)
check("redaction", "sk-ant" not in blob and "apikeyXYZ" not in blob and "ghp_" not in blob and "hunter2" not in blob, blob[:160])

cfg = {**jevlib.DEFAULTS, "compact_at_tokens": 60_000}
newtask = {"boundary": {"noul": 0.95}, "fresh": {"probabilities": {"fresh_ok": 0.1}}}
check("cheap boundary not worth a block (116k)", jevlib.compact_decision(cfg, 116_000, False, newtask) is None)
newtopic = {"boundary": {"noul": 0.95}, "fresh": {"probabilities": {"fresh_ok": 1.0}}}
check("cheap /clear not worth a block (78k)", jevlib.compact_decision(cfg, 78_000, False, newtopic) is None)
check("big boundary still blocks (400k)", jevlib.compact_decision(cfg, 400_000, False, newtask) == ("compact", "boundary"))
check("gap since last compaction (floor 300k)", jevlib.compact_decision(cfg, 400_000, False, newtask, 0, 300_000) is None)
check("hard cap ignores gap", jevlib.compact_decision(cfg, 600_000, False, newtask, 0, 500_000) == ("compact", "hard"))

logtxt = (TMP / "data" / "decisions.jsonl").read_text(encoding="utf-8")
check("log has no prompt text", "Acme" not in logtxt and "private key" not in logtxt)
print(f"\n{ok} passed, {fail} failed. data={TMP}")
