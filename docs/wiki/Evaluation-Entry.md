# Evaluations - Batch Processing Entry Point

Evaluation scripts run agents on benchmark datasets for performance measurement and testing.

## Command

```bash
cd src/aigise/evaluations
python cybergym/cybergym_vul_detection.py run \
  --agent-id my_agent \
  --config-path /path/to/config.toml \
  --max_llm_calls 75 \
  --use_multiprocessing \
  --max_workers 3
```

## Step-by-Step Workflow

### Step 1: Script Initialization

1. Fire library parses command-line arguments
2. Creates `Evaluation` class instance with parameters:
   - `agent_id`: Identifier for the agent
   - `config_path`: Path to TOML configuration
   - `max_llm_calls`: Maximum LLM calls per task
   - `use_multiprocessing`: Use processes vs threads
   - `max_workers`: Number of parallel workers
3. Sets up logging and instrumentation (Langfuse, OpenTelemetry)

### Step 2: Load Dataset

```python
self.dataset = self._get_dataset()
```

1. Loads benchmark dataset (e.g., HuggingFace datasets, JSON files)
2. Dataset contains multiple samples/tasks to evaluate
3. Example: CyberGym dataset has vulnerability detection tasks
4. Each sample contains:
   - Task description
   - Expected outputs (ground truth)
   - Metadata (file paths, vulnerability info, etc.)

### Step 3: Prepare General Environment (`_prepare_general_env`)

This sets up shared resources used across all evaluation tasks.

#### 3.1 Create Base Configuration

1. Loads base configuration from TOML file
2. Expands template variables
3. Stores in class for later use

#### 3.2 Setup Evaluation Directories

```python
self.eval_output_dir = Path(f"evals/{self.agent_id}/...")
self.eval_output_dir.mkdir(parents=True, exist_ok=True)
```

- Creates output directories for results
- Structure: `evals/{agent_id}/{benchmark_name}/{timestamp}/`
- Stores agent outputs, logs, artifacts

### Step 4: Generate Samples (Parallel Execution)

The evaluation runs tasks in parallel. Choose one mode:

#### Mode A: Multiprocessing (`generate()`)

```python
with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
    futures = {
        executor.submit(_run_sample_in_process, self, sample): sample
        for sample in self.dataset
    }
```

- Each sample runs in separate process
- True parallelism (bypasses Python GIL)
- Processes are isolated (no shared memory)
- Requires serializable data

#### Mode B: Multithreading (`generate_threaded()`)

```python
with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
    futures = {
        executor.submit(run_sample_in_thread, sample): sample
        for sample in self.dataset
    }
```

- Each sample runs in separate thread
- Shared memory (can share resources)
- Limited by GIL for CPU-bound tasks
- Better for I/O-bound operations

#### Mode C: Single Thread (`generate_single_thread()`)

- Sequential execution, one sample at a time
- Used for debugging
- Easier to debug issues
- Much slower

### Step 5: Process Each Sample (`_generate_sample` or `_run_sample_in_process`)

For each sample in the dataset:

#### 5.1 Create Evaluation Task

```python
task = self._create_task(sample)
```

1. Extracts sample data
2. Creates `EvaluationTask` object with:
   - `session_id`: Unique ID for this task
   - `sample`: Original sample data
   - `aigise_session`: Will be created next
   - Metadata (task name, description, etc.)

#### 5.2 Create SAGE-X Session

```python
aigise_session = get_aigise_session(
    aigise_session_id=task.session_id,
    config_path=self.config_path
)
```

- Creates isolated SAGE-X session for this task
- Loads configuration
- Each task gets its own session (isolation)

#### 5.3 Prepare Task-Specific Environment (`_prepare_environment`)

This is benchmark-specific. Example for CyberGym:

1. **Extract code/data**:
   - Extracts source code to sandbox
   - Copies test files, build scripts
   - Sets up project structure

2. **Initialize sandboxes**:
   ```python
   aigise_session.sandboxes.initialize_shared_volumes()
   await aigise_session.sandboxes.launch_all_sandboxes()
   await aigise_session.sandboxes.initialize_all_sandboxes()
   ```
   - Creates shared volumes
   - Launches required sandbox containers
   - Initializes sandboxes (tools, dependencies)

3. **Set source directory**:
   ```python
   aigise_session.config.src_dir_in_sandbox = "/shared/code"
   ```
   - Tells tools where to find source code

4. **Git repository setup** (if applicable):
   - Finds git repository in sandbox
   - Checks out main/master branch
   - Updates `src_dir_in_sandbox` to repo path

#### 5.4 Load Agent

```python
mk_agent = self._load_mk_agent()
agent = mk_agent(aigise_session_id=task.session_id)
```

1. Imports agent module
2. Calls `mk_agent()` function with session ID
3. Agent is configured for this specific session
4. Agent has access to task-specific sandboxes and resources

