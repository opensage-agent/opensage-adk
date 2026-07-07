---
name: workflow
description: "Multi-agent orchestration patterns for orchestrator agents. Three reusable patterns built on the existing call_subagent / wait_for_subagent / create_subagent / get_available_models tools. Available patterns: ensemble, pool, multi-model."
---

# Workflow Patterns

Category of orchestration patterns for composing existing subagent tools to solve common multi-agent problems. No new tools — every pattern is pure prompt-level guidance for the orchestrator.

## Available Patterns

- **ensemble**: Run the same task on multiple agents/models in parallel and reduce the answers (vote / compare).
- **pool**: Run N tasks under a concurrency cap K (rate-limit-aware sliding window).
- **multi-model**: Recovery strategies when a subagent is stuck — fresh-context retry, or retry with a different model.

## Usage

These patterns are read by orchestrator agents through their system prompt. Each sub-skill's SKILL.md describes when to use the pattern and lays out the recipe in pseudocode. They use only existing tools (`call_subagent`, `wait_for_subagent`, `create_subagent`, `get_available_models`, `send_message`, `list_subagents`) — no scripts.

## Common Use Cases

- High-stakes question with multiple opinions needed (ensemble)
- Many independent subtasks under a shared rate limit (pool)
- A subagent has clearly stalled on a hard task (multi-model)

## Requires Sandbox

None — pattern docs are pure prompt guidance.
