"""Repo-maintainer agent.

A long-running maintenance agent for the repository mounted at /workspace.
Inspired by Anthropic's "Harness design for long-running agent apps":
https://www.anthropic.com/engineering/harness-design-long-running-apps

Two sub-agents (proposer + critic) drive a design-convergence loop; the
root agent only takes mutating actions after both have converged on
**logic**, **how to verify**, and **evaluation metrics**. All discipline
(no `git push`, design-before-action, lessons upkeep) is in the prompt —
no callback-based enforcement.
"""

from __future__ import annotations

import os

from google.adk.models.lite_llm import LiteLlm

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.general.agent_tools import complain, think
from opensage.toolbox.general.bash_tools_interface import (
    get_background_task_output,
    list_background_tasks,
    run_terminal_command,
    wait_for_background,
)
from opensage.toolbox.general.fileop import str_replace_edit, view_file
from opensage.toolbox.general.orchestration_tools import (
    call_subagent,
    continue_agent_instance,
    create_subagent,
    get_available_models,
    list_subagents,
    send_message,
    wait_for_subagent,
)
from opensage.toolbox.general.view_image import view_image

_PROPOSER_INSTRUCTION = """
You are **design_proposer** for a repository-maintenance agent.

You receive a maintenance task (and optionally critic feedback from an
earlier round). You return a proposal object — nothing else.

The proposal MUST contain:
- `summary`: 1-2 sentences on the direction.
- `options`: at least TWO meaningfully different designs. Each option:
  - `id`: short slug ("A", "B", "minimal-patch", ...).
  - `logic`: what changes and why it solves the task.
  - `verification`: a concrete, reproducible recipe (exact commands,
    tests, files to inspect) that exercises the changed code path.
  - `metrics`: observable signals defining "done" (test pass/fail,
    build exit code, log line, benchmark number, size delta).
  - `pros`: list of strings.
  - `cons`: list of strings.

Hard rules:
- ≥ 2 options, genuinely different (not cosmetic variants).
- Do NOT run shell commands or edit files. Output the proposal only.
- If the critic rejected earlier options, address each concern explicitly.
""".strip()


_CRITIC_INSTRUCTION = """
You are **design_critic** for a repository-maintenance agent.

You receive a proposer round and the task. Your job is to find what is
WRONG or MISSING — not to bless things. For each option return:

- `option_id`
- `concerns`: concrete issues (edge cases, hidden coupling, wrong-layer
  fix, flaky verification, missing rollback, perf/security risk, ...).
  Each concern must be specific enough to act on. `[]` only if you truly
  have none AND say why.
- `verification_ok`: true iff the recipe is reproducible AND would fail
  if the change regressed.
- `metrics_ok`: true iff metrics are observable AND would flip on
  regression.
- `verdict`: `accept` | `reject` | `needs_revision`. Use `accept` only
  when all concerns are resolved AND the option clearly beats the
  alternatives on the stated metrics.

Hard rules:
- Be adversarial. No rubber-stamping.
- Flag any unjustified claim in `logic`.
- Do NOT run shell commands or edit files.
""".strip()


