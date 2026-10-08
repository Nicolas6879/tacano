---
name: orchestrator
description: Protocol for sessions running the tacano plugin — how Opus should act on "[tacano]" hints and blocks, write delegation briefs, review worker output, and keep context small. Use whenever a message contains "[tacano]", or when deciding whether to delegate tool-heavy work to a cheaper model.
---

# Orchestrating with tacano

Opus is the planner and reviewer. Cost in Claude Code is dominated by re-reading the conversation context on every tool call (cache reads + rewrites ≈ 90% of spend), not by output. So the two things that save real tokens are: keep the main context small, and run long tool loops in a fresh, small worker context.

## When a `[tacano]` hint appears
It means Jev judged the request as heavy execution and the expected saving of delegating is positive.
1. Decide the approach yourself (answer any opinion/decision part directly — never delegate opinions, strategy, secrets, signing, payments or publishing).
2. Spawn the suggested worker (`tacano:sonnet-worker` or `tacano:haiku-worker`; when the work needs MCP connectors or the browser — Apify, Notion, Drive, Chrome — use `tacano:sonnet-tools-worker` or `tacano:haiku-browser-worker`, since the plain workers lack those tools) with a **self-contained brief**: goal, exact paths/URLs, constraints, how to verify, what to report. The worker has no access to this conversation.
3. Independent pieces → several workers in one message (parallel).
4. Review the report; spot-check claims that matter (open the diff, rerun the test). Send it back with specific fixes if needed.
5. Ignore the hint if the work is small (1–3 tool calls) or the request is mainly a question.
6. "Hazlo tú" / "do it yourself" from the user means Claude as a whole, not "don't delegate": keep orchestrating unless they explicitly say not to use subagents.

## When there is no hint
Default to working inline. Delegate on your own only when you foresee ≥10 tool calls of mechanical execution.

## Blocks
The gate blocks a prompt only when the session context is large: at a task boundary from 250k, always from 550k, or ≥300k with an expired cache. The user decides: `/compact`, `/clear`, or resend the same message to continue. After a compact/clear, a `[tacano]` note restores the held-back message. The user is asked to resend it: handle it exactly once; if they only say "sigue"/"continue", that held-back message is the task, not whatever came before. After any compaction (manual or auto) a `[tacano]` block of VERBATIM excerpts (recent history + earlier decisions/values) is injected: treat it as exact and prefer it over the summary when they disagree.

## Keep the context lean
- Prefer Grep/targeted reads over reading whole large files; don't paste big outputs back.
- Ask workers for short reports (they are instructed to keep them ≤250 words).
