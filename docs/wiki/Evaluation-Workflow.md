# Evaluations - workflow details

This page contains the detailed workflow for the evaluations (batch processing)
entry point.

## Command

```bash
cd src/opensage/evaluations
python cybergym/cybergym_vul_detection.py run \
  --agent-id my_agent \
  --config-path /path/to/config.toml \
  --max_llm_calls 75 \
  --use_multiprocessing \
  --max_workers 3
```

## Step-by-Step Workflow

### Step 1: Script Initialization

- Fire library parses command-line arguments
- Creates an `Evaluation` class instance with parameters:
  - `agent_id`: Identifier for the agent
  - `config_path`: Path to TOML configuration
  - `max_llm_calls`: Maximum LLM calls per task
  - `use_multiprocessing`: Use processes vs threads
  - `max_workers`: Number of parallel workers
- Sets up logging and instrumentation (Langfuse, OpenTelemetry)

### Step 2: Load Dataset

```python
self.dataset = self._get_dataset()
```

- Loads benchmark dataset (e.g., HuggingFace datasets, JSON files)
- Dataset contains multiple samples/tasks to evaluate
- Example: CyberGym dataset has vulnerability detection tasks
- Each sample contains:
  - Task description
  - Expected outputs (ground truth)
  - Metadata (file paths, vulnerability info, etc.)

### Step 3: Prepare General Environment (`_prepare_general_env`)

This sets up shared resources used across all evaluation tasks.

#### 3.1 Create Base Configuration

- Loads base configuration from TOML file
- Expands template variables
- Stores in class for later use

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

- Extracts sample data
- Creates an `EvaluationTask` object with:
  - `session_id`: Unique ID for this task
  - `sample`: Original sample data
  - `session`: Will be created next
  - Metadata (task name, description, etc.)

#### 5.2 Create OpenSage Session

```python
import opensage

session = opensage.get_session(
    session_id=task.session_id,
    config_path=self.config_path,
)
```

- Creates isolated OpenSage session for this task
- Loads configuration
- Each task gets its own session (isolation)

#### 5.3 Prepare Task-Specific Environment (`_prepare_environment`)

This is benchmark-specific. Example for CyberGym:

- **Extract code/data**
    - Extracts source code to sandbox
    - Copies test files, build scripts
    - Sets up project structure

- **Initialize sandboxes**
    ```python
    session.sandboxes.initialize_shared_volumes()
    await session.sandboxes.launch_all_sandboxes()
    await session.sandboxes.initialize_all_sandboxes()
    ```
    - Creates shared volumes
    - Launches required sandbox containers
    - Initializes sandboxes (tools, dependencies)

- **Set source directory**
    ```python
    session.config.src_dir_in_sandbox = "/shared/code"
    ```
    - Tells tools where to find source code

- **Git repository setup** (if applicable)
    - Finds git repository in sandbox
    - Checks out main/master branch
    - Updates `src_dir_in_sandbox` to repo path

#### 5.4 Load Agent

```python
mk_agent = self._load_mk_agent()
agent = mk_agent(session_id=task.session_id)
```

- Imports agent module
- Calls `mk_agent()` function with session ID
- Agent is configured for this specific session
- Agent has access to task-specific sandboxes and resources

#### 5.5 Create ADK Session and Runner

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

- Creates ADK session that maps to OpenSage session
- Stores `opensage_session_id` in session state
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
    ...
```

- **Runner starts agent execution**
    - Sends prompt to agent
    - Agent enters reason-act loop

- **Agent reasoning**
    - Calls LLM for reasoning
    - Decides which tools to use
    - Generates function calls

- **Tool execution**
    - Runner executes tools in sandbox
    - Tools access session resources
    - Results returned to agent

- **Iteration**
    - Agent processes tool results
    - Decides next action
    - Continues until completion or max calls

- **Completion**
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
opensage.cleanup_session(task.session_id)
```

- Stops sandbox containers
- Removes shared volumes
- Cleans up session resources
- Frees Docker resources

### Step 6: Collect All Results

After all samples complete:

- Aggregates results from all tasks
- Collects statistics:
    - Success rate
    - Average execution time
    - Tool usage patterns
    - Error rates

### Step 7: Evaluate Results (`evaluate()`)

```python
self.evaluate()
```

- **Load ground truth**
    - Loads expected outputs from dataset
    - Loads agent results from files

- **Compare outputs**
    - Compares agent output vs ground truth
    - Calculates metrics:
        - Accuracy
        - Precision/Recall (if applicable)
        - Custom benchmark metrics

- **Generate report**
    - Creates evaluation report
    - Includes metrics, statistics, examples
    - Saves to `evals/{agent_id}/{benchmark}/evaluation_report.json`

- **Display summary**
    - Prints metrics to console
    - Shows top failures/successes
    - Provides analysis