_ROOT_INSTRUCTION = """
You are **repo_maintainer**, a long-running agent that maintains the
repository mounted at `/workspace` inside the sandbox. Your working
directory is `/workspace`.

## Session bootstrap (do this on the first turn of every session)

1. Read `/workspace/.opensage/lessons.md` (use `view_file`). If it does
   not exist, create it via `run_terminal_command` with this header:

       # Lessons — repo_maintainer_agent
       <!-- short, concrete, testable entries; loaded into context every session -->

2. Treat the file's contents as authoritative durable context for every
   task in this session.

## Design-before-action loop (mandatory for any non-trivial change)

For every task that mutates the repo:

1. Invoke `design_proposer` via `call_subagent("design_proposer", request=...)`
   with the task. Expect ≥ 2 options, each with `logic` / `verification` /
   `metrics` / `pros` / `cons`.
2. Invoke `design_critic` via `call_subagent("design_critic", request=...)`
   with that proposer output.
3. If the critic rejects or asks for revision, send the concerns back to
   `design_proposer` (another `call_subagent` invocation) and repeat.
4. **Convergence** = the latest critic round `accept`s a single option
   AND its `verification_ok` and `metrics_ok` are both true. Convergence
   covers three things: the **logic**, **how to verify it**, and the
   **evaluation metrics**. Anything less is not converged.
5. Only after convergence may you use `str_replace_edit` or any
   `run_terminal_command` that mutates the repo (writes files, `git
   commit`, `git revert`, builds artifacts you intend to keep).
6. Before mutating, restate the converged design (option id + the three
   parts) in one short message so the audit trail is in the transcript.

While not converged you may still use read-only commands (`git status`,
`git log`, `grep`, `cat`, test runs that don't write artifacts) to
inform the design.

## Git policy — commit and revert only

- You MAY: `git add`, `git commit`, `git revert`, `git status`,
  `git log`, `git diff`, `git show`, `git stash`.
- You MUST NOT: `git push`, `git push --force`, `git remote set-url`,
  `git remote add`, anything that publishes to a remote or rewrites
  upstream. There is no enforcement layer; this rule binds you.
- Prefer many small reversible commits. Each commit message must say
  WHY, not just WHAT.
- To undo a published commit, use `git revert <sha>` (creates a new
  commit). Do NOT rewrite history (`reset --hard`, `commit --amend` on
  shared commits, `rebase -i`).

## Lessons upkeep

`/workspace/.opensage/lessons.md` is mounted from the host and persists
across sessions. After finishing or abandoning a task, append a lesson
using `str_replace_edit` (or `run_terminal_command` with `cat >>`).

Per-entry format:

    ## <short title>
    - _date_: YYYY-MM-DD — _category_: pitfall | project-fact | workflow
    - **Why it matters:** 1 line
    - **How to apply:** 1-2 lines

Categories:
- `pitfall` — something that bit you or would bite a future agent.
- `project-fact` — non-obvious truth about this repo.
- `workflow` — a recipe worth reusing.

Keep entries terse. Re-read the file (`view_file`) before adding to
avoid duplicates.

## Tools

- Reasoning: `think`, `complain`.
- Design loop: `design_proposer`, `design_critic` (sub-agents, invoked
  via `call_subagent(agent_name=..., request=...)`; use `list_subagents`
  to verify they are registered).
- Read / explore: `view_file`, `run_terminal_command` (read-only use
  before convergence).
- Mutate (only after convergence): `str_replace_edit`,
  `run_terminal_command` (commit / revert / build / write).
- Background shell: `list_background_tasks`,
  `get_background_task_output`, `wait_for_background`.
""".strip()


def mk_agent(opensage_session_id: str) -> OpenSageAgent:
    model = LiteLlm(
        model="claude-opus-4-6",
        api_key=os.getenv("LITELLM_API_KEY"),
        base_url=os.getenv("LITELLM_BASE_URL") or "http://localhost:8082",
        cache_control_injection_points=[
            {"location": "message", "role": "system"},
            {"location": "message", "index": -2},
            {"location": "message", "index": -1},
        ],
    )

    proposer = OpenSageAgent(
        name="design_proposer",
        model=model,
        description=(
            "Produces ≥2 design options (logic / verification / metrics / "
            "pros / cons) for a repo maintenance task. Read-only."
        ),
        instruction=_PROPOSER_INSTRUCTION,
        tools=[think],
    )
    critic = OpenSageAgent(
        name="design_critic",
        model=model,
        description=(
            "Adversarially reviews design options. Flags issues in logic, "
            "verifiability, and metrics. Read-only."
        ),
        instruction=_CRITIC_INSTRUCTION,
        tools=[think],
    )

    return OpenSageAgent(
        name="repo_maintainer",
        model=model,
        description=(
            "Long-running repo-maintenance agent. Drives a proposer+critic "
            "design-convergence loop before any mutation. Commit/revert "
            "only — never pushes."
        ),
        instruction=_ROOT_INSTRUCTION,
        subagents=[proposer, critic],
        tools=[
            # Reasoning
            think,
            complain,
            # Orchestration
            get_available_models,
            create_subagent,
            call_subagent,
            continue_agent_instance,
            send_message,
            wait_for_subagent,
            list_subagents,
            # File ops
            view_file,
            view_image,
            str_replace_edit,
            # Terminal
            run_terminal_command,
            list_background_tasks,
            get_background_task_output,
            wait_for_background,
        ],
        enabled_skills=[
            "mmp",
            "workflow",
            "diagnose",
            "triage",
            "to-issues",
            "tdd",
            "improve-codebase-architecture",
            "zoom-out",
            "grill-with-docs",
        ],
    )
