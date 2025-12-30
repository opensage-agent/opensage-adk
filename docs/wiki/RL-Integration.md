# RL Framework Integration

AIgiSE provides seamless integration with RL frameworks (slime, verl, areal, etc.) for agent training and evaluation. This integration allows RL frameworks to use AIgiSE agents as rollout systems.

## Overview

The RL integration module (`aigise.rl_integration`) provides:

- **Client**: Manages agent configuration and model setup
- **RLSession**: Wraps AigiseSession with framework-specific generate methods
- **Adapters**: Framework-specific logic for sample handling (slime, verl, areal)

## Architecture

```
RL Framework (slime/verl/areal)
    ↓
AIgiSE Client
    ↓
RLSession (wraps AigiseSession)
    ↓
Framework Adapter (SlimeAdapter/VerlAdapter/ArealAdapter)
    ↓
Agent Execution
```

## Basic Usage

### Creating a Client

```python
import aigise

# Create client for agent and benchmark
client = aigise.create(
    agent_name="vul_agent",
    benchmark_name="secodeplt"
)
```

The `create()` function:
- Resolves the agent directory from `examples/agents/<agent_name>/`
- Loads the benchmark interface from `aigise/evaluations/<benchmark_name>/`
- Creates a `Client` instance with agent and benchmark configuration

### Using RLSession

```python
# Initialize session and generate samples
with client.init_session() as session:
    # For slime framework
    sample = await session.slime_generate(
        args=args,
        sample=sample,
        sampling_params=sampling_params
    )

    # For verl framework (when implemented)
    # sample = await session.verl_generate(args, sample, sampling_params)

    # For areal framework (when implemented)
    # sample = await session.areal_generate(args, sample, sampling_params)
```

### Framework-Specific Methods

Each framework has its own generate method:

- **`slime_generate()`**: For slime framework integration
- **`verl_generate()`**: For verl framework integration (planned)
- **`areal_generate()`**: For areal framework integration (planned)

## Client API

### `Client.__init__(agent_name, benchmark_name)`

Initializes the client with agent and benchmark configuration.

**Parameters:**
- `agent_name`: Name of the agent (must exist in `examples/agents/`)
- `benchmark_name`: Name of the benchmark (must exist in `aigise/evaluations/`)

**Raises:**
- `ValueError`: If agent or benchmark not found

### `Client.init_session()`

Creates and returns an `RLSession` context manager.

**Returns:**
- `RLSession`: Session context manager for agent execution

**Usage:**
```python
with client.init_session() as session:
    # Use session for agent execution
    pass
```

## RLSession API

### `RLSession.slime_generate(args, sample, sampling_params)`

Generates a sample using the slime framework adapter.

**Parameters:**
- `args`: Framework-specific arguments
- `sample`: Input sample to process
- `sampling_params`: Sampling parameters

**Returns:**
- Processed sample with agent output

### `RLSession.verl_generate(args, sample, sampling_params)`

Generates a sample using the verl framework adapter (planned).

### `RLSession.areal_generate(args, sample, sampling_params)`

Generates a sample using the areal framework adapter (planned).

## Adapters

Adapters handle framework-specific logic for sample processing:

- **`SlimeAdapter`**: Handles slime framework sample format and processing
- **`VerlAdapter`**: Handles verl framework sample format (planned)
- **`ArealAdapter`**: Handles areal framework sample format (planned)

Each adapter implements the `BaseAdapter` interface and provides:
- Sample format conversion
- Framework-specific parameter handling
- Result formatting

## Benchmark Interface

The benchmark interface (`BenchmarkInterface`) provides:

- Benchmark configuration loading
- Evaluation instance creation
- Task data management

Benchmarks are automatically loaded from `aigise/evaluations/<benchmark_name>/` and must implement the `BenchmarkInterface` protocol.

## Example: Complete Workflow

```python
import aigise

# 1. Create client
client = aigise.create("vul_agent", "secodeplt")

# 2. Initialize session
with client.init_session() as session:
    # 3. Generate samples for training
    for sample in training_samples:
        result = await session.slime_generate(
            args=training_args,
            sample=sample,
            sampling_params=sampling_params
        )
        # Process result...
```

## Session Lifecycle

1. **Session Creation**: `init_session()` creates a new AigiseSession
2. **Agent Loading**: Agent is loaded and configured
3. **Sandbox Initialization**: Required sandboxes are launched and initialized
4. **Sample Generation**: Framework-specific generate methods execute agents
5. **Session Cleanup**: Session and resources are cleaned up on exit

## Integration Points

The RL integration automatically handles:

- **Agent Configuration**: Loads agent from `examples/agents/`
- **Model Setup**: Configures LLM models from agent configuration
- **Session Management**: Creates and manages AigiseSession lifecycle
- **Sandbox Management**: Launches and initializes required sandboxes
- **Benchmark Integration**: Loads benchmark interface and evaluation instances
- **Framework Adapters**: Provides framework-specific sample handling

## See Also

- [Entry Points](Entry-Points.md) - Overview of all entry points
- [Core Concepts](Core-Concepts.md) - Understanding sessions and agents
- [Architecture](Architecture.md) - System architecture
