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
git clone https://github.com/OpenSage-ADK/OpenSage.git
cd OpenSage

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
cd "src/opensage/sandbox_scripts"

# Download CodeQL bundle (current default expected by the framework)
wget "https://github.com/github/codeql-action/releases/download/codeql-bundle-v2.18.4/codeql-bundle-linux64.tar.gz"

# Extract only the `codeql/` directory so `src/opensage/sandbox_scripts/codeql/codeql` exists
tar -xzf "codeql-bundle-linux64.tar.gz" "codeql"
rm -f "codeql-bundle-linux64.tar.gz"
```

### Step 4: Verify Installation

```bash
# Check OpenSage CLI is available
uv run opensage --help

# Check optional external dependencies (Docker / CodeQL / kubectl, etc.)
uv run opensage dependency-check
```

## Tools

OpenSage agents can use Python tools, filesystem-discovered Skills, and MCP
toolsets.

[Adding a New Tool](Adding-Tools.md): How to add new tools/skills/toolsets.

## Creating Your Own Agent

This section shows the minimal structure and conventions for writing an agent
that OpenSage can load via `opensage web` and evaluation entry points.

### 1) Agent directory layout

Create a directory for your agent:

```
my_agent/
└── agent.py
```

OpenSage loads your agent by importing `agent.py` and calling the `mk_agent`
factory function.

### 2) `agent.py`: implement `mk_agent(session_id, ...)`

`mk_agent` is a factory that receives a **session id** and returns the root ADK
agent instance (your “main” agent). It should be deterministic and avoid doing
heavy work at import time.

**What is `session_id`?**

`session_id` is a user-chosen string that identifies one OpenSage run/session.
It is used to scope and isolate session resources (sandboxes, config, Neo4j
clients, dynamic agents, etc.). You can set it to any value you like, but it
should be **unique per concurrent run** (using a UUID is a common choice).

Minimal template:

```python
import os
from typing import Optional

from google.adk.models import BaseLlm
from google.adk.models.lite_llm import LiteLlm

import opensage
from opensage.agents import OpenSageAgent


def mk_agent(
    session_id: str,
    model: Optional[BaseLlm] = None,
):
    # Link to the session created by OpenSage (sandboxes/config/neo4j live here).
    session = opensage.get_session(session_id)

    if model is None:
        model = LiteLlm(
            model="YOUR_MODEL_NAME",
            api_key=os.environ.get("YOUR_API_KEY"),
        )

    root_agent = OpenSageAgent(
        name="my_agent",
        description="My custom OpenSage agent.",
        model=model,
        instruction="You are a helpful assistant.",
        # Flags / features:
        enabled_skills="all",
        enable_memory_management=False,
        # ADK tools you want the agent to call:
        tools=[],
        # Optional: static sub-agents (if you have them)
        sub_agents=[],
    )
    return root_agent
```

### 3) Base agent attributes / flags (what you typically customize)

The OpenSage base agent extends ADK’s `LlmAgent` and adds OpenSage-specific
capabilities. The most commonly used fields are:

**`name` / `description`**: identify the agent in logs and the UI.
**`model`**: the LLM backend for reasoning.
**`instruction`**: system instruction/policy for the agent.
**`tools`**: Python tools and toolsets (including dynamic sub-agent tools).
**`sub_agents`**: static sub-agents you attach at construction time.
**`enabled_skills`**: which filesystem Skills under `bash_tools/` are exposed.
`None`: enable no Skills
`"all"` / `["all"]`: enable only top-level Skills
`List[str]`: prefix allowlist (e.g. `"retrieval"` or `"static_analysis/search-function"`)
**`enable_memory_management`**: enables a dedicated **memory management sub-agent**
(often exposed as a `memory_management_agent` tool) that mediates interactions
with short-term (session history/trace) and long-term (Neo4j graph memory).
**`tool_combos`** (optional): group multi-step tool workflows into a single callable tool.

### 4) Configuration

OpenSage sessions are configured via TOML. You can start from the default config
and **override only what you need** (sandboxes, build commands, plugins, etc.).

When running the web UI, pass your config path:

```bash
uv run opensage web --config "/path/to/config.toml" --agent "/path/to/my_agent"
```

Minimal workable config (example `opensage.toml`) that can start the web UI and
launch the default `main` sandbox:

```toml
# Optional: a human-readable label used for naming/logging. It can be any string.
task_name = "my_task"

