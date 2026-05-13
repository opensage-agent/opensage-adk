# Adding an evaluation benchmark

This guide walks through creating a new evaluation benchmark for OpenSage-ADK.

## 1. Create the Evaluation Module

Create a directory under `benchmarks/` with your benchmark name:

```
benchmarks/
└── my_benchmark/
    ├── __init__.py
    └── my_evaluation.py
```

## 2. Implement the Evaluation Class

Subclass `Evaluation` and implement the two required abstract methods:

```python
from __future__ import annotations

from dataclasses import dataclass

from opensage.evaluation.base import Evaluation, EvaluationTask


@dataclass(kw_only=True)
class MyEvaluation(Evaluation):
    """Custom evaluation benchmark."""

    # Required fields
    dataset_path: str = "org/dataset_name"
    agent_dir: str = "agent_library/agents/my_agent"

    # Optional overrides
    max_llm_calls: int = 100
    max_workers: int = 6
    use_multiprocessing: bool = True
    run_until_explicit_finish: bool = True
    use_sandbox_cache: bool = True

    # Custom fields
    custom_param: str = "default_value"

    # --- Required abstract methods ---

    def _get_task_id(self, sample: dict) -> str:
        """Extract unique task ID from a dataset sample."""
        return sample["task_id"]

    def _get_first_user_message(self, sample: dict) -> str:
        """Extract the initial prompt to send to the agent."""
        return sample["prompt"]

    # --- Optional overrides ---

    def _get_dataset(self) -> datasets.Dataset:
        """Custom dataset loading or filtering."""
        dataset = super()._get_dataset()
        # dataset = dataset.filter(lambda x: x["difficulty"] == "hard")
        return dataset

    def _create_task(self, sample: dict) -> EvaluationTask:
        """Attach additional fields to the task if needed."""
        task = super()._create_task(sample)
        return task

    def _get_export_dir_in_sandbox(self, sample: dict) -> str | tuple | None:
        """Sandbox directories to export after execution."""
        return "/output"  # or ("/output1", "/output2")

    def customized_modify_and_save_results(
        self,
        *,
        results: list | None,
        failed_samples: list[str] | None,
        mode: str,
    ) -> None:
        """Post-process and save aggregated results."""
        pass

    def evaluate(self) -> None:
        """Calculate final metrics after all samples complete."""
        pass
```

### Required Abstract Methods

| Method | Purpose |
|--------|---------|
| `_get_task_id(sample) -> str` | Extract unique task ID |
| `_get_first_user_message(sample) -> str` | Extract initial prompt |

### Optional Methods

| Method | Purpose |
|--------|---------|
| `_get_dataset()` | Load and filter dataset |
| `_create_task(sample)` | Create task instance |
| `_get_input_data_path(sample)` | Input data directory |
| `_get_cache_dir(sample)` | Cache directory |
| `_get_export_dir_in_sandbox(sample)` | Output dirs to export |
| `_get_fake_user_fn(opensage_session)` | Return a fake-user callback for multi-turn interaction (see below) |
| `_prepare_general_env()` | Setup shared across all samples |
| `_before_initialize_hooks(session, task)` | Hooks before sandbox init |
| `customized_modify_and_save_results(...)` | Post-processing |
| `evaluate()` | Final evaluation and metrics |

### Multi-Turn Interaction (Fake User)

By default each benchmark sample runs a single invocation. To drive multi-turn conversations — where the agent receives follow-up messages after each invocation — configure a **fake-user callback**.

A fake-user function has the signature `async (Session) -> str | None`. After each invocation completes, the function receives the refreshed Session and returns either a follow-up message (`str`) or `None` to stop.

Three ways to provide one, checked in priority order:

1. **`fake_user_fn` field** — set directly on the `Evaluation` subclass (programmatic):
    ```python
    @dataclass(kw_only=True)
    class MyEvaluation(Evaluation):
        async def my_fake_user(self, session):
            if session.state.get("task_finished"):
                return None
            return "Continue."

        def _get_fake_user_fn(self, opensage_session=None):
            return self.my_fake_user
    ```

2. **`[fake_user]` config section** — point at a Python file in `config.toml`:
    ```toml
    [fake_user]
    python_file = "my_fake_user.py"
    ```
    See the [`[fake_user]` reference](../../reference/configuration/fake-user.md) for details.

3. **`run_until_explicit_finish`** — legacy field. Uses the built-in `default_fake_user` which sends a continuation prompt until `session.state["task_finished"]` is `True`. The agent must have the `finish_task` tool.
    ```python
    run_until_explicit_finish: bool = True
    continuation_prompt: str | None = "Keep going."
    ```

## 3. Add a Configuration Template

Create a TOML config next to your agent:

```toml
[llm]
model_name = "gemini-2.0-flash-exp"
temperature = 0.7

[sandbox]
[sandbox.main]
type = "docker"
image = "python:3.12"
working_dir = "/workspace"

# Template variables:
# ${TASK_NAME} - Replaced with actual task ID
# ${ABSOLUTE_SHARED_DATA_PATH} - Replaced with absolute input data dir
```

## 4. Registration

The evaluation class is **automatically registered** when imported. The
registered name is the lowercase class name:

- `MyEvaluation` is registered as `"myevaluation"`
- Retrieve with `get_evaluation_class("myevaluation")`

## 5. Run the Evaluation

**CLI (recommended):**

```bash
# Production run
python -m opensage.evaluations.my_benchmark.my_evaluation run \
  --dataset_path="org/dataset" \
  --agent_dir="agent_library/agents/my_agent" \
  --max_workers=6 \
  --output_dir="results/my_benchmark"

# Debug run (single-threaded)
python -m opensage.evaluations.my_benchmark.my_evaluation run_debug \
  --dataset_path="org/dataset" \
  --agent_dir="agent_library/agents/my_agent"
```

**Python API:**

```python
from opensage.evaluations import MyEvaluation

eval = MyEvaluation(
    dataset_path="org/dataset",
    agent_dir="agent_library/agents/my_agent",
    max_workers=6,
)
eval.run()       # production
eval.run_debug() # debugging
```

See [execution modes](index.md#execution-modes) for the full list of methods.

## Sample Lifecycle

Each sample goes through six phases:

1. **Task creation** (`_create_task`) -- Convert dataset sample to `EvaluationTask`
2. **Environment preparation** (`_prepare_environment`) -- Create session, launch sandboxes, restore cache
3. **Agent preparation** (`_prepare_agent`) -- Load agent from `agent_dir`
4. **Agent execution** (`_run_agent`) -- Run with configured limits
5. **Output collection** (`_collect_outputs`) -- Export sandbox outputs, save traces and cost info
6. **Cleanup** -- Stop sandboxes, close session

For the full internal details, see [workflow details](workflow.md).

## Existing Examples

| Example | Description |
|---------|-------------|
| `src/opensage/evaluation/base.py` | Base `Evaluation` / `EvaluationTask` implementation |
| `benchmarks/cybergym/cybergym_static.py` | Static CyberGym evaluation |
| `benchmarks/cybergym/cybergym_dynamic.py` | Dynamic CyberGym evaluation |
| `benchmarks/cybergym/cybergym_vul_detection.py` | Vulnerability-detection CyberGym evaluation |
| `benchmarks/swe_bench_pro/swe_bench_pro.py` | SWE-Bench Pro benchmark entry point |
