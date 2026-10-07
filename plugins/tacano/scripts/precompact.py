"""PreCompact hook (manual /compact and auto-compaction): save a verbatim handoff of the current context
(newest history + older decisions/concrete values) so SessionStart can re-inject it after the summary.
Sims on real compactions: summary alone kept 50% of later-needed items; summary + this handoff ~85%."""
import json
import sys
import time

import jevlib as J


def main():
    inp = json.loads(sys.stdin.read() or "{}")
    cfg = J.config()
    if not cfg.get("enabled", True) or not inp.get("transcript_path"):
        return
    sid = inp.get("session_id") or "nosession"
    t0 = time.perf_counter()
    text = J.build_handoff(inp["transcript_path"], cfg, cfg["lang"])
    if not text:
        return
    (J.DATA / f"handoff-{sid}.md").write_text(text, encoding="utf-8")
    J.log({"action": "handoff_saved", "session": sid, "trigger": inp.get("trigger"), "chars": len(text),
           "hook_ms": round((time.perf_counter() - t0) * 1000)})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never block compaction
        try:
            J.log({"action": "crash_precompact", "error": repr(e)[:300]})
        except Exception:
            pass
    sys.exit(0)
