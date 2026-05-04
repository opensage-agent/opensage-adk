---
icon: lucide/terminal
---


# CLI Reference

OpenSage-ADK provides a command-line interface for interactive development and utility tasks.

## Commands

| Command | Description | Reference |
|---------|-------------|-----------|
| [`opensage`](../generated/cli/opensage.md) | Main entry point | [--help](../generated/cli/opensage.md) |
| [`opensage web`](opensage-web.md) | Interactive web UI for agent development | [--help](../generated/cli/opensage-web.md) |
| [`opensage dependency-check`](dependency-check.md) | Verify external dependencies | [--help](../generated/cli/opensage-dependency-check.md) |


```text
Usage: opensage [OPTIONS] COMMAND [ARGS]...

  OpenSage CLI tools.

Options:
  --help  Show this message and exit.

Commands:
  dependency-check  Check OpenSage external dependencies.
  web               Starts an OpenSage-flavored Web UI: prepare...
```


## `opensage web` vs Evaluations

| Aspect | `opensage web` | Evaluations |
|--------|----------------|-------------|
| **Use Case** | Development, debugging | Performance measurement |
| **Interaction** | Interactive chat | Batch processing |
| **Sessions** | Single long-lived | Multiple short-lived |
| **Parallelism** | Single user | Multiple tasks |
| **Output** | Real-time events | Saved results files |

See also: [Evaluations](../developer-guide/evaluation/index.md)
