# Getting Started

## Prerequisites

- Python 3.11+ (recommended)
- `uv` package manager (required)
- Docker (for sandbox execution)
- CodeQL (optional, for CodeQL analysis)

## Installation

### Step 1: Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Step 2: Clone and Setup

```bash
# Clone the repository
git clone https://github.com/AIgiSE/AIgiSE.git
cd AIgiSE

# Create virtual environment
uv venv --python "python3.11" ".venv"
source .venv/bin/activate

# Install dependencies
uv sync

# Install pre-commit hooks
uv run pre-commit install
```

### Step 3: Additional Setup

**CodeQL Setup (Optional):**
```bash
# Download CodeQL bundle
# Extract to: PROJECT_PATH/src/<package>/sandbox_scripts/codeql
```

## Verify Installation

```bash
# Check SAGE-X CLI is available
uv run sage-x --help
```

## Next Steps

- [Project Structure](Project-Structure.md) - Understand the codebase structure
- [Core Concepts](Core-Concepts.md) - Learn the core concepts
- [Development Guides](Development-Guides.md) - Start developing

## Sandbox Images (Docker)

SAGE-X uses Docker-based sandboxes. Some sandboxes require Python tooling inside
their images (for initializers and Python-based helper scripts).

### uv inside sandbox images

Sandbox Dockerfiles install `uv` using:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Python in sandbox images

For sandboxes that need Python, the Dockerfile creates a venv at **`/app/.venv`**:

```bash
uv venv --python 3.12
```

and installs Python dependencies into that venv:

```bash
uv pip install <package...>
```

Because sandbox command execution is **non-persistent** (each command is a fresh
process), prefer calling the venv Python explicitly:

- `/app/.venv/bin/python ...`
- `/app/.venv/bin/pip ...`

### Per-sandbox requirements (defaults)

- **main sandbox**
  - Requires `python3` and the Python package `neo4j`
  - Image built from `src/<package>/templates/dockerfiles/main/Dockerfile`

- **joern sandbox**
  - Requires `python3` and Python packages `httpx` + `websockets`
  - Image built from `src/<package>/templates/dockerfiles/joern/Dockerfile`

## See Also

- [Introduction](Introduction.md) - Project overview
- [Configuration](Configuration.md) - Configuration guide
