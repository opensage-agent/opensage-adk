# Adding an evaluation benchmark

This guide walks through creating a new evaluation benchmark for OpenSage.

## 1. Create the evaluation module

Create a directory under `benchmarks/` with your benchmark name:

```
benchmarks/
└── my_benchmark/
    ├── __init__.py
    └── my_evaluation.py
```

## 2. Implement the evaluation class

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
    agent_dir: str = "examples/agents/my_agent"

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

### Required abstract methods

| Method | Purpose |
|--------|---------|
| `_get_sample_id(sample) -> str` | Extract unique task ID |
| `_get_user_msg_first(sample) -> str` | Extract initial prompt |

### Optional methods

| Method | Purpose |
|--------|---------|
| `_get_dataset()` | Load and filter dataset |
| `_create_task(sample)` | Create task instance |
| `_get_input_data_path(sample)` | Input data directory |
| `_get_cache_dir(sample)` | Cache directory |
| `_get_export_dir_in_sandbox(sample)` | Output dirs to export |
| `_prepare_general_env()` | Setup shared across all samples |
| `_before_initialize_hooks(session, task)` | Hooks before sandbox init |
| `customized_modify_and_save_results(...)` | Post-processing |
| `evaluate()` | Final evaluation and metrics |

## 3. Add a configuration template

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

## 5. Run the evaluation

**CLI (recommended):**

```bash
# Production run
python -m opensage.evaluations.my_benchmark.my_evaluation run \
  --dataset_path="org/dataset" \
  --agent_dir="examples/agents/my_agent" \
  --max_workers=6 \
  --output_dir="results/my_benchmark"

# Debug run (single-threaded)
python -m opensage.evaluations.my_benchmark.my_evaluation run_debug \
  --dataset_path="org/dataset" \
  --agent_dir="examples/agents/my_agent"
```

**Python API:**

```python
from opensage.evaluations import MyEvaluation

eval = MyEvaluation(
    dataset_path="org/dataset",
    agent_dir="examples/agents/my_agent",
    max_workers=6,
)
eval.run()       # production
eval.run_debug() # debugging
```

See [execution modes](index.md#execution-modes) for the full list of methods.

## Sample lifecycle

Each sample goes through six phases:

1. **Task creation** (`_create_task`) -- Convert dataset sample to `EvaluationTask`
2. **Environment preparation** (`_prepare_environment`) -- Create session, launch sandboxes, restore cache
3. **Agent preparation** (`_prepare_agent`) -- Load agent from `agent_dir`
4. **Agent execution** (`_run_agent`) -- Run with configured limits
5. **Output collection** (`_collect_outputs`) -- Export sandbox outputs, save traces and cost info
6. **Cleanup** -- Stop sandboxes, close session

For the full internal details, see [workflow details](workflow.md).

## Existing examples

| Example | Description |
|---------|-------------|
| `src/opensage/evaluations/cybergym/__init__.py` | Base evaluation class |
| `src/opensage/evaluations/cybergym/cybergym_static.py` | Full-featured evaluation |
| `src/opensage/evaluations/mock_debug/mock_debug_evaluation.py` | Minimal example |
| `src/opensage/evaluations/secodeplt/vul_detection.py` | Another example |

## See also

- [Development Guides](../Development-Guides.md)
- [Testing & Debugging](../Testing-Debugging.md)
