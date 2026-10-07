# Tacaño 🪙

**English** · [Español](README.es.md)

**Your Claude, but stingy with tokens.** ~39% lower Claude Code spend, same Opus.

*Tacaño* is Spanish for "stingy".

```
/plugin marketplace add Nicolas6879/tacano
/plugin install tacano@tacano-marketplace
```

Opus stays the orchestrator. [Jev](https://docs.typesafe.ai) (TypeSafe System One) is a decision-only model: on every prompt it decides, in ~400 ms and for ~$0.00007, when to compact, what to keep, and what to hand off to cheap workers.

## The problem

In Claude Code, almost all of the spend is **re-reading the context** on every tool call. Cache reads and writes are ~89% of the cost; model output is only ~11%. Long sessions reach 500k–1M tokens, and every response reads all of it again.

"Just use a cheaper model" doesn't fix this:
- Sonnet 5.5 charges the same for cache reads as Opus 5.5.
- In our measurements, **delegating everything to Sonnet cost 22% more**.

## What it does

On every prompt, a hook asks Jev 7 typed questions in a single request: work volume, intent, which model fits, whether it's sensitive, whether it needs the history, whether it's a task boundary, and so on. The code then acts on the answers:

| Situation | Action |
|---|---|
| Context ≥250k **and** Jev sees you starting a new task, or context ≥550k, or an expired cache with ≥300k | Blocks the prompt (0 tokens spent) and suggests `/compact`, or `/clear` if the request doesn't need the history. Type "continue" afterwards and your message is restored automatically. Resend the same message to skip the block. |
| Any compaction (manual or automatic) | `PreCompact` saves ~13k tokens of **verbatim** excerpts (the most recent history plus decisions, constraints and concrete values). `SessionStart` injects them back after the summary. |
| Heavy execution (≥9 expected tool actions) with an expected saving above $0.05 | Hints Opus to delegate the execution to `sonnet-worker` or `haiku-worker` (restricted-tool agents that start with a small context) and review the result. |
| Opinions, decisions, secrets, signing, payments, publishing | Never delegated. |
| Everything else | Silence. |

It fails safe. If Jev doesn't answer or something errors, Tacaño never blocks because of it; only code rules apply, such as the 550k hard cap.

## Results

Replay of 500 real turns, call by call, using the plugin's own code and real Jev answers:

| Metric | Value |
|---|---|
| Cost saved | **39.1%** (35.4% under pessimistic assumptions) |
| In 500k–1M-token sessions | ~50% |
| Interruptions | 1 every ~38 prompts |
| Information that survives a compaction | summary alone: 50% → summary + handoff: ~85–95% |
| Hook latency | gate p50 ~430 ms; `PreCompact` <1 s on a 26 MB transcript |
| Tests | 31/31 |

Tried and dropped:
- **RTK**: lost information Claude needed afterwards.
- **context-mode**: kept only 2% of what mattered on code reads.
- **Jev choosing what to keep**: it didn't beat "keep the most recent".

## Install

Requirements:
- Claude Code (CLI or desktop app).
- Python 3 on your PATH as `python`.
- A TypeSafe API key, either in `TYPESAFE_API_KEY` or as a `TYPESAFE_API_KEY=...` line in `~/.typesafe.env`.

From GitHub:

```
/plugin marketplace add Nicolas6879/tacano
/plugin install tacano@tacano-marketplace
```

From a local clone:

```
/plugin marketplace add /path/to/tacano
/plugin install tacano@tacano-marketplace
```

The plugin takes effect from the next session.

## Configuration

Create `config.json` in the plugin's data folder (`~/.claude/plugins/data/tacano-…/`) with only the keys you want to change:

```json
{ "lang": "en", "compact_at_tokens": 250000, "hard_compact_at_tokens": 550000, "cold_compact_at_tokens": 300000,
  "boundary_threshold": 0.7, "handoff_budget_chars": 48000, "enabled": true }
```

Messages default to Spanish (`"lang": "es"`); set `"lang": "en"` for English. Every key and its default is in `DEFAULTS` in `plugins/tacano/scripts/jevlib.py`.

## Measure your real savings

```
python plugins/tacano/scripts/report.py
```

`report.py` compares your cost per prompt before and after installing, using your local transcripts (subagents included). It also counts blocks, overrides and handoffs, and shows whether Opus followed the delegation hints.

## Privacy

- Keys, tokens, passwords and private keys are redacted before anything is sent to Jev. Only the prompt, the previous prompt and the end of the last assistant message are sent.
- The local log (`decisions.jsonl`) stores hashes and numbers, never the text of your prompts.
- Handoff excerpts stay on your machine.

## Layout

```
.claude-plugin/marketplace.json     marketplace (this repo)
plugins/tacano/
  .claude-plugin/plugin.json
  hooks/hooks.json                  UserPromptSubmit, PreCompact, SessionStart
  scripts/jevlib.py                 config, Jev client, policy, cost model, handoff
  scripts/gate.py                   decides: block, delegation hint, or nothing
  scripts/precompact.py             saves the verbatim handoff
  scripts/session.py                re-injects the handoff and any held-back message
  scripts/report.py                 measures real savings
  agents/                           sonnet-worker, haiku-worker, haiku-browser-worker
  skills/orchestrator/              protocol for Opus
lab/                                simulations and evaluation (run on YOUR transcripts)
```

## Lab

`lab/` reproduces the analysis using the local transcripts of whoever runs it (`~/.claude/projects`). Generated data (`lab/*.json`) contains prompt text and is git-ignored.

```
python lab/mine2.py && python lab/mine3.py      # extract turns
python lab/final_sim.py fetch                   # Jev answers to the plugin's questions
python lab/final_sim.py grid                    # full replay + tuning grid
python lab/test_hooks.py                        # 31 edge-case tests
```

## Limitations

- Short sessions (under 200k tokens) see little change. That's expected: there's not much to save there.
- Handoff quality was measured with a proxy: paths, URLs, IDs and identifiers reused after compaction.
- The delegation hint is advice, not enforcement. `report.py` measures how often Opus follows it.
- Prices are pinned to 2026-09-25 (the `prices` key in the configuration).

## License

MIT
