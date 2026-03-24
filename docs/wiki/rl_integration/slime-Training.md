# slime Training with OpenSage

## 1. Start slime Container

On the GPU machine:

```bash
git clone https://github.com/rucnyz/slime
cd slime
git checkout opensage
cd for_ssh

# Copy your SSH public key
cp ~/.ssh/id_ed25519.pub ./id_ed25519.pub

docker compose up --build          # default SSH port 2222
# or
SSH_PORT=2224 docker compose up --build
```

On your local machine, add to `~/.ssh/config`:

```
Host slime
    HostName 127.0.0.1
    Port 2222
    User root
    StrictHostKeyChecking no
    IdentityFile ~/.ssh/id_ed25519
    ProxyCommand ssh <gpu-machine> -W 127.0.0.1:2222
```

Then `ssh slime` to enter the container.

Base image: `slimerl/slime:latest` (includes Megatron-LM, sglang, ray, slime).

## 2. Install OpenSage Inside Container

```bash
# inside the container
cd /root
git clone https://github.com/OpenSage/OpenSage opensage
cd opensage
pip install -e .
```

## 3. Install CodeQL (Required for SeCodePLT)

The SeCodePLT benchmark uses CodeQL for call graph analysis. Download and install the CodeQL bundle inside the container:

```bash
cd /root/opensage/src/opensage/sandbox_scripts
wget https://github.com/github/codeql-action/releases/download/codeql-bundle-v2.18.4/codeql-bundle-linux64.tar.gz
tar -xzf codeql-bundle-linux64.tar.gz codeql
rm -f codeql-bundle-linux64.tar.gz
```

After this, `sandbox_scripts/` should contain: `callgraph/`, `codeql/`, `ossfuzz/`.

## 4. Install Docker Buildx (if missing)

The sandbox system builds Docker images for evaluation tasks. If `docker buildx` is not available:

```bash
mkdir -p ~/.docker/cli-plugins
curl -SL -o ~/.docker/cli-plugins/docker-buildx \
    'https://github.com/docker/buildx/releases/download/v0.31.1/buildx-v0.31.1.linux-amd64'
chmod +x ~/.docker/cli-plugins/docker-buildx
docker buildx version  # verify
```

## 5. Prepare Model Checkpoints

```bash
# inside the container
cd /root/slime
pip install -e .

# HF checkpoint
huggingface-cli download Qwen/Qwen3-4B-Instruct-2507 --local-dir /root/Qwen3-4B-Instruct-2507
# Convert to Megatron torch_dist format
source scripts/models/qwen3-4B-Instruct-2507.sh
PYTHONPATH=/root/Megatron-LM python tools/convert_hf_to_torch_dist.py \
    ${MODEL_ARGS[@]} \
    --hf-checkpoint /root/Qwen3-4B-Instruct-2507 \
    --save /root/Qwen3-4B-Instruct-2507_torch_dist
```

## 6. Generate Training Data

```bash
cd /root/slime/examples/opensage

# Mock benchmark (no Docker/sandbox dependencies, for testing)
python opensage_mock.py \
    --local_dir /root/opensage_data \
    --dataset_path /root/opensage/src/opensage/evaluations/mock_debug/mock_test_dataset.json \
    --output_filename mock_tasks.jsonl

# SeCodePLT (vulnerability detection benchmark)
python opensage_mock.py \
    --local_dir /root/opensage_data \
    --dataset_path opensage/secodeplt \
    --dataset_split train \
    --task_subset_file /root/opensage/src/opensage/evaluations/secodeplt/metadata/successful_task_list.txt \
    --output_filename secodeplt_tasks.jsonl
```

## 7. Run Training (Local Docker)

Use the OpenSage launcher script (`rl/slime/train.sh`), which handles Docker cleanup, environment setup, and delegates to the SLIME launch script:

