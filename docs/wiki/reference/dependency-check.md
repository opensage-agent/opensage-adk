---
icon: lucide/package-check
---

# opensage dependency-check

Run `uv run opensage dependency-check --help` for the current flag reference.

Checks whether external dependencies required by specific OpenSage-ADK features are installed.

## Usage

```bash
uv run opensage dependency-check
```

## What It Checks

```text
Usage: opensage dependency-check [OPTIONS]

  Check OpenSage external dependencies.

Checks for manually installed dependencies:
  - CodeQL: Required for CodeQL static analysis features
  - Docker: Required for native Docker sandbox backend
  - kubectl: Required for Kubernetes sandbox backend (under development)

  All dependencies are optional unless you plan to use the corresponding
  features.

Options:
  --help  Show this message and exit.
```


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
