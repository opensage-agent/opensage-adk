# Lessons — repo_maintainer_agent
<!-- short, concrete, testable entries; loaded into context every session -->

## repo_maintainer_agent memory system redesign
- _date_: 2026-05-10 — _category_: project-fact
- **Why it matters:** The agent's knowledge management was fully redesigned — future sessions should use /mem/long_term/, not lessons.md
- **How to apply:** Knowledge files go in /mem/long_term/ (mounted from .opensage/knowledge/). auto_insert_review.md replaces review_discipline.md. _ROOT_INSTRUCTION has the full write policy (admission test, format, capacity, git-tracking).

## auto_insert_prompt_file overrides framework default.md
- _date_: 2026-05-10 — _category_: pitfall
- **Why it matters:** Setting auto_insert_prompt_file in config.toml REPLACES the framework's default long-term memory policy — it doesn't supplement it. If you only put review discipline there, agents never learn about /mem/long_term/.
- **How to apply:** Always include both review discipline AND knowledge read/write protocol in the auto-insert file, or ensure _ROOT_INSTRUCTION covers the missing pieces.

## /mem/long_term/ and /workspace/.opensage/knowledge/ are the same directory
- _date_: 2026-05-10 — _category_: project-fact
- **Why it matters:** Mount aliasing means writes to either path affect the same files. Use /mem/long_term/ for agent read/write, but `cd /workspace && git add .opensage/knowledge/` for git operations.
- **How to apply:** Agent prompts must document both paths and when to use each. The /workspace path is needed because /mem/long_term/ is not inside a git working tree.

## Cross-cutting paths

### auto-insert-prompt-injection
- **Trigger:** Any agent invocation (root or sub-agent)
- **Sequence:** config.toml[auto_insert_prompt_file] → resolve_auto_insert_prompt_path() → reads .md from agent dir → _inject_runtime_blocks() → appends to agent.instruction → stripped in finally block
- **Invariants:** File must exist at the resolved path; content is injected into ALL agents (root + sub); original instruction restored after invocation
- **Past breakage:** Overriding to review_discipline.md caused total loss of /mem/long_term/ awareness for all agents
