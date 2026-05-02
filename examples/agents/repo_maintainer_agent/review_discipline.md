## Review Discipline: Cross-Cutting Paths

Every non-trivial change in a repo must be reviewed against the
**cross-cutting paths** of that repo, not just the function being
changed. A cross-cutting path is an end-to-end flow that traverses
multiple modules — for example, a "resume from snapshot" path that
goes cli → session loader → orchestration → persistence. Local code
review (looking only at the file being edited) routinely passes fixes
that look correct in isolation but break a path that traverses the
same code from a different direction.

This is the single biggest source of "the change reviewed clean but
production broke" incidents. It happens because reviewers think
**finding-driven** ("what does this fix change?") instead of
**path-driven** ("which user-visible flows still need to work
afterwards?").

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
Save it under `/workspace/.opensage/lessons.md` in a top-level
section called `## Cross-cutting paths`. Each entry should be:

```
### <path-name>
- **Trigger:** <what user/system action invokes this>
- **Sequence:** <module1.func1 → module2.func2 → ...>
- **Invariants:** <what must hold for the path to work>
- **Past breakage:** <one-line note on a fix that almost broke it,
  if any>
```

Re-read this section every time you take on a new task. The list
grows as you find more paths; you do not start with a complete list,
and that is fine.

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

### A concrete failure mode to remember

A localized fix wanted to prevent duplicate session ids by raising
on disk-collision before writing. Reviewed locally inside one
function, the change looked fine. But the project had a separate
**resume** path that re-uses an existing session id by design — its
adopt branch *required* the disk dir to already exist. The new
raise unconditionally fired on every resume.

The mistake was finding-driven review: the reviewer asked "is this
local guard correct?" and the answer was yes. The path-driven
question — "does the resume path still complete end to end?" —
would have caught it in seconds, because the reviewer would have
been forced to write `resume: BREAKS — adopt path expects existing
dir`.

If you cannot answer the path-driven question for a fix, you have
not finished reviewing it.

### Lessons upkeep interaction

When you discover a new cross-cutting path during a task, add it to
the `## Cross-cutting paths` section of `lessons.md` BEFORE running
your design loop. The proposer and critic will pick up the entry
the next time they are invoked.

When a fix gets close to breaking a path (caught by the critic, or
caught only after a try-and-revert), add a `### <fix-summary>`
entry under the relevant path's `Past breakage` line. This is how
the path list earns its weight over time: each entry has a real
incident behind it, not just a hypothetical concern.
