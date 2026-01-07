# Adding a New Evaluation Benchmark

## Overview

Evaluations are used to benchmark agent performance on specific tasks. The evaluation system in AIgiSE is built on top of the base `Evaluation` class, which provides a complete framework for running benchmarks, managing sandboxes, collecting outputs, and generating metrics.

## Entry Points

The `Evaluation` class provides **multiple entry points** for running evaluations, each suited for different use cases:

### 1. `generate()` - Multiprocessing Mode (Default)
- Uses `ProcessPoolExecutor` for true parallelism
- Each sample runs in a separate process
- Best for production runs with maximum parallelism
- Bypasses Python's GIL for true concurrent execution

### 2. `generate_threaded()` - Multithreading Mode
- Uses `ThreadPoolExecutor` for parallel execution
- Each sample runs in a separate thread
- Useful when multiprocessing has serialization issues
- Shares memory across threads

### 3. `generate_single_thread()` - Single-Threaded Mode
- Sequential execution in a single thread
- Best for debugging and development
- Easiest to debug with step-by-step execution

### 4. `run()` - Auto-Select Mode
- Automatically selects execution mode based on `use_multiprocessing` flag
- If `use_multiprocessing=True`: calls `generate()` (multiprocessing)
- If `use_multiprocessing=False`: calls `generate_threaded()` (multithreading)
- Calls `evaluate()` after generation completes
- **Recommended for most use cases**

### 5. `run_debug()` - Debug Mode
- Calls `generate_single_thread()` followed by `evaluate()`
- Best for debugging and development
- Slower but easier to debug

### Usage Example

Evaluations use Python Fire for command-line interface. You can run evaluations in several ways:

**Using command-line (recommended):**

```bash
# Option 1: Auto-select mode (uses generate() or generate_threaded() based on use_multiprocessing)
python -m aigise.evaluations.my_benchmark.my_evaluation \
  --dataset_path="org/dataset" \
  --agent_dir="examples/agents/my_agent" \
  --max_workers=6 \
  --use_multiprocessing=true \
  run

# Option 2: Explicit multiprocessing mode
python -m aigise.evaluations.my_benchmark.my_evaluation \
  --dataset_path="org/dataset" \
  --agent_dir="examples/agents/my_agent" \
  generate

# Option 3: Multithreading mode
python -m aigise.evaluations.my_benchmark.my_evaluation \
  --dataset_path="org/dataset" \
  --agent_dir="examples/agents/my_agent" \
  generate_threaded

# Option 4: Single-threaded debugging mode
python -m aigise.evaluations.my_benchmark.my_evaluation \
  --dataset_path="org/dataset" \
  --agent_dir="examples/agents/my_agent" \
  run_debug

# Or using direct file path
python src/aigise/evaluations/my_benchmark/my_evaluation.py \
  --dataset_path="org/dataset" \
  --agent_dir="examples/agents/my_agent" \
  run
```

**Using Python API:**

```python
from aigise.evaluations import MyEvaluation

# Create evaluation instance
eval = MyEvaluation(
    dataset_path="org/dataset",
    agent_dir="examples/agents/my_agent",
    max_workers=6,
    use_multiprocessing=True,
)

# Option 1: Auto-select mode (recommended)
eval.run()

# Option 2: Explicit multiprocessing
eval.generate()

# Option 3: Multithreading
eval.generate_threaded()

# Option 4: Single-threaded debugging
eval.run_debug()
```

## Steps to Create a New Evaluation

### 1. Create Evaluation Module

Create a new directory under `src/aigise/evaluations/` with your benchmark name:

```
src/aigise/evaluations/
└── my_benchmark/
    ├── __init__.py
    └── my_evaluation.py
```

### 2. Implement Evaluation Class

Create a class that inherits from `Evaluation` and implements required abstract methods:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aigise.evaluations import Evaluation, EvaluationTask