#### 5.5 Create ADK Session and Runner

```python
inner_session_service = InMemorySessionService()
await inner_session_service.create_session(
    app_name=app_name,
    user_id=self.user_id + "_" + meta_data,
    session_id=task.session_id,
    state={"aigise_session_id": task.session_id},
)

runner = Runner(
    agent=agent,
    app_name=app_name,
    session_service=inner_session_service,
)
```

- Creates ADK session that maps to SAGE-X session
- Stores `aigise_session_id` in session state
- Creates ADK Runner for agent execution

#### 5.6 Run Agent

```python
run_config = RunConfig(max_llm_calls=self.max_llm_calls)

async for event in runner.run_async(
    user_id=user_id,
    session_id=task.session_id,
    run_config=run_config,
    new_message=types.Content(parts=[types.Part(text=task.prompt)]),
):
    # Process events
    if isinstance(event, types.FunctionResponse):
        # Tool execution results
    elif isinstance(event, types.Candidate):
        # Agent responses
```

1. **Runner starts agent execution**:
   - Sends prompt to agent
   - Agent enters reason-act loop

2. **Agent reasoning**:
   - Calls LLM for reasoning
   - Decides which tools to use
   - Generates function calls

3. **Tool execution**:
   - Runner executes tools in sandbox
   - Tools access session resources
   - Results returned to agent

4. **Iteration**:
   - Agent processes tool results
   - Decides next action
   - Continues until completion or max calls

5. **Completion**:
   - Agent generates final response
   - Runner finishes execution
   - Events collected

#### 5.7 Collect Results

```python
result = {
    "session_id": task.session_id,
    "prompt": task.prompt,
    "response": agent_response,
    "events": events,
    "metadata": {...},
}
```

- Extracts agent response
- Collects execution metadata:
  - Number of LLM calls
  - Tools used
  - Execution time
  - Errors (if any)

#### 5.8 Save Results

```python
self._save_result(task, result)
```

- Saves result to file (JSON)
- Location: `evals/{agent_id}/{benchmark}/results/{task_id}.json`
- Includes full event history for analysis

#### 5.9 Cleanup Task Session

```python
cleanup_aigise_session(task.session_id)
```

- Stops sandbox containers
- Removes shared volumes
- Cleans up session resources
- Frees Docker resources

### Step 6: Collect All Results

After all samples complete:

1. Aggregates results from all tasks
2. Collects statistics:
   - Success rate
   - Average execution time
   - Tool usage patterns
   - Error rates

### Step 7: Evaluate Results (`evaluate()`)

```python
self.evaluate()
```

1. **Load ground truth**:
   - Loads expected outputs from dataset
   - Loads agent results from files

2. **Compare outputs**:
   - Compares agent output vs ground truth
   - Calculates metrics:
     - Accuracy
     - Precision/Recall (if applicable)
     - Custom benchmark metrics

3. **Generate report**:
   - Creates evaluation report
   - Includes metrics, statistics, examples
   - Saves to `evals/{agent_id}/{benchmark}/evaluation_report.json`

4. **Display summary**:
   - Prints metrics to console
   - Shows top failures/successes
   - Provides analysis

## Key Characteristics

### Isolation
- Each task gets its own SAGE-X session
- Separate sandbox containers
- No interference between tasks

### Parallelism
- Multiple tasks run simultaneously
- Configurable worker count
- Process or thread-based execution

### Reproducibility
- Deterministic task execution
- Results saved with full event history
- Can replay specific tasks

### Resource Management
- Sessions cleaned up after each task
- Containers stopped and removed
- Prevents resource leaks

## Comparison with aigise web

| Aspect | aigise web | Evaluations |
|--------|-----------|-------------|
| **Purpose** | Development, debugging | Performance measurement |
| **Sessions** | Single long-lived session | Multiple short-lived sessions |
| **Interaction** | Interactive chat | Batch processing |
| **Parallelism** | Single user | Multiple tasks in parallel |
| **Cleanup** | Manual (on exit) | Automatic (per task) |
| **Output** | Real-time events | Saved results files |

## Example Evaluation Flow

```
Dataset (100 tasks)
  ↓
Process Pool (3 workers)
  ├─ Worker 1: Task 1 → Session 1 → Agent → Result 1
  ├─ Worker 2: Task 2 → Session 2 → Agent → Result 2
  └─ Worker 3: Task 3 → Session 3 → Agent → Result 3
  ├─ Worker 1: Task 4 → Session 4 → Agent → Result 4
  ...
  ↓
All Results Collected
  ↓
Evaluation (compare vs ground truth)
  ↓
Report Generated
```

## Related Topics

- [Entry Points](Entry-Points.md) - Overview of entry points
- [Core Concepts](Core-Concepts.md) - Understanding sessions
- [Testing Debugging](Testing-Debugging.md) - Debugging evaluations
