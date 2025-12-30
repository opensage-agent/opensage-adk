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
git clone https://github.com/YOUR_USERNAME/adk-python.git
cd adk-python/AIgiSE

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
# Extract to: PROJECT_PATH/src/aigise/sandbox_scripts/codeql
```

## Verify Installation

```bash
# Check aigise CLI is available
uv run aigise --help
```

## Next Steps

- [Project Structure](Project-Structure.md) - Understand the codebase structure
- [Core Concepts](Core-Concepts.md) - Learn the core concepts
- [Development Guides](Development-Guides.md) - Start developing

## See Also

- [Introduction](Introduction.md) - Project overview
- [Configuration](Configuration.md) - Configuration guide
