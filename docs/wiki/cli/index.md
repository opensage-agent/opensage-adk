# CLI Reference

OpenSage provides a command-line interface for interactive development and utility tasks.

## Commands

| Command | Description | Reference |
|---------|-------------|-----------|
| [`opensage`](../generated/cli/opensage.md) | Main entry point | [--help](../generated/cli/opensage.md) |
| [`opensage web`](opensage-web.md) | Interactive web UI for agent development | [--help](../generated/cli/opensage-web.md) |
| [`opensage dependency-check`](dependency-check.md) | Verify external dependencies | [--help](../generated/cli/opensage-dependency-check.md) |

## opensage web vs Evaluations

| Aspect | `opensage web` | Evaluations |
|--------|----------------|-------------|
| **Use Case** | Development, debugging | Performance measurement |
| **Interaction** | Interactive chat | Batch processing |
| **Sessions** | Single long-lived | Multiple short-lived |
| **Parallelism** | Single user | Multiple tasks |
| **Output** | Real-time events | Saved results files |

See also: [Evaluations](../evaluation/index.md)
