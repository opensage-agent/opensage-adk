# AIgiSE

## Setup

### Python Environment

We use [uv](https://docs.astral.sh/uv) to manage Python dependencies:
- [Installing uv](https://docs.astral.sh/uv/getting-started/installation/#installing-uv)

```bash
# at the root of the repo
uv python install
uv sync

# install pre-commit hook (required for committing to this repo)
uv run pre-commit install
```

NOTE:
- `uv` installs dependencies into `.venv` which is unknown for your shell by default. Therefore, you should use `uv run <command>` to run all commands using the dependencies. Check [`uv`'s documentation](https://docs.astral.sh/uv/concepts/projects/run) for details.
- Since we use `uv` for dependency management, you should avoid using `pip` to change dependencies. Instead, always use `uv add` or `uv remove`. Check [`uv`'s documentation](https://docs.astral.sh/uv/concepts/projects/dependencies) for details.

## Sandboxes

In order to use the joern and codeql sandbox, you need to download codeql here https://github.com/github/codeql-action/releases/download/codeql-bundle-v2.18.4/codeql-bundle-linux64.tar.gz, decompress it and copy the codeql folder to PROJECT_PATH/src/aigise/sandbox_scripts

## Evaluation

The evaluation script of each benchmark has the following sub-commands:

- `generate`: Run the agent on the benchmark (multi-threaded)
- `generate_single_thread`: Run the agent on the benchmark (single-threaded for debugging)
- `evaluate`: Run the benchmark evaluation against the agent outputs
- `run`: Runs `generate` then `evaluate`
- `run_debug`: Runs `generate_single_thread` then `evaluate`

### PatchAgent

```shell
cd src/aigise/evaluations
python patchagent.py run
```

### CyberGym

Install cybergym in third_party/cybergym following its own README.

```shell
cd third_party/cybergym
pip3 install -e '.[dev,server]'
git lfs install
git clone https://huggingface.co/datasets/sunblaze-ucb/cybergym cybergym_data
python scripts/server_data/download.py --tasks-file ./cybergym_data/tasks.json
bash scripts/server_data/download_chunks.sh
7z x cybergym-oss-fuzz-data.7z
```

Start the PoC submission server

```shell
PORT=8666 # port of the server
POC_SAVE_DIR=./server_poc # dir to save the pocs
CYBERGYM_SERVER_DATA_DIR=./oss-fuzz-data
python3 -m cybergym.server \
    --host 0.0.0.0 --port $PORT \
    --log_dir $POC_SAVE_DIR --db_path $POC_SAVE_DIR/poc.db \
    --cybergym_oss_fuzz_path $CYBERGYM_SERVER_DATA_DIR
```

#### Run evaluation (with only static tools)

```shell
python -m evaluations.cybergym.cybergym_static --agent_id=<your_agent_id> run
```

run for the zero-day vulnerability detection task
```shell
python -m evaluations.cybergym.cybergym_vul_detection run --agent-id aaa --max_llm_calls 75 --checkout_main_branch --log_level INFO --model_name="openrouter/openai/gpt-5" --start_idx 0 --end_idx 50 --use_multiprocessing --max_workers 3

python -m evaluations.cybergym.cybergym_vul_detection run --agent-id aaa --max_llm_calls 75 --checkout_main_branch --log_level INFO --model_name="gemini-3-pro-preview" --use_multiprocessing --start_idx 50 --end_idx 100 --max_workers 3
```

#### Run evaluation (with dynamic tools)

```shell
python -m evaluations.cybergym.cybergym_dynamic --agent_id=<your_agent_id> run
```

#### Evaluate results

After running the agent, you can evaluate the results:

```shell
cd src/aigise/evaluations
python cybergym_static.py --agent_id=<your_agent_id> evaluate
# or
python cybergym_dynamic.py --agent_id=<your_agent_id> evaluate
```

## Development Notes

Use git subtree to add third_party dependencies that we want to edit. Example:

```bash
git subtree add --prefix third_party/cybergym https://github.com/sunblaze-ucb/cybergym.git main --squash
```

## Debug

Run a single-agent web UI (Dev UI) backed by AIgiSE services for debugging:

```bash
# from the repo root
uv run aigise web \
  --config /abs/path/to/your_config.toml \
  --agent  /abs/path/to/agents/<your_agent_dir> \
  --port   <your_preferred_port> \
  --neo4j_logging   # optional, enable Neo4j event logging
```
