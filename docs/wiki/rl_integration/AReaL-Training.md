# AReaL Training with OpenSage

## 1. Setup

```bash
git clone --recurse-submodules -b adk https://github.com/rucnyz/AReaL
cd AReaL
pip install uv
uv sync --extra cuda
```

The SeCodePLT benchmark uses CodeQL for call graph analysis. Download and install the CodeQL bundle:

```bash
cd OpenSage/src/opensage/sandbox_scripts
wget https://github.com/github/codeql-action/releases/download/codeql-bundle-v2.18.4/codeql-bundle-linux64.tar.gz
tar -xzf codeql-bundle-linux64.tar.gz codeql
rm -f codeql-bundle-linux64.tar.gz
```

After this, `sandbox_scripts/` should contain: `callgraph/`, `codeql/`, `ossfuzz/`.

## 2. Running

A launch script is provided at `examples/opensage/run_opensage_grpo.sh`:

```bash
# Default 4-GPU training (SGLang TP=2 inference + FSDP DP=2 training)
./examples/opensage/run_opensage_grpo.sh --trial my_experiment

# 2-GPU mode
GPUS=0,1 NGPU=2 ALLOCATION=sglang:d1p1t1+fsdp:d1p1t1 ./examples/opensage/run_opensage_grpo.sh

# All options (CLI args or env vars):
#   --trial NAME       Trial name (default: auto-generated timestamp)
#   --gpus 2,3,5,6     CUDA_VISIBLE_DEVICES (default: 2,3,5,6)
#   --ngpu 4           Number of GPUs (default: 4)
#   --batch 2          Training batch size (default: 2)
#   ALLOCATION=...     allocation_mode (default: sglang:d1p1t2+fsdp:d2p1t1)
#   MAX_CONCURRENT=4   Max concurrent rollouts (default: 4)
```

The script automatically kills stale sglang/rpc processes before starting.

### Default Configuration

The YAML config (`examples/opensage/opensage_grpo_mt.yaml`) includes sensible defaults:

| Parameter | Default | Description |
|---|---|---|
| `actor.path` | `Qwen/Qwen3-4B` | Base model |
| `gconfig.max_new_tokens` | `8192` | Token budget across all turns |
| `gconfig.n_samples` | `4` | Rollout samples per prompt (for GRPO variance) |
| `max_tokens_per_mb` | `65536` | Micro-batch capacity (must fit prompt + generation) |
| `agent_run_args.max_turns` | `20` | Max agent turns per episode |
| `log_raw_conversation` | `true` | Save full trajectories for analysis |
| `export_style` | `concat` | Concatenate multi-turn interactions for training |
| `generation_kwargs` | `{extra_body: {chat_template_kwargs: {enable_thinking: true}}}` | Generic kwargs merged into every `chat.completions.create()` call. Any key-value pairs here are passed through to the OpenAI-compatible API. |

### Manual Launch (without script)

```bash
pkill -9 -f sglang; pkill -9 -f rpc_server

CUDA_VISIBLE_DEVICES=2,3,5,6 uv run examples/opensage/opensage_rl_mt.py \
    --config examples/opensage/opensage_grpo_mt.yaml \
    scheduler.type=local \
    trial_name=my_experiment \
    allocation_mode=sglang:d1p1t2+fsdp:d2p1t1 \
    cluster.n_nodes=1 \
    cluster.n_gpus_per_node=4 \
    train_dataset.batch_size=2 \
    rollout.max_concurrent_rollouts=4 \
    rollout.max_head_offpolicyness=0
```

### Output Locations

```
output/experiments/logs/<user>/<experiment_name>/<trial_name>/
├── generated/raw_conversations/     # Per-rollout trajectory files
│   └── <data_id>_<uuid>/
│       ├── turn_000.json            # Raw model I/O per turn (see below)
│       ├── trajectory_summary.json  # Lightweight summary
│       └── trajectory_full.json     # Parsed/structured trajectory
├── stats/                           # Training metrics
└── ...

OpenSage/evals/secodeplt/<timestamp>/  # Per-task evaluation outputs
└── <task_id>/
    ├── vulnerability_findings_*.json
    ├── poc_findings_*.json
    ├── metadata.json
    ├── config_used.toml
    ├── sandbox_output/
    └── neo4j_history/
```

