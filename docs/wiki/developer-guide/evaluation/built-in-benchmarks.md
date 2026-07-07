# Built-in Benchmarks

Concrete CLI invocations for the benchmarks that ship with OpenSage-ADK. For the general evaluation model (subcommands, parallelism, output layout), see [Evaluations](index.md).

## CyberGym

CyberGym runs against `third_party/cybergym`. Install it following the upstream README:

```bash
cd third_party/cybergym
pip3 install -e '.[dev,server]'
git lfs install
git clone https://huggingface.co/datasets/sunblaze-ucb/cybergym cybergym_data
python scripts/server_data/download.py --tasks-file ./cybergym_data/tasks.json
bash scripts/server_data/download_chunks.sh
7z x cybergym-oss-fuzz-data.7z
```

### PoC Submission Server

PoC tasks submit candidates to a local server. Start it before running the evaluation:

```bash
PORT=8666
POC_SAVE_DIR=./server_poc
CYBERGYM_SERVER_DATA_DIR=./oss-fuzz-data
python3 -m cybergym.server \
    --host 0.0.0.0 --port $PORT \
    --log_dir $POC_SAVE_DIR --db_path $POC_SAVE_DIR/poc.db \
    --cybergym_oss_fuzz_path $CYBERGYM_SERVER_DATA_DIR
```

### Run Evaluations

```bash
# Static-tools variant
python -m benchmarks.cybergym.cybergym_static --agent_id=<your_agent_id> run

# Dynamic-tools variant
python -m benchmarks.cybergym.cybergym_dynamic --agent_id=<your_agent_id> run

# Vulnerability detection (multi-process)
python -m benchmarks.cybergym.cybergym_vul_detection run \
    --agent-id <id> --max_llm_calls 75 --checkout_main_branch \
    --log_level INFO --model_name="gemini-3-pro-preview" \
    --start_idx 0 --end_idx 50 --use_multiprocessing --max_workers 3

# Evaluate previously-generated outputs
python -m benchmarks.cybergym.cybergym_static --agent_id=<your_agent_id> evaluate
python -m benchmarks.cybergym.cybergym_dynamic --agent_id=<your_agent_id> evaluate
```

## SWE-Bench Pro

```bash
# Basic run
python -m benchmarks.swe_bench_pro.swe_bench_pro run \
    --model_name="gemini-3-flash-preview" \
    --start_idx 0 --end_idx 10 \
    --max_workers 3

# With the explore agent (pre-exploration pass before the main agent)
python -m benchmarks.swe_bench_pro.swe_bench_pro run \
    --model_name="gemini-3-flash-preview" \
    --use_explore_agent --explore_max_llm_calls 40 \
    --start_idx 0 --end_idx 10 \
    --max_workers 3

# Skip already-solved tasks on re-runs
python -m benchmarks.swe_bench_pro.swe_bench_pro run \
    --model_name="gemini-3-flash-preview" \
    --skip_existing --skip_successful \
    --max_workers 3

# Run specific tasks from a file (one task ID per line)
python -m benchmarks.swe_bench_pro.swe_bench_pro run \
    --model_name="gemini-3-flash-preview" \
    --task_file ./tasks_to_run.txt \
    --max_workers 3

# Evaluate results
python -m benchmarks.swe_bench_pro.swe_bench_pro evaluate
```

## SeCodePLT

```bash
python -m benchmarks.secodeplt.vul_detection run \
    --agent-id <id> --max_llm_calls 75 --log_level INFO \
    --start_idx 1 --end_idx 2 --model_name="gemini-3-pro-preview" \
    --output_dir ./evals/secodeplt/test --skip_poc --max_workers 1
```

With memory enabled (single-thread debug run):

```bash
python -m benchmarks.secodeplt.vul_detection_memory run_debug \
    --agent-id <id> --max_llm_calls 75 --log_level INFO \
    --start_idx 1 --end_idx 2 --model_name="gemini-3-pro-preview" \
    --output_dir ./evals/secodeplt/test_memory --skip_poc --max_workers 1
```

## See Also

- [Evaluations](index.md): shared subcommands, parallelism modes, and output layout.
- [Workflow Details](workflow.md): what happens inside a single evaluation run.
- [Adding a Benchmark](adding-evaluations.md): building a new benchmark on the `Evaluation` base class.
