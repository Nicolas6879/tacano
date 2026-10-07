---
name: setup
description: Opt in (or out) of Opus-as-orchestrator. Adds or removes a short section in your ~/.claude/CLAUDE.md asking Claude to follow tacano delegation hints. Run /tacano:setup, /tacano:setup off, or /tacano:setup status.
disable-model-invocation: true
allowed-tools: Bash(python "${CLAUDE_PLUGIN_ROOT}/scripts/setup.py" *)
---

!`python "${CLAUDE_PLUGIN_ROOT}/scripts/setup.py" $ARGUMENTS`

Tell the user, in their language and in one or two sentences, what the output above says changed. Do nothing else.
