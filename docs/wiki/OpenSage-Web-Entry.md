# opensage web - Interactive Development Entry Point

The `opensage web` command starts an interactive web UI for developing and debugging agents.

## CLI Commands

### opensage web

Starts an interactive web UI for agent development and debugging.

```bash
uv run opensage web \
  --config /path/to/config.toml \
  --agent /path/to/agent_dir \
  --port 8000 \
  --neo4j_logging  # optional
```

Session persistence and resume:

```bash
# Start normally (web default keeps snapshots on exit)
uv run opensage web --config /path/to/config.toml --agent /path/to/agent_dir

# Explicitly control cleanup behavior
uv run opensage web --config /path/to/config.toml --agent /path/to/agent_dir --auto_cleanup true

# Resume latest snapshot
uv run opensage web --resume

# Resume a specific saved snapshot
uv run opensage web --resume-from ctf_agent_c0606edc-2fff-496d-8964-48bdd7f0bd23
```

Snapshots are stored under:
`~/.local/opensage/sessions/<agent_name>_<session_id>/`

- `--resume` restores the latest saved snapshot.
- `--resume-from` restores a specific saved snapshot by directory name, bare
  session id suffix, or absolute path.

### opensage dependency-check

Checks if external dependencies are properly installed.

```bash
uv run opensage dependency-check
```

This command verifies:

- **CodeQL**: Required for CodeQL static analysis features
- **Docker**: Required for native Docker sandbox backend
- **kubectl**: Required for Kubernetes sandbox backend

All dependencies are optional unless you plan to use the corresponding features.
The command will show:

- Green checkmarks for available dependencies
- Yellow warnings for missing optional dependencies
- Red errors for missing required dependencies (if any)

**Example Output:**

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

## Workflow details

For the full step-by-step workflow (session creation, sandbox initialization,
service wiring, request/streaming flow, and cleanup), see
[opensage web - workflow details](OpenSage-Web-Workflow.md).
