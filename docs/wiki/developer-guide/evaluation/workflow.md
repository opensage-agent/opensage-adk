# Evaluation Workflow Details

This page describes the internal step-by-step workflow when an evaluation runs.

## Overview

```
Script init -> Load dataset -> Prepare shared env -> Process samples (parallel) -> Aggregate -> Evaluate
```

Each sample follows its own lifecycle within the parallel execution:

```
Create task -> Create session -> Prepare environment -> Load agent -> Run agent -> Collect outputs -> Cleanup
```

---

## Step 1: Script Initialization

- Fire parses command-line arguments and creates an `Evaluation` instance
- Logging and instrumentation (Langfuse, OpenTelemetry) are configured

## Step 2: Load Dataset

```python
self.dataset = self._get_dataset()
```

Loads the benchmark dataset (HuggingFace or local). Each sample contains a task description, expected outputs (ground truth), and metadata.

## Step 3: Prepare Shared Environment

`_prepare_general_env()` sets up resources shared across all samples:

- Loads and expands the base TOML configuration
- Creates the output directory structure:
  `evals/{agent_id}/{benchmark_name}/{timestamp}/`

## Step 4: Parallel Sample Execution

Depending on the execution mode, samples are dispatched via:

**Multiprocessing** (`generate()`): `ProcessPoolExecutor`, true parallelism, process isolation. Requires serializable data.

```python
with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
    futures = {
        executor.submit(_run_sample_in_process, self, sample): sample
        for sample in self.dataset
    }
```

**Multithreading** (`generate_threaded()`): `ThreadPoolExecutor`, shared memory, GIL-limited. Better for I/O-bound operations.

```python
with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
    futures = {
        executor.submit(run_sample_in_thread, sample): sample
        for sample in self.dataset
    }
```

**Single thread** (`generate_single_thread()`): sequential execution, best for debugging. Much slower than the parallel modes.

## Step 5: Per-Sample Lifecycle

For each sample in the dataset:

### 5.1 Create Evaluation Task

```python
task = self._create_task(sample)
```

Produces an `EvaluationTask` with a unique `session_id`, the original `sample` data, and task metadata.

### 5.2 Create OpenSage-ADK Session

```python
session = opensage.get_session(
    session_id=task.session_id,
    config_path=self.config_path,
)
```

Each task gets an isolated session with its own configuration and managers.

### 5.3 Prepare Task Environment

Benchmark-specific setup (`_prepare_environment`). Typical steps:

- Extract code/data into the sandbox
- Initialize shared volumes and launch sandbox containers:
  ```python
  session.sandboxes.initialize_shared_volumes()
  await session.sandboxes.launch_all_sandboxes()
  await session.sandboxes.initialize_all_sandboxes()
  ```
- Set `session.config.src_dir_in_sandbox` to point tools at the source code
- Git repository setup (checkout main/master) if applicable

### 5.4 Load Agent

```python
mk_agent = self._load_mk_agent()
agent = mk_agent(session_id=task.session_id)
```

The agent is configured for this specific session with access to task-specific sandboxes and resources.

### 5.5 Create ADK Session and Runner

```python
inner_session_service = InMemorySessionService()
await inner_session_service.create_session(
    app_name=app_name,
    user_id=self.user_id + "_" + meta_data,
    session_id=task.session_id,
    state={"opensage_session_id": task.session_id},
)

runner = Runner(
    agent=agent,
    app_name=app_name,
    session_service=inner_session_service,
)
```

### 5.6 Run Agent

```python
run_config = RunConfig(max_llm_calls=self.max_llm_calls)

async for event in runner.run_async(
    user_id=user_id,
    session_id=task.session_id,
    run_config=run_config,
    new_message=types.Content(parts=[types.Part(text=task.prompt)]),
):
    ...
```

The agent enters a reason-act loop:

- **Runner starts agent execution**: sends the prompt and hands control to the agent.
- **Agent reasoning**: calls the LLM, decides which tools to use, generates function calls.
- **Tool execution**: the runner executes tools in the sandbox; tools access session resources; results return to the agent.
- **Iteration**: the agent processes tool results, picks the next action, and continues until completion or `max_llm_calls`.
- **Completion**: the agent generates a final response; the runner finishes; events collect for downstream analysis.

### 5.7 Collect and Save Results

```python
result = {
    "session_id": task.session_id,
    "prompt": task.prompt,
    "response": agent_response,
    "events": events,
    "metadata": {...},  # LLM calls, tools used, execution time, errors
}
self._save_result(task, result)
```

Results are saved as JSON to `evals/{agent_id}/{benchmark}/results/{task_id}.json`.

### 5.8 Cleanup

```python
opensage.cleanup_session(task.session_id)
```

Stops sandbox containers, removes shared volumes, and frees Docker resources.

## Step 6: Aggregation and Evaluation

After all samples complete:

### Aggregate

Collects per-task results and computes run-wide statistics:

- Success rate
- Average execution time
- Tool usage patterns
- Error rates

### Evaluate (`evaluate()`)

- **Load ground truth**: reads expected outputs from the dataset and the per-task agent results from disk.
- **Compare outputs**: compares each agent output to its ground truth and computes metrics (accuracy, precision/recall where applicable, plus benchmark-specific metrics).
- **Generate report**: writes the metrics, statistics, and example failures/successes to `evals/{agent_id}/{benchmark}/evaluation_report.json`.
- **Display summary**: prints metrics, top failures and successes, and a short analysis to the console.
