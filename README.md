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
cd src/aigise/evaluations
python cybergym_static.py --agent_id=<your_agent_id> run
```

#### Run evaluation (with dynamic tools)

```shell
cd src/aigise/evaluations
python cybergym_dynamic.py --agent_id=<your_agent_id> run
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
