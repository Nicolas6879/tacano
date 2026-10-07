"""/tacano:setup [on|off|status] — the user's own opt-in for Opus-as-orchestrator.

Claude Code only spawns subagents when the user asks, so a hook hint alone gets ignored.
This writes the request where it belongs: a marked section of the user's ~/.claude/CLAUDE.md."""
import pathlib
import re
import sys

import jevlib as J

START, END = "<!-- tacano:orchestration -->", "<!-- /tacano:orchestration -->"
TEXT = {
    "es": ("## Orquestación\n"
           "Quiero que Opus actúe como orquestador. Cuando aparezca una pista [tacano] sugiriendo un trabajador,\n"
           "síguela: planea tú, delega la ejecución al agente tacano:… indicado con un brief autocontenido\n"
           "(rutas, objetivo, criterios de aceptación) y revisa el resultado. Considera esto mi petición\n"
           "explícita de usar esos subagentes. No delegues opiniones, decisiones, secretos ni publicaciones.\n"),
    "en": ("## Orchestration\n"
           "I want Opus to act as the orchestrator. When a [tacano] hint suggests a worker, follow it:\n"
           "plan yourself, delegate the execution to the named tacano:… agent with a self-contained brief\n"
           "(paths, goal, acceptance criteria) and review the result. Treat this as my explicit request to\n"
           "use those subagents. Never delegate opinions, decisions, secrets or publishing.\n"),
}
BLOCK = re.compile(re.escape(START) + r".*?" + re.escape(END) + r"\n?", re.S)


def main():
    arg = (sys.argv[1] if len(sys.argv) > 1 else "on").strip().lower()
    path = pathlib.Path.home() / ".claude" / "CLAUDE.md"
    cur = path.read_text(encoding="utf-8") if path.exists() else ""
    present = bool(BLOCK.search(cur))
    if arg == "status":
        print(f"Orchestration opt-in {'ON' if present else 'OFF'} in {path}")
        return
    if arg == "off":
        if present:
            path.write_text(BLOCK.sub("", cur), encoding="utf-8")
        print(f"Removed the tacano orchestration section from {path}" if present else f"Nothing to remove in {path}")
        return
    lang = J.config().get("lang", "en")
    block = f"{START}\n{TEXT.get(lang, TEXT['en'])}{END}\n"
    new = BLOCK.sub(block, cur) if present else (cur.rstrip() + "\n\n" if cur.strip() else "") + block
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    print(f"{'Updated' if present else 'Added'} the tacano orchestration section in {path}. "
          "It applies from the next session. Undo with /tacano:setup off.")


if __name__ == "__main__":
    main()