**Trajectory file formats:**

| File | Source | Content |
|---|---|---|
| `turn_NNN.json` | `on_generate` callback | Raw model I/O: `input` is the full chat template string (with `<\|im_start\|>` tokens), `output` is the raw generated text (e.g. `<tool_call>` XML). Pre-parse. |
| `trajectory_full.json` | `_dump_trajectory_json` | Parsed/structured: each turn has `role`, `tool_calls` (list of name+args), `tool_responses` (list of results). From `ArealOpenAI._cache` interaction objects. |
| `trajectory_summary.json` | `_dump_trajectory_json` | Lightweight: turn count, tools used, final reward, token counts. No full content. |

Use turn files to debug tokenizer/parser issues; use trajectory_full to analyze agent behavior.

## 3. Changes for AReaL Integration

Summary of modifications made to OpenSage for AReaL RL training integration.

### RL client: `model_name` passthrough (`rl_integration/client.py`)

`Client.__init__` and `opensage.create()` accept an optional `model_name` parameter.
When provided, it is forwarded to the evaluation class constructor so that prompt
formatting and model-specific logic (e.g. Gemini vs LiteLlm branches) use the correct
model identity instead of the evaluation's default `"gemini-3-pro-preview"`.

```python
# Example: override model for AReaL training
client = opensage.create("vul_agent_static_tools", "secodeplt", model_name="qwen3-8b")
```

The unused `os` import and the hard-coded `log_level` parameter were also cleaned up.

### Output directory: `exist_ok=True` (`evaluations/__init__.py`)

`Evaluation.__post_init__` now uses `mkdir(parents=True, exist_ok=True)` when creating
the output directory. This prevents `FileExistsError` when multiple concurrent rollout
episodes race to create the same directory tree.

### CodeQL sandbox re-enabled

The CodeQL sandbox is now enabled. A previous bug in `tool_normalization.py` caused
`__sandbox_requirements__` metadata to be stripped during tool wrapping, which made
`collect_sandbox_dependencies()` unable to detect CodeQL/Joern requirements. The sandbox
was then pruned as "unused" even when present in the config. This bug is fixed.

## 4. Known Issues

### Race condition on shared `self.model` in SeCodePLT (fixed)

**Symptom:** When running with `group_size >= 2` (multiple concurrent rollout episodes),
the error `"No interaction in cache to set reward for"` appears. Rewards are misrouted
between episodes.

**Root cause:** `SeCodePLT._run_agent` stored the per-task `ArealLlm` model on
`self.model` (a shared instance attribute). When multiple episodes ran concurrently via
`asyncio.gather`, a second episode would overwrite `self.model`, causing the first
episode's LLM calls to use the wrong `ArealOpenAI` client. That client's interaction
cache stayed empty, so `set_last_reward()` failed.

**Fix:** Replaced `self.model = task.model` mutation with a local `model_to_use`
variable. The model is now threaded explicitly through `_detect_vulnerability_with_retry`
and `_generate_poc_with_retry` via a `model` parameter, eliminating the shared mutable
state.

### `__sandbox_requirements__` lost during tool wrapping (fixed)

**Symptom:** `collect_sandbox_dependencies()` only finds `{'neo4j', 'main'}` even though
tools like `search_function` have `@requires_sandbox("neo4j", "codeql", "joern")`. CodeQL
sandbox is pruned as "unused", call graph tools return empty results.

**Root cause:** `_make_safe_dict_callable()` in `tool_normalization.py` wraps tool functions
but only copies 5 attributes. `__sandbox_requirements__` is not in the copy list, so the
metadata is lost when tools are wrapped for safe dict handling.

**Fix:** Added `__sandbox_requirements__` to the attribute copy list in `tool_normalization.py`.

### CodeQL sandbox initialization failures (mostly resolved)

