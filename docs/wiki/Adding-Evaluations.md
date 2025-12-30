# Adding a New Evaluation Benchmark

## Overview

Evaluations are used to benchmark agent performance on specific tasks.

## Steps

1. Create evaluation module in `src/aigise/evaluations/`
2. Implement evaluation interface
3. Add configuration template
4. Add sample data handling

## Evaluation Structure

```python
# src/aigise/evaluations/my_benchmark/my_evaluation.py
from aigise.evaluations import EvaluationTask

class MyEvaluation:
    async def run_evaluation(self, tasks):
        # Evaluation logic
        pass
```

## Configuration

Create config template in `src/aigise/evaluations/configs/`:

```toml
# my_benchmark_config.toml
task_name = "my_benchmark"
# ... evaluation-specific config
```

## Data Handling

- Load benchmark data
- Run agents on tasks
- Collect results
- Generate metrics

## See Also

- [Development Guides](Development-Guides.md) - Other development guides
- [Testing Debugging](Testing-Debugging.md) - Testing evaluations
