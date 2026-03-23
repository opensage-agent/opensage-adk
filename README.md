# OpenSage

An agent framework that enables AI to create their own agent and tools, as well as providing hierarchical memory structure, and domain-specific tools for software engineering.

> 📖 **Full Documentation**: See [https://docs.opensage-agent.ai](https://docs.opensage-agent.ai)

> 📄 **License**: Apache License 2.0. See [`LICENSE`](LICENSE).

## Key Features

- **AI-created agent structure** provide tools for AI to create and manage agent structure/topology
- **AI-created tools** enable AI to write their own tools.
- **Sandboxed execution** supports isolated execution of tools and targets via
  sandbox backends, with `native` as the current recommended backend
- **Hierarchical memory** provide both long-term and short-term memory
- **Multiple benchmarks** for evaluation: CyberGym, PatchAgent, TerminalBench, SeCodePLT, SWE-bench Pro
- **CLI (`opensage`)** with Web UI for interactive debugging

## Installation

### Prerequisites

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/getting-started/installation/#installing-uv) (dependency manager)
- Docker (required for the `native` sandbox backend)

### Setup

```bash
# install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# at the root of the repo
uv python install
uv sync

# install pre-commit hook (required for committing)
uv run pre-commit install
```

> **Note**: Use `uv run <command>` to run commands with project dependencies. Use `uv add` / `uv remove` instead of `pip` for dependency management. See [uv docs](https://docs.astral.sh/uv/concepts/projects/run) for details.

### Sandbox Setup

OpenSage currently documents `native` as the default and recommended sandbox
backend. Other backends (`remotedocker`, `opensandbox`, `agentdocker-lite`,
`local`, and `k8s`) exist in docs or code, but should currently be treated as
**under development**.

#### CodeQL (for joern/codeql sandbox)

```bash
cd src/opensage/sandbox_scripts
wget https://github.com/github/codeql-action/releases/download/codeql-bundle-v2.18.4/codeql-bundle-linux64.tar.gz
tar -xzf codeql-bundle-linux64.tar.gz codeql
rm -f codeql-bundle-linux64.tar.gz
```

#### Sandbox Python Environment

The notes below apply to the default `native` Docker-based sandbox setup.

Sandbox Docker images use `uv` for Python tooling:

- **venv**: `/app/.venv` (created via `uv venv --python 3.12`)
- **Commands are non-persistent** — use `/app/.venv/bin/python` explicitly

Per-sandbox requirements:

| Sandbox | Python Packages | Dockerfile |
|---------|----------------|------------|
| **main** | `neo4j` | `src/opensage/templates/dockerfiles/main/Dockerfile` |
| **joern** | `httpx`, `websockets` | `src/opensage/templates/dockerfiles/joern/Dockerfile` |

## Project Structure

```
OpenSage/
├── src/opensage/            # Core library
│   ├── agents/            # Agent definitions and creation
│   ├── bash_tools/        # Bash tool scripts
│   ├── cli/               # CLI entry point (opensage)
│   ├── config/            # Configuration loading and schemas
│   ├── evaluation/        # Evaluation utilities
│   ├── features/          # Feature modules (e.g., tool combos)
│   ├── memory/            # Long-term memory management
│   ├── plugins/           # Plugin system
│   ├── sandbox/           # Sandbox backend management
│   ├── sandbox_scripts/   # Sandbox initialization scripts
│   ├── session/           # Session and state management
│   ├── templates/         # Dockerfiles and prompt templates
│   ├── toolbox/           # Tool normalization and utilities
│   ├── util_agents/       # Utility sub-agents (e.g., memory management)
│   └── utils/             # Shared helpers
├── benchmarks/            # Evaluation benchmarks
│   ├── cybergym/          # CyberGym vulnerability detection
│   └── swe_bench_pro/     # SWE-bench Pro
├── docs/                  # Documentation sources
│   ├── hooks/             # MkDocs build hooks
│   └── wiki/              # Markdown docs content
├── examples/              # Example agents and configs
│   ├── agents/            # Full example agents
│   └── agents_with_features/ # Small focused feature examples
├── rl/                    # RL integration launch scripts and configs
└── tests/                 # Unit, integration, and benchmark tests
```

## Quick Start

### 1. Create an Agent

Each agent lives in its own directory with an `agent.py` file that defines a `mk_agent()` factory function:

```
my_agent/
├── agent.py        # Required: defines mk_agent()
├── config.toml     # Optional: sandbox, model, and plugin configuration
└── __init__.py
```

Here's a minimal agent example:

```python
# my_agent/agent.py
from google.adk.models.lite_llm import LiteLlm
from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.general.bash_tool import bash_tool_main

def mk_agent(opensage_session_id: str):
    return OpenSageAgent(
        name="my_agent",
        model=LiteLlm(model="anthropic/claude-sonnet-4-20250514"),
        description="A simple agent with shell access.",
        instruction="You are a helpful coding assistant. Use bash_tool_main to run commands.",
        tools=[bash_tool_main],
        # enabled_skills=["retrieval", "static_analysis"],  # optional: load bash tool skills
    )
```

**API Keys for `LiteLlm` models**

When using `LiteLlm`, pass the API key from environment variables that match
your provider setup.
If `api_key=...` is omitted, LiteLlm follows LiteLLM's default behavior and
reads provider credentials from environment variables (for example,
`OPENAI_API_KEY` for `openai/...` models). The api_key and base_url can also be manually specified

```python
model = LiteLlm(
    model="litellm_proxy/sage-gpt-5.3-codex",
    api_key=os.environ.get("LITELLM_PROXY_API_KEY"),
    base_url="https://xxxx/",
)
```

See `examples/agents/` for more complete examples (debugger, PoC generation, vulnerability detection, etc.).

### 2. Run the Agent

```bash
# launch the Web UI
uv run opensage web \
  --agent  /path/to/my_agent \
  --config /path/to/config.toml \
  --port   8000

# check external dependencies (CodeQL, Docker, kubectl)
uv run opensage dependency-check
```

`opensage web` supports session persistence and resume:

```bash
# default behavior: auto_cleanup=false for web
# on shutdown, saves snapshot under ~/.local/opensage/sessions/<agent_name>_<session_id>/
uv run opensage web --agent /path/to/my_agent --config /path/to/config.toml

# resume latest saved web session
uv run opensage web --resume

# resume a specific saved web session
uv run opensage web --resume-from ctf_agent_c0606edc-2fff-496d-8964-48bdd7f0bd23
```

Notes:
- `--resume` restores the latest saved session snapshot (ADK session + sandbox metadata + resolved runtime config).
- `--resume-from` restores a specific saved snapshot by directory name, bare session id suffix, or absolute path.
- `--resume` can reuse `agent_dir` from saved metadata. If missing in old snapshots, pass `--agent`.
- For legacy snapshots without `resolved_config.toml`, pass `--config` explicitly.

## Evaluation

Each benchmark script supports the following sub-commands:

| Command | Description |
|---------|-------------|
| `generate` | Run agent on the benchmark (multi-threaded) |
| `generate_single_thread` | Run agent on the benchmark (single-threaded, for debugging) |
| `evaluate` | Run benchmark evaluation against agent outputs |
| `run` | Run `generate` then `evaluate` |
| `run_debug` | Run `generate_single_thread` then `evaluate` |

### CyberGym

Install CyberGym in `third_party/cybergym` following its own README:

```shell
cd third_party/cybergym
pip3 install -e '.[dev,server]'
git lfs install
git clone https://huggingface.co/datasets/sunblaze-ucb/cybergym cybergym_data
python scripts/server_data/download.py --tasks-file ./cybergym_data/tasks.json
bash scripts/server_data/download_chunks.sh
7z x cybergym-oss-fuzz-data.7z
```

Start the PoC submission server:

```shell
PORT=8666
POC_SAVE_DIR=./server_poc
CYBERGYM_SERVER_DATA_DIR=./oss-fuzz-data
python3 -m cybergym.server \
    --host 0.0.0.0 --port $PORT \
    --log_dir $POC_SAVE_DIR --db_path $POC_SAVE_DIR/poc.db \
    --cybergym_oss_fuzz_path $CYBERGYM_SERVER_DATA_DIR
```

Run evaluation:

```shell
# static tools only
python -m benchmarks.cybergym.cybergym_static --agent_id=<your_agent_id> run

# dynamic tools
python -m benchmarks.cybergym.cybergym_dynamic --agent_id=<your_agent_id> run

# vulnerability detection
python -m benchmarks.cybergym.cybergym_vul_detection run \
  --agent-id <id> --max_llm_calls 75 --checkout_main_branch \
  --log_level INFO --model_name="gemini-3-pro-preview" \
  --start_idx 0 --end_idx 50 --use_multiprocessing --max_workers 3

# evaluate results
python -m benchmarks.cybergym.cybergym_static --agent_id=<your_agent_id> evaluate
python -m benchmarks.cybergym.cybergym_dynamic --agent_id=<your_agent_id> evaluate
```

### SWE-bench Pro

```shell
# basic run
python -m benchmarks.swe_bench_pro.swe_bench_pro run \
  --model_name="gemini-3-flash-preview" \
  --start_idx 0 --end_idx 10 \
  --max_workers 3

# with explore agent (runs a pre-exploration step before the main agent)
python -m benchmarks.swe_bench_pro.swe_bench_pro run \
  --model_name="gemini-3-flash-preview" \
  --use_explore_agent --explore_max_llm_calls 40 \
  --start_idx 0 --end_idx 10 \
  --max_workers 3

# skip already-solved tasks on re-runs
python -m benchmarks.swe_bench_pro.swe_bench_pro run \
  --model_name="gemini-3-flash-preview" \
  --skip_existing --skip_successful \
  --max_workers 3

# run specific tasks from a file (one task ID per line)
python -m benchmarks.swe_bench_pro.swe_bench_pro run \
  --model_name="gemini-3-flash-preview" \
  --task_file ./tasks_to_run.txt \
  --max_workers 3

# evaluate results
python -m benchmarks.swe_bench_pro.swe_bench_pro evaluate
```

### SeCodePLT

```shell
python -m benchmarks.secodeplt.vul_detection run \
  --agent-id <id> --max_llm_calls 75 --log_level INFO \
  --start_idx 1 --end_idx 2 --model_name="gemini-3-pro-preview" \
  --output_dir ./evals/secodeplt/test --skip_poc --max_workers 1
```

With memory:

```shell
python -m benchmarks.secodeplt.vul_detection_memory run_debug \
  --agent-id <id> --max_llm_calls 75 --log_level INFO \
  --start_idx 1 --end_idx 2 --model_name="gemini-3-pro-preview" \
  --output_dir ./evals/secodeplt/test_memory --skip_poc --max_workers 1
```




## Short-Term Memory

Short-term memory records agent execution traces into Neo4j, capturing inputs, outputs, and intermediate steps for each agent run.

### 1. Agent Lifecycle Logging (Patched `run_async`)

Before starting an agent, the monkey-patch in `src/opensage/patches/neo4j_logging.py` must be applied via `neo4j_logging.apply()` and enabled via `neo4j_logging.enable()`. This wraps `BaseAgent.run_async` and `AgentTool.run_async` to automatically:

- **Record agent start/end** — creates `AgentRun` nodes in Neo4j with start time, end time, status (`completed` or `error`), and final output text.
- **Record agent input** — the initial user message is captured when the agent run begins (`record_agent_start`).
- **Record agent output** — the last event's text content is captured when the agent run ends (`record_agent_end`).
- **Record agent-to-agent calls** — when a parent agent invokes a sub-agent via `AgentTool`, an `AGENT_CALLS` relationship is created between their `AgentRun` nodes, along with the request content.
- **Log each event** — every event streamed during execution is written to Neo4j via `log_single_event_neo4j`, creating `Event` nodes linked to the `AgentRun`.
- **Store session state** — the final session state is persisted on the `AgentRun` node upon completion.

### 2. Intermediate Steps Recording

All intermediate event recording is handled by the patched `run_async` itself — `log_single_event_neo4j` ([neo4j_logging.py:100](src/opensage/patches/neo4j_logging.py#L100)) writes every streamed event (tool calls, model responses, etc.) as `Event` nodes linked to the `AgentRun` via `HAS_EVENT` relationships. No plugin is responsible for this recording.

### 3. Summarization Plugins

Two plugins handle compaction when recorded data grows too large:

| Plugin | File | What It Does |
|--------|------|--------------|
| **ToolResponseSummarizerPlugin** | `src/opensage/plugins/tool_response_summarizer_plugin.py` | When a tool response exceeds `max_tool_response_length` (default 10000 chars), saves the full raw response as a `RawToolResponse` node and replaces the in-context response with an LLM-generated summary. Linked to `AgentRun` via `AGENT_RUN_HAS_RAW_TOOL_RESPONSE`. |
| **HistorySummarizerPlugin** | `src/opensage/plugins/history_summarizer_plugin.py` | When event history exceeds the context budget, compacts old events into summary `Event` nodes (type `history_summary`). Creates `SUMMARIZES_EVENTS` relationships to the original events for lineage. |

## Development

Use git subtree to add third-party dependencies:

```bash
git subtree add --prefix third_party/cybergym https://github.com/sunblaze-ucb/cybergym.git main --squash
```

### Fuzzing Architecture

**Path A: Simplified Python Fuzzer** (ad-hoc, runs in `main` sandbox)

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
│  │       │  loop:                                  │     │
│  │       │    1. Load/create seed                  │     │
│  │       │    2. Mutate seed (grammar-aware)       │     │
│  │       │    3. Feed to target program            │     │
│  │       │    4. If crash → save to /tmp/crash_*   │     │
│  │       │    5. Repeat                            │     │
│  │       ▼                                        │     │
│  │  /tmp/crash_* files                            │     │
│  └────────────────────────────────────────────────┘     │
│                                                         │
│  Post-run: find /tmp -name 'crash_*'                    │
│            Read up to 5 crash files                     │
│            Return results                               │
└─────────────────────────────────────────────────────────┘
```

**Path B: AFL++ Campaign** (structured, runs in `fuzz` sandbox)

```
┌──────────────────────────────────────────────────────────────────┐
│  Agent                                                          │
│  1. Creates seed files in the container                         │
│  2. Optionally writes a custom mutator (def fuzz(...))          │
│  3. Calls run_fuzzing_campaign                                  │
└──────────┬───────────────────────────────────────────────────────┘
           │ seeds, custom_mutator
           ▼
┌──────────────────────────────────────────────────────────────────┐
│  run_fuzzing_campaign                                           │
│                                                                 │
│  ┌───────────────────── fuzz sandbox ──────────────────────┐    │
│  │                                                         │    │
│  │  /fuzz/in/          ← seed inputs copied here           │    │
│  │  /fuzz/mutator/     ← custom_mutator.py (if provided)   │    │
│  │  /out/{target}      ← AFL++-instrumented binary         │    │
│  │                                                         │    │
│  │  AFL++ fuzzing loop (3 min):                            │    │
│  │  ┌────────────────────────────────────────────┐         │    │
│  │  │                                            │         │    │
│  │  │  Pick seed from /fuzz/in (or queue)        │         │    │
│  │  │         │                                  │         │    │
│  │  │         ▼                                  │         │    │
│  │  │  Mutate (default or custom mutator)        │         │    │
│  │  │         │                                  │         │    │
│  │  │         ▼                                  │         │    │
│  │  │  Feed to /out/{target}                     │         │    │
│  │  │         │                                  │         │    │
│  │  │         ▼                                  │         │    │
│  │  │  Monitor execution via instrumentation     │         │    │
│  │  │    ├── new coverage? → add to queue        │         │    │
│  │  │    ├── crash?        → save to crashes/    │         │    │
│  │  │    └── normal exit   → discard             │         │    │
│  │  │         │                                  │         │    │
│  │  │         └──── loop ◄───────────────────────┘         │    │
│  │  │                                                      │    │
│  │  │  /fuzz/out/                                          │    │
│  │  │    ├── queue/         (interesting inputs)           │    │
│  │  │    ├── crashes/       (crash-triggering inputs)      │    │
│  │  │    └── fuzzer_stats   (execution statistics)         │    │
│  │  └──────────────────────────────────────────────────────┘    │
│  │                                                              │
│  │  _analyze_fuzzing_results → count crashes                    │
│  └──────────────────────────────────────────────────────────────┘
│                                                                  │
│  Post-campaign tools:                                            │
│    check_fuzzing_stats  → parse fuzzer_stats file                │
│    extract_crashes      → copy crash inputs to target dir        │
└──────────────────────────────────────────────────────────────────┘
```


## License
