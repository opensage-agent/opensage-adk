# opensage dependency-check

> Full flag reference: [`opensage dependency-check --help`](../generated/cli/opensage-dependency-check.md)

Checks whether external dependencies required by specific OpenSage features are installed.

## Usage

```bash
uv run opensage dependency-check
```

## What it checks

| Dependency | Required for |
|------------|-------------|
| **CodeQL** | CodeQL static analysis features |
| **Docker** | Native Docker sandbox backend |
| **kubectl** | Kubernetes sandbox backend |

All dependencies are optional unless you plan to use the corresponding features.

## Output

The command reports status for each dependency:

- Green checkmarks for available dependencies
- Yellow warnings for missing optional dependencies
- Red errors for missing required dependencies (if any)

**Example:**

```
Checking OpenSage dependencies...

Checking CodeQL...
  [OK] CodeQL binary found at /path/to/codeql

Checking Docker...
  [OK] Docker daemon is running and accessible

Checking kubectl...
  [WARN] kubectl command not found in PATH. Install kubectl to use Kubernetes backend.
    Note: Only required when using Kubernetes sandbox backend

============================================================
[WARN] Some dependencies missing (2/3 available)

Note: Missing dependencies are optional unless you plan to use
the corresponding features.
============================================================
```