The CodeQL sandbox had two known initialization failures. These appear to be resolved
in recent versions, but may resurface:

**Issue 1 — Neo4j connection failure:**

```
2026-02-17 15:54:31 | ERROR | opensage.sandbox.initializers.codeql:64 -
  CodeQL initialization failed: Failed to read from defunct connection
  IPv4Address(('127.0.0.134', 7687)) (ResolvedIPv4Address(('127.0.0.134', 7687)))
```

The CodeQL initializer cannot connect to the Neo4j instance when inserting call-graph
results. This appears to be a transient networking / container-startup timing issue.

**Issue 2 — Pandas DataFrame column mismatch in `merge_joern_codeql.py`:**

```
2026-02-19 12:42:48 | ERROR | opensage.sandbox.native_docker_sandbox:1163 -
  sandbox 'codeql' (session 82a36c4d-...) state=error -
  Initialization failed: Cannot set a DataFrame with multiple columns
  to the single column caller_id
```

`insert_codeql_results_to_cpg` in `opensage/utils/merge_joern_codeql.py` (line 211)
assigns the result of `df.apply(...)` to `df["caller_id"]`, but the apply returns
multiple columns instead of one, causing a `ValueError`.

To reproduce:
```bash
cd OpenSage

uv run --python ../.venv/bin/python -m src.opensage.evaluations.secodeplt.vul_detection run_debug \
    --agent-id reproduce_codeql \
    --task_ids "arvo:65380" \
    --model_name="gemini-3-pro-preview" \
    --output_dir ./evals/secodeplt/reproduce_codeql \
    --skip_poc \
    --max_workers 1
```

### Neo4j schema warnings (benign)

**Warnings:**

```
Received notification from DBMS server: {severity: WARNING} {code: Neo.ClientNotification.Statement.UnknownPropertyKeyWarning} {category: UNRECOGNIZED} {title: The provided property key is not in the database} {description: One of the property names in your query is not available in the database, make sure you didn't misspell it or that the label is available when you run this statement in your application (the missing property name is: name)} {position: line: 1, column: 111, offset: 110} for query: "MATCH (m:METHOD)-[:CG_CALL]->(n:METHOD) WHERE m.name = $name AND NOT n.name STARTS WITH '<operator>' RETURN n.name as callee_name, n.filename as path, n.lineNumber as start, n.lineNumberEnd as end"
```

```
Neo.ClientNotification.Statement.UnknownPropertyKeyWarning — missing property: event_id
Neo.ClientNotification.Statement.UnknownLabelWarning — missing label: Event
```

**Cause:** Fresh/empty Neo4j database with no `Event` nodes or `event_id` properties yet.
Queries return empty results but do not fail. These warnings disappear once data is
populated.

### Micro-batch capacity overflow

**Error:**

```
RuntimeError: Values [29125 29067] is larger than capacity 10240
```

**Cause:** Rollout sequences (29125 and 29067 tokens) exceed `max_tokens_per_mb` in the
`MicroBatchSpec`. The FFD allocator cannot pack a single sequence that is larger than the
micro-batch capacity.

**Fix:** Increase `max_tokens_per_mb` in `opensage_grpo_mt.yaml` to at least match the
maximum possible sequence length (`prompt_len + max_new_tokens`). For agent tasks where
prompts can be ~27K tokens:

| `max_new_tokens` | Min `max_tokens_per_mb` | Recommended |
|---|---|---|
| 2048 | 32768 | 32768 |
| 4096 | 32768 | 65536 |
| 8192 | 65536 | 65536 |

### Training not learning (zero reward / zero gradient)

**Symptoms** (observed in debug_v10, Epoch 1 Step 3):

