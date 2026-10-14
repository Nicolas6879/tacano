---
name: report
description: Show what Tacaño has saved on this computer — per-action savings (followed delegation hints, compactions), blocks, overrides, latency and raw spend. Run /tacano:report, or /tacano:report --since YYYY-MM-DD.
disable-model-invocation: true
allowed-tools: Bash(python "${CLAUDE_PLUGIN_ROOT}/scripts/report.py" *)
---

!`python "${CLAUDE_PLUGIN_ROOT}/scripts/report.py" $ARGUMENTS`

Show the user the report above verbatim in a code block, then add at most three short lines in their language: the estimated total saved, how many delegation hints were followed, and anything that looks off (Jev errors, many overrides). Do not run anything else.