[sandbox]
backend = "native"

[sandbox.sandboxes.main]
image = "ubuntu:20.04_main"
project_relative_dockerfile_path = "src/opensage/templates/dockerfiles/main/Dockerfile"

[sandbox.sandboxes.main.build_args]
BASE_IMAGE = "ubuntu:20.04"
```

See the full field reference in [Configuration](Configuration.md).

### 5) Turning on common features

**Enable memory agent**

In `agent.py`: set `enable_memory_management=True`.

**Enable dynamic sub-agents**

Dynamic sub-agents are enabled by adding the dynamic-subagent tools to your
agent’s `tools` list (e.g. `create_subagent`, `list_active_agents`,
`call_subagent_as_tool`). OpenSage will manage their lifecycle under the current
session.

If you want dynamic agents to persist across runs, set an `agent_storage_path`
in your TOML configuration (see [Configuration](Configuration.md)).

### 6) Examples (used by integration tests)

The repo includes small “feature agents” used in integration tests. Use them as
starting points:

`examples/agents_with_features/sample_dynamic_subagent/`
`examples/agents_with_features/sample_agent_ensemble/`
`examples/agents_with_features/sample_summarization/`
`examples/agents_with_features/sample_neo4j_logging/`
`examples/agents_with_features/sample_tool_combo/`

Run one in the web UI by pointing `--agent` to the example directory and
`--config` to the config file shipped alongside it.

## Adding tools to your agent

OpenSage supports three tool types you can enable in your agent:

**Python tools** (callable functions)
**Agent Skills** (bash/Python scripts discovered from `bash_tools/`)
**MCP toolsets** (external services exposed via MCP)

### 1) Python tools

Any callable can be a tool. The function signature becomes the tool schema, and
the docstring is shown to the model as the tool description.

```python
def greet(name: str) -> str:
    """Return a friendly greeting."""
    return f"Hello, {name}!"
```

Enable it by adding it to `tools`:

```python
root_agent = OpenSageAgent(
    name="my_agent",
    model=...,
    instruction="...",
    tools=[greet],
)
```

### 2) Agent Skills (bash script tools)

Skills are filesystem-discovered tools that run inside sandboxes.

Directory layout:

```
src/opensage/bash_tools/
└── category/
    └── tool-name/
        ├── SKILL.md
        └── scripts/
            └── tool_script.sh
```

`SKILL.md` outline:

```markdown
---
name: my-tool
description: Tool description
should_run_in_sandbox: main
---

## Usage

    scripts/tool_script.sh <input> --option value

## Requires Sandbox

main
```

Enable Skills using `enabled_skills` on the base agent:

`None`: enable no Skills
`"all"` / `["all"]`: enable only top-level Skills (`<root>/*/SKILL.md`)
`List[str]`: prefix allowlist (e.g. `"retrieval"` or `"static_analysis/search-function"`)

Example:

```python
root_agent = OpenSageAgent(
    name="my_agent",
    model=...,
    instruction="...",
    enabled_skills=["retrieval", "static_analysis/search-function"],
)
```

### 3) MCP toolsets

To use an MCP toolset, write a **getter function** that returns an `MCPToolset`
instance. The endpoint URL should come from config and be resolved per-session
via `get_mcp_url_from_session_id("<service>", session_id)`.

```python
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseConnectionParams

from opensage.toolbox.decorators import requires_sandbox, safe_tool_execution
from opensage.utils.agent_utils import get_mcp_url_from_session_id


@safe_tool_execution
@requires_sandbox("gdb_mcp")
def get_gdb_toolset(session_id: str) -> MCPToolset:
    """Return a GDB MCP toolset for the current session."""
    url = get_mcp_url_from_session_id("gdb_mcp", session_id)
    return MCPToolset(connection_params=SseConnectionParams(url=url))
```

Enable it by adding the getter to `tools`:

```python
root_agent = OpenSageAgent(
    name="my_agent",
    model=...,
    instruction="...",
    tools=[get_gdb_toolset],
)
```

MCP service endpoints are configured in TOML (host/port). See
[Configuration](Configuration.md) for the MCP section.

## Next Steps

[Project Structure](Project-Structure.md) - Understand the codebase structure
[Development Guides](Development-Guides.md) - Start developing