@dataclass
class MyEvaluation(Evaluation):
    """Custom evaluation benchmark.

    This class is automatically registered by name (lowercase).
    You can retrieve it later using: get_evaluation_class("myevaluation")
    """

    # Required fields from parent class
    dataset_path: str = "org/dataset_name"  # HuggingFace dataset or local path
    agent_dir: str = "examples/agents/my_agent"  # Directory containing agent.py

    # Optional configuration overrides
    max_llm_calls: int = 100
    max_workers: int = 6
    use_multiprocessing: bool = True
    run_until_explicit_finish: bool = True
    use_cache: bool = True

    # Custom fields for your benchmark
    custom_param: str = "default_value"

    # Implement required abstract methods
    def _get_sample_id(self, sample: dict) -> str:
        """Extract unique task ID from sample.

        This ID is used for:
        - Output directory naming
        - Task identification in logs
        - Result tracking
        """
        return sample["task_id"]  # or sample.get("id"), etc.

    def _get_user_msg_first(self, sample: dict) -> str:
        """Extract the initial prompt/message to send to agent.

        This is the first message that will trigger agent execution.
        """
        return sample["prompt"]  # or sample.get("question"), etc.

    # Optional: Override methods for custom behavior
    def _get_dataset(self) -> datasets.Dataset:
        """Load dataset with custom filtering or preprocessing."""
        dataset = super()._get_dataset()
        # Add custom filtering logic if needed
        # dataset = dataset.filter(lambda x: x["difficulty"] == "hard")
        return dataset

    def _create_task(self, sample: dict) -> EvaluationTask:
        """Create custom task with additional fields if needed."""
        task = super()._create_task(sample)
        # Add custom fields to task if needed
        return task

    def _get_input_data_path(self, sample: dict) -> str:
        """Specify input data directory for this sample."""
        task_id = self._get_sample_id(sample)
        return str(Path(self.input_data_path) / task_id) if self.input_data_path else ""

    def _get_cache_dir(self, sample: dict) -> str:
        """Specify cache directory for sandbox state."""
        task_id = self._get_sample_id(sample)
        return str(Path(self.cache_dir) / task_id) if self.cache_dir else ""

    def _get_output_dir_in_sandbox(self, sample: dict) -> str | tuple | None:
        """Specify which sandbox directories to export after execution."""
        return "/output"  # or ("/output1", "/output2") for multiple dirs

    def customized_modify_and_save_results(
        self,
        *,
        results: list | None,
        failed_samples: list[str] | None,
        mode: str,
    ) -> None:
        """Post-process and save aggregated results after all samples complete.

        Args:
            results: List of successful sample outputs
            failed_samples: List of task IDs that failed
            mode: Execution mode ("multiprocess", "threaded", or "single_thread")
        """
        # Calculate metrics, save summary, etc.
        pass

    def evaluate(self) -> None:
        """Analyze collected results and generate final metrics.

        This is called after generate() completes. Implement your
        evaluation logic here (accuracy, pass rate, etc.).
        """
        # Load results from output_dir
        # Calculate metrics
        # Save evaluation report
        pass
```

### 3. Configuration Template

Create a configuration template in `src/aigise/evaluations/configs/`:

```toml
# src/aigise/evaluations/configs/my_benchmark_config.toml
[llm]
model_name = "gemini-2.0-flash-exp"
temperature = 0.7

[sandbox]
[sandbox.main]
type = "docker"
image = "python:3.11"
working_dir = "/workspace"

# Template variables can be used:
# ${TASK_NAME} - Replaced with actual task ID
# ${PROJECT_RELATIVE_SHARED_DATA_PATH} - Replaced with data path
```

### 4. Registration

The evaluation class is **automatically registered** when imported. The registration name is the lowercase class name.

Example:
- Class name: `MyEvaluation` → Registered as: `"myevaluation"`
- Retrieve with: `get_evaluation_class("myevaluation")`

### 5. Running the Evaluation

Since evaluations use Python Fire, you can run them from command-line:

```bash
# Run with auto-select mode (recommended)
python -m aigise.evaluations.my_benchmark.my_evaluation \
  --dataset_path="org/dataset" \
  --agent_dir="examples/agents/my_agent" \
  --max_workers=6 \
  --output_dir="results/my_benchmark" \
  run

# Or for debugging (single-threaded)
python -m aigise.evaluations.my_benchmark.my_evaluation \
  --dataset_path="org/dataset" \
  --agent_dir="examples/agents/my_agent" \
  run_debug

# Or directly specify execution method
python -m aigise.evaluations.my_benchmark.my_evaluation \
  --dataset_path="org/dataset" \
  --agent_dir="examples/agents/my_agent" \
  generate  # or generate_threaded, generate_single_thread
```

Or programmatically:

```python
from aigise.evaluations import MyEvaluation

# Create and run
eval = MyEvaluation(
    dataset_path="org/dataset",
    agent_dir="examples/agents/my_agent",
    output_dir="results/my_benchmark",  # Optional, auto-generated if not provided
    max_workers=6,
)

