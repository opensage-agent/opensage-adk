# Fuzzing Agent

OpenSage-ADK agents can drive fuzzing through two paths, each backed by a different sandbox and aimed at a different use case.

| Path | Tool | Sandbox | Fit |
|---|---|---|---|
| **A: Simplified Python Fuzzer** | `simplified_python_fuzzer` | `main` | Ad-hoc fuzzing where the agent writes the whole fuzzer as a Python script. |
| **B: AFL++ Campaign** | `run_fuzzing_campaign` | `fuzz` | Structured campaigns against an AFL++-instrumented binary, optionally with a custom mutator. |

## Path A: Simplified Python Fuzzer

The agent analyzes the target, writes a complete Python fuzzer, and the tool ships that script into the `main` sandbox and runs it for a short budget.

```
┌─────────────────────────────────────────────────────────┐
│  Agent                                                  │
│  1. Analyzes the target program's input format          │
│  2. Writes a complete Python fuzzer script              │
│     (mutation logic, crash detection, saving crashes)   │
└──────────────────────┬──────────────────────────────────┘
                       │ script (str)
                       ▼
┌─────────────────────────────────────────────────────────┐
│  simplified_python_fuzzer                               │
│                                                         │
│  Host side:                                             │
│    Write script to temp file                            │
│    Copy into container at /tmp/fuzzer.py                │
│                                                         │
│  ┌───────────────── main sandbox ─────────────────┐     │
│  │                                                │     │
│  │  python3 /tmp/fuzzer.py  (runs for 70s)        │     │
│  │       │                                        │     │
│  │       │  loop:                                 │     │
│  │       │    1. Load/create seed                 │     │
│  │       │    2. Mutate seed (grammar-aware)      │     │
│  │       │    3. Feed to target program           │     │
│  │       │    4. If crash -> save to /tmp/crash_* │     │
│  │       │    5. Repeat                           │     │
│  │       ▼                                        │     │
│  │  /tmp/crash_* files                            │     │
│  └────────────────────────────────────────────────┘     │
│                                                         │
│  Post-run: find /tmp -name 'crash_*'                    │
│            Read up to 5 crash files                     │
│            Return results                               │
└─────────────────────────────────────────────────────────┘
```

## Path B: AFL++ Campaign

The agent stages seed inputs (and, optionally, a custom mutator) inside the `fuzz` sandbox, then calls `run_fuzzing_campaign`. AFL++ runs on the instrumented binary for the campaign's duration; the tool reports crash counts, and follow-up tools read statistics and extract crash inputs.

```
┌──────────────────────────────────────────────────────────────────┐
│  Agent                                                           │
│  1. Creates seed files in the container                          │
│  2. Optionally writes a custom mutator (def fuzz(...))           │
│  3. Calls run_fuzzing_campaign                                   │
└──────────┬───────────────────────────────────────────────────────┘
           │ seeds, custom_mutator
           ▼
┌──────────────────────────────────────────────────────────────────┐
│  run_fuzzing_campaign                                            │
│                                                                  │
│  ┌───────────────────── fuzz sandbox ──────────────────────┐     │
│  │                                                         │     │
│  │  /fuzz/in/          <- seed inputs copied here          │     │
│  │  /fuzz/mutator/     <- custom_mutator.py (if provided)  │     │
│  │  /out/{target}      <- AFL++-instrumented binary        │     │
│  │                                                         │     │
│  │  AFL++ fuzzing loop (3 min):                            │     │
│  │  ┌────────────────────────────────────────────┐         │     │
│  │  │                                            │         │     │
│  │  │  Pick seed from /fuzz/in (or queue)        │         │     │
│  │  │         │                                  │         │     │
│  │  │         ▼                                  │         │     │
│  │  │  Mutate (default or custom mutator)        │         │     │
│  │  │         │                                  │         │     │
│  │  │         ▼                                  │         │     │
│  │  │  Feed to /out/{target}                     │         │     │
│  │  │         │                                  │         │     │
│  │  │         ▼                                  │         │     │
│  │  │  Monitor execution via instrumentation     │         │     │
│  │  │    ├── new coverage? -> add to queue       │         │     │
│  │  │    ├── crash?        -> save to crashes/   │         │     │
│  │  │    └── normal exit   -> discard            │         │     │
│  │  │         │                                  │         │     │
│  │  │         └──── loop <──────────────────────┘          │     │
│  │  │                                                      │     │
│  │  │  /fuzz/out/                                          │     │
│  │  │    ├── queue/         (interesting inputs)           │     │
│  │  │    ├── crashes/       (crash-triggering inputs)      │     │
│  │  │    └── fuzzer_stats   (execution statistics)         │     │
│  │  └──────────────────────────────────────────────────────┘     │
│  │                                                               │
│  │  _analyze_fuzzing_results -> count crashes                    │
│  └───────────────────────────────────────────────────────────────┘
│                                                                   │
│  Post-campaign tools:                                             │
│    check_fuzzing_stats  -> parse fuzzer_stats file                │
│    extract_crashes      -> copy crash inputs to target dir        │
└───────────────────────────────────────────────────────────────────┘
```

## Picking a Path

- Use **Path A** when the target is simple enough that a Python loop can drive it, or when the agent needs to express custom input-shape logic directly.
- Use **Path B** when the target has an AFL++-instrumented build and coverage-guided mutation is worth the sandbox startup cost.

## Related References

- [Tools](../components/tools.md): how tools are declared, wrapped, and routed to sandboxes.
- [Sandbox](../components/sandbox.md): `main` and `fuzz` backend details.
