## Long-term Memory Policy

You share `/mem/long_term/` with every other agent in this OpenSage session.
Treat it as a small wiki of durable, hard-won knowledge.

**What to persist there.** Save high-level knowledge that is genuinely
expensive to re-derive — typically anything that takes more than ~10 tool
calls to figure out from scratch. Examples:

- Authentication / authorization mechanism of `nginx`
- A library's surprising default behavior you only learned by reading source
- A non-obvious build flag combination that fixes a class of failures
- A summary of a long debugging session: root cause + the wrong hypotheses
  you ruled out

**What NOT to persist there.** Skip things that are cheap to look up again
(the location of a single function, a function's signature, the contents of a
specific config file). Those belong in your short-term notes (`TODO.md`) or
nowhere.

**File layout.**

- One knowledge entry per `.md` file under `/mem/long_term/`.
- Filename should be a slug of the topic (e.g. `nginx-auth.md`,
  `libpng-tile-decoder-quirks.md`). No spaces.
- `/mem/long_term/index.md` is the table of contents. Each line is exactly:
  `filename.md — one-line summary`.
- The summary line is what other agents skim first; keep it specific enough
  to decide "should I open this file?" in one read.

**Maintenance is your job.** Whenever you finish a non-trivial subtask:

1. Skim `index.md`. Did you learn anything that future-you (or another agent)
   would want and can't find here?
2. If yes, write a new `.md` and append a line to `index.md`. Be concise —
   one short markdown file beats a long one.
3. If you found an existing entry to be wrong or stale, update or remove it
   (and its index line) instead of leaving the contradiction.

Skipping this step is not "saving time" — it shifts cost onto every future
agent that has to re-discover the same thing. Maintain the wiki.
