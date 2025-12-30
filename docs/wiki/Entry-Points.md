# Entry Points

SAGE-X has three main entry points for different use cases:

1. **[Aigise Web Entry](Aigise-Web-Entry.md)** - Interactive web UI for development and debugging
2. **[Evaluation Entry](Evaluation-Entry.md)** - Batch evaluation on benchmarks
3. **[RL Framework Integration](RL-Integration.md)** - Integration with RL frameworks (slime, verl, areal) for agent training

Each entry point has a different workflow and use case. Click on the links above to see detailed step-by-step workflows.

## Quick Comparison

| Aspect | aigise web | Evaluations | RL Integration |
|--------|-----------|-------------|----------------|
| **Use Case** | Development, debugging | Performance measurement | Agent training |
| **Interaction** | Interactive chat | Batch processing | Framework API |
| **Sessions** | Single long-lived | Multiple short-lived | Per-sample sessions |
| **Parallelism** | Single user | Multiple tasks | Framework-managed |
| **Output** | Real-time events | Saved results files | Framework samples |
