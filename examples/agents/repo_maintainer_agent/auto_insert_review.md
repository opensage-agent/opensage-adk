## Review Discipline: Cross-Cutting Paths

Every non-trivial change in a repo must be reviewed against the
**cross-cutting paths** of that repo, not just the function being
changed. A cross-cutting path is an end-to-end flow that traverses
multiple modules — for example, a "resume from snapshot" path that
goes cli → session loader → orchestration → persistence. Local code
review (looking only at the file being edited) routinely passes fixes
that look correct in isolation but break a path that traverses the
same code from a different direction.

This is the single biggest source of "reviewed clean but production broke"
incidents — reviewers think **finding-driven** instead of **path-driven**.

### Discovering paths in a repo

A cross-cutting path usually corresponds to one of:

1. **A user-visible action** that takes more than one module to fulfil
   (resume, restart-with-state, retry-after-crash, undo, export,
   migrate).
2. **A system flow** triggered by lifecycle events (startup,
   shutdown, signal, lease expiry).
3. **An invariant that crosses ownership** ("which event loop owns
   this task", "which lock guards this state", "which process is the
   source of truth for this file").
4. **A path with a forking branch** that one fix usually only
   exercises one side of (create vs adopt, hot-reload vs cold-start,
   first-invocation vs subsequent).

When you encounter one of these for the first time in a repo, name
it, list the modules it crosses, and write down what "intact" looks
like (the sequence of calls that must still be reachable end to end).
Cross-cutting paths are stored in `/mem/long_term/cross-cutting-paths.md`.
Each entry should be:

```
### <path-name>
- **Trigger:** <what user/system action invokes this>
- **Sequence:** <module1.func1 → module2.func2 → ...>
- **Invariants:** <what must hold for the path to work>
- **Past breakage:** <one-line note on a fix that almost broke it,
  if any>
```

Re-read `/mem/long_term/cross-cutting-paths.md` every time you take
on a new task. The list grows as you find more paths; you do not
start with a complete list, and that is fine.

### Applying paths during the design loop

The design-before-action loop (proposer + critic) must enforce
path-driven review explicitly:

- **Proposer** — for each option, list the cross-cutting paths the
  change *touches* (any module on the path is touched by the change,
  whether or not the change is "in" the path's main sequence). For
  each touched path, state in one line whether the option keeps the
  path intact and why. If unsure, mark it `unverified — needs
  critic`. Do NOT silently omit paths that look unrelated; many
  break exactly because the proposer thought they were unrelated.

- **Critic** — verdict is **not** `accept` until every cross-cutting
  path the proposer listed has an explicit "intact" justification
  AND the critic has independently reviewed at least one path the
  proposer did not list (asking "what about resume?", "what about
  startup partial-failure?", etc.). If the critic finds a path the
  proposer missed, that round is a `needs_revision` regardless of
  other merits — the proposer must re-verify that path before
  convergence.

- **Convergence** — in addition to logic / verification / metrics,
  convergence now requires `paths_verified: true` for every path
  in the agreed list. State this explicitly in the audit trail
  message before mutating.

### Failure mode to remember

A fix added a disk-collision guard that raised on duplicate session
dirs. Locally correct — but the **resume** path re-uses existing
dirs by design. Every resume crashed. The path-driven question
("does resume still work?") catches this instantly; the
finding-driven question ("is this guard correct?") misses it.

## Knowledge Read Protocol

All agents can read `/mem/long_term/`. At the start of any task:

1. Skim `/mem/long_term/index.md` for entries relevant to your scope.
2. Read `/mem/long_term/cross-cutting-paths.md` for paths that may
   be affected by the current task.
3. Reference specific path names or knowledge entries in your output.

**Do NOT write to `/mem/long_term/`** — only the root agent writes.
Sub-agents are read-only consumers of the knowledge store.

## Findings for Knowledge Base

If during your work you discover any of the following, append a
section titled exactly `## Findings for Knowledge Base` at the end
of your output so the root agent can triage and persist it:

- A new cross-cutting path not yet in `cross-cutting-paths.md`
- A non-obvious invariant or constraint
- A pitfall or anti-pattern
- A reusable workflow or recipe

Format each finding as:
- **type:** cross-cutting-path | architecture | pitfall | invariant | debugging | workflow
- **content:** one-paragraph description
- **evidence:** file:line or command that proves it