# Run evaluation
eval.run()  # or eval.run_debug() for debugging
```

## Evaluation Lifecycle

Each evaluation sample goes through the following lifecycle:

1. **Task Creation** (`_create_task()`)
   - Convert dataset sample to `EvaluationTask`
   - Extract task ID, prompt, paths, etc.

2. **Environment Preparation** (`_prepare_environment()`)
   - Initialize AIgiSE session
   - Load/launch sandboxes
   - Set up Neo4j (if enabled)
   - Load cached sandbox states (if `use_cache=True`)

3. **Agent Preparation** (`_prepare_agent()`)
   - Load `mk_agent` function from `agent_dir`
   - Create agent instance
   - Configure model (if `use_config_model=True`)

4. **Agent Execution** (`_run_agent()`)
   - Send prompt to agent
   - Run agent with configured limits
   - Track LLM calls, costs, etc.
   - Handle `run_until_explicit_finish` loop

5. **Output Collection** (`_collect_outputs()`)
   - Export sandbox outputs (if `output_dir_in_sandbox` specified)
   - Export Neo4j database
   - Save session trace
   - Calculate cost information

6. **Cleanup**
   - Clean up sandboxes
   - Close sessions
   - Save error information (if failed)

## Key Methods to Override

### Required Abstract Methods

- `_get_sample_id(sample: dict) -> str`: Extract unique task ID
- `_get_user_msg_first(sample: dict) -> str`: Extract initial prompt

### Optional Methods (with Defaults)

- `_get_dataset() -> datasets.Dataset`: Load and filter dataset
- `_create_task(sample: dict) -> EvaluationTask`: Create task instance
- `_get_input_data_path(sample: dict) -> str`: Input data directory
- `_get_cache_dir(sample: dict) -> str`: Cache directory
- `_get_output_dir_in_sandbox(sample: dict) -> str | tuple | None`: Output dirs to export
- `_prepare_general_env() -> None`: Setup shared across all samples
- `_before_initialize_hooks(aigise_session, task) -> None`: Hooks before sandbox init
- `customized_modify_and_save_results(results, failed_samples, mode) -> None`: Post-processing
- `evaluate() -> None`: Final evaluation and metrics

## Output Structure

Each evaluation run creates an output directory with the following structure:

```
evals/
└── myevaluation/
    └── yymmdd_HHMMSS/
        ├── evaluation_master.log       # Master log for entire run
        ├── eval_params.json            # Evaluation parameters
        ├── task_001/
        │   ├── execution_debug.log     # DEBUG-level log
        │   ├── execution_info.log      # INFO-level log
        │   ├── config_used.toml        # Config used for this task
        │   ├── cost_info.json          # Token usage and costs
        │   ├── session_trace.json      # Complete session events
        │   ├── session_trace.txt       # Human-readable trace
        │   ├── metadata.json           # Task metadata
        │   ├── sandbox_output/         # Exported from sandbox
        │   └── neo4j_history/          # Neo4j database export
        └── task_002/
            └── ...
```

## Configuration Options

Key configuration options available in `Evaluation`:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `dataset_path` | str | Required | HuggingFace dataset or local path |
| `agent_dir` | str | Required | Directory with `agent.py` |
| `max_llm_calls` | int | 100 | Maximum LLM calls per task |
| `max_workers` | int | 6 | Parallel workers |
| `use_multiprocessing` | bool | True | Use multiprocessing vs threading |
| `use_cache` | bool | True | Load/cache sandbox states |
| `run_until_explicit_finish` | bool | True | Keep running until task finished |
| `use_config_model` | bool | False | Use model from config file |
| `llm_retry_count` | int | 3 | Retries for LLM API calls |
| `llm_retry_timeout` | int | 30 | Timeout per LLM request (seconds) |
| `log_level` | str | "INFO" | Terminal log level |

## Examples

See existing evaluations for reference:

- `src/aigise/evaluations/cybergym/__init__.py` - Base class of evaluation
- `src/aigise/evaluations/cybergym/cybergym_static.py` - Full-featured evaluation
- `src/aigise/evaluations/mock_debug/mock_debug_evaluation.py` - Minimal example
- `src/aigise/evaluations/secodeplt/vul_detection.py` - Another example

## See Also

- [Development Guides](Development-Guides.md) - Other development guides
- [Testing Debugging](Testing-Debugging.md) - Testing evaluations
- `src/aigise/evaluations/__init__.py` - Base Evaluation class implementation