```bash
# Mock benchmark (default):
bash /root/opensage/rl/slime/train.sh

# SeCodePLT benchmark:
bash /root/opensage/rl/slime/train.sh --benchmark secodeplt

# Debug mode (verbose logging, smaller batch):
bash /root/opensage/rl/slime/train.sh --debug

# With SLIME training config:
bash /root/opensage/rl/slime/train.sh \
    --benchmark secodeplt \
    --gpus 2,3 \
    --slime-config rl/slime/configs/secodeplt.yaml \
    --debug
```

Run `bash /root/opensage/rl/slime/train.sh --help` for all options.

<details>
<summary>Alternative: launch directly from SLIME side</summary>

```bash
cd /root/slime
bash examples/opensage/run_qwen3_4B.sh

# SeCodePLT:
OPENSAGE_AGENT_NAME=vul_agent_static_tools OPENSAGE_BENCHMARK_NAME=secodeplt \
    bash examples/opensage/run_qwen3_4B.sh
```

</details>

### Configuration

Environment variables (set before running, or use `train_slime.sh` flags):

| Variable                | Default            | Description                                          |
|-------------------------|--------------------|------------------------------------------------------|
| `CUDA_VISIBLE_DEVICES`  | ``                 | GPUs to use (`--gpus`)                               |
| `OPENSAGE_AGENT_NAME`     | `mock_rl_agent`    | Agent directory name (`--agent`)                     |
| `OPENSAGE_BENCHMARK_NAME` | `mock_debug`       | Benchmark name (`--benchmark`)                       |
| `OPENSAGE_MAX_CONCURRENT` | `4`                | Max concurrent evaluations (`--max-concurrent`)      |

### SLIME Training Hyperparameters

Use `--slime-config` to override SLIME `train.py` parameters without editing launch scripts. Config files live in `rl/slime/configs/`:

| Config | Use case |
|--------|----------|
| `rl/slime/configs/debug.yaml` | Small-scale testing (batch=2, lr=1e-6) |
| `rl/slime/configs/secodeplt.yaml` | SeCodePLT training (batch=8, lr=5e-7) |

YAML keys are `train.py` CLI arg names (without `--`). Boolean values with `true` become flags; `false` are skipped.

```yaml
# Example: rl/slime/configs/secodeplt.yaml
num-rollout: 20
rollout-batch-size: 4
global-batch-size: 8
lr: 5e-7
kl-loss-coef: 0.01
save-interval: 10
eval-interval: 10
```

The config values are appended as `EXTRA_TRAIN_ARGS` after the launch script's defaults, so they override any matching parameters.

### Monitor

```bash
# In another terminal
tail -f /root/opensage_train.log
```
## 8. Run Training (Remote Container)

TBD

## File Reference

| Side | File | Role |
|------|------|------|
| SLIME | `examples/opensage/generate_with_opensage.py` | Rollout entry point called by `train.py` |
| SLIME | `examples/opensage/opensage_mock.py` | Dataset → SLIME JSONL converter |
| SLIME | `examples/opensage/run_qwen3_4B{_debug}.sh` | Launch scripts (ray job submit) |
| OpenSage | `rl/slime/train.sh` | **Launcher** — env setup, Docker cleanup, delegates to SLIME |
| OpenSage | `rl/slime/configs/*.yaml` | Training hyperparameter configs (`--slime-config`) |
| OpenSage | `src/opensage/rl_integration/` | `SlimeLlm`, `SlimeAdapter`, `Client`, `BenchmarkInterface` |

For architecture and data flow details, see
[AReaL Training](AReaL-Training.md).

## Known Issues

- **ABORTED samples crash Megatron** ([slime #200](https://github.com/THUDM/slime/issues/200)): Samples with `response_length=0` cause `F.pad` to crash. Workaround: OpenSage uses `TRUNCATED` status and always includes prompt tokens so `total_length > 0`. See `SlimeAdapter._build_error_sample()`.