| Metric | Value | Issue |
|---|---|---|
| `task_reward` | 0.0 (all seqs) | No correct answers |
| `correct_n_seqs` | 0 / 2 | 0% accuracy |
| `no_eos_ratios` | 1.0 | All sequences hit max length without EOS |
| `advantages` | 0.0 | All-zero rewards → all-zero advantages after GRPO normalization |
| `actor_loss` / `grad_norm` | 0.0 | No parameter updates |
| `timeperf/rollout` | 241.6s | 99.7% of step time spent in rollout |
| `n_seqs` | 2 | Very small batch |
| `n_valid_tokens` | 248 | ~124 generated tokens per sequence |

**Root causes:**
1. **Generation length too short** — `max_new_tokens=2048` is the total token budget
   across ALL turns. Agent tasks require multi-turn tool use (bash, search, analyze),
   so 2048 tokens total is far too little. The model hits max length before completing
   the task → zero reward.
2. **Batch size too small** — with `n_samples=2` and all-zero rewards, GRPO advantage
   normalization always yields zero (zero variance → zero advantages).
3. **Rollout bottleneck** — 241s for 2 short sequences suggests the inference server is
   under-provisioned.

**Fix — recommended parameters for next training run:**

```bash
CUDA_VISIBLE_DEVICES=2,3,4,5 uv run examples/opensage/opensage_rl_mt.py \
    --config examples/opensage/opensage_grpo_mt.yaml \
    scheduler.type=local \
    trial_name=debug_v11 \
    allocation_mode=sglang:d1p1t2+d1p1t2 \
    cluster.n_nodes=1 \
    cluster.n_gpus_per_node=4 \
    gconfig.max_new_tokens=8192 \
    gconfig.max_tokens_per_mb=65536 \
    train_dataset.batch_size=2 \
    gconfig.n_samples=4 \
    rollout.max_concurrent_rollouts=4 \
    rollout.max_head_offpolicyness=0
```

Key changes vs debug_v10:
- `max_new_tokens`: 2048 → 8192 (4x more generation budget for multi-turn agent)
- `max_tokens_per_mb`: 32768 → 65536 (accommodate longer sequences)
- `n_samples`: 2 → 4 (more rollouts per prompt for GRPO variance)
- `batch_size`: 1 → 2 (more prompts per batch)
- `allocation_mode`: `d1p1t1+d1p1t1` → `d1p1t2+d1p1t2` (TP=2 for each model function)
- 4 GPUs instead of 2

If only 2 GPUs available, use a minimal improvement config:

```bash
CUDA_VISIBLE_DEVICES=2,3 uv run examples/opensage/opensage_rl_mt.py \
    --config examples/opensage/opensage_grpo_mt.yaml \
    scheduler.type=local \
    trial_name=debug_v11_2gpu \
    allocation_mode=sglang:d1p1t1+d1p1t1 \
    cluster.n_nodes=1 \
    cluster.n_gpus_per_node=2 \
    gconfig.max_new_tokens=4096 \
    gconfig.max_tokens_per_mb=65536 \
    train_dataset.batch_size=1 \
    gconfig.n_samples=4 \
    rollout.max_concurrent_rollouts=2 \
    rollout.max_head_offpolicyness=0
```

### JSON parsing of agent structured output (fixed)

**Symptom:** `VulFinding.model_validate_json(resp)` and `PoCFinding.model_validate_json(resp)`
fail because the raw agent response is not valid JSON.

**Root cause:** `run_agent_in_thread` only captured `part.text` from ADK events, ignoring
`part.function_call`. The tool call parsing chain actually works:

1. Qwen3-Instruct outputs `<tool_call>` tags (same format as Qwen 2.5)
2. ArealOpenAI (`tool_call_parser=qwen25`, configured in `opensage_grpo_mt.yaml`) uses
   sglang's `Qwen25Detector` to parse `<tool_call>` tags into structured tool calls
3. ArealLlm converts OpenAI tool_calls to ADK `Part.from_function_call()`
4. ADK processes `set_model_response` function call properly

But `run_agent_in_thread` missed step 4 because it only looked at text parts.

**Fix:** `run_agent_in_thread` now captures `part.function_call` events:
- Primary path: `part.function_call.name == "set_model_response"` → extract `args` as JSON
- Fallback: `_extract_json_from_response(resp)` text extraction (with warning log)
