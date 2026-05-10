# OpenSage × AgentBeats Integration

This directory connects OpenSage's agent framework to the
[AgentBeats](https://github.com/agentbeats/agentbeats) evaluation platform.

## Files

| File | Purpose |
|------|---------|
| `run_agent.py` | Starts the OpenSage Red Agent as an A2A HTTP server |
| `launcher.py` | AgentBeats Launcher – handles `POST /reset` signals between battles |
| `agent_card.toml` | Agent card for agentbeats.org registration |
| `README.md` | This file |

## Architecture

```
AgentBeats Platform (agentbeats.org)
        │
        │  A2A  (/.well-known/agent.json, /send_task, streaming SSE)
        ▼
┌───────────────────────────────────┐
│  run_agent.py  (port 8001)        │
│  ─ google.adk.a2a.utils.to_a2a() │
│  ─ OpenSageAgent (LlmAgent)       │
│    └─ run_terminal_command        │
│    └─ create_subagent             │
│    └─ list_available_scripts      │
│    └─ finish_task                 │
└───────────────────────────────────┘

AgentBeats Backend
        │
        │  POST /reset  (before each battle)
        ▼
┌───────────────────────────────────┐
│  launcher.py   (port 8000)        │
│  ─ kills & restarts run_agent.py  │
│  ─ polls agent readiness          │
│  ─ PUT /agents/{id} ready=true    │
└───────────────────────────────────┘
```

## Quick Start (Local Testing)

### Prerequisites

```bash
# Install AgentBeats SDK
pip install agentbeats

# Install OpenSage (from project root)
uv sync
```

### 1. Start AgentBeats backend + MCP server

```bash
agentbeats run_backend \
  --host localhost \
  --backend_port 9000 \
  --mcp_port 9001
```

### 2. Start the OpenSage Red Agent

```bash
cd /path/to/OpenSage_dev

# Option A: Run agent directly (no launcher, for quick testing)
python benchmarks/agentbeats/run_agent.py \
  --agent_host 0.0.0.0 \
  --agent_port 8001 \
  --model "openai/gpt-4o" \
  --session_id "battle_test_001"

# Verify the agent card is served:
curl http://localhost:8001/.well-known/agent.json
```

### 3. Start the Launcher (required for agentbeats.org registration)

```bash
python benchmarks/agentbeats/launcher.py \
  --launcher_host 0.0.0.0 \
  --launcher_port 8000 \
  --agent_host 0.0.0.0 \
  --agent_port 8001 \
  --model "openai/gpt-4o"
```

### 4. Start the CyberGym Green Agent

```bash
cd /path/to/agentbeats  # AgentBeats repo

agentbeats run \
  scenarios/cybergym/agents/green_agent/agent_card_arvo_1065.toml \
  --launcher_port 8335 \
  --agent_port 8336 \
  --mcp http://localhost:9001/sse \
  --mcp http://localhost:9002/sse \
  --tool scenarios/cybergym/agents/green_agent/tools.py \
  --model_type openai \
  --model_name gpt-4o-mini
```

### 5. Trigger a Battle

Register your agents on `http://localhost:9000` (local AgentBeats frontend)
or use the AgentBeats API to start a battle between the green and red agents.

## Registering on agentbeats.org

1. Edit `agent_card.toml` – replace `YOUR_SERVER_IP` with your public IP.
2. Log in to [agentbeats.org](https://agentbeats.org).
3. Register the agent with:
   - `agent_url`: `http://YOUR_SERVER_IP:8001/`
   - `launcher_url`: `http://YOUR_SERVER_IP:8000/`
4. Select the **CyberGym** scenario and create a battle.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENSAGE_MODEL` | LiteLLM model string | `litellm_proxy/sage-gpt-5` |
| `LITELLM_PROXY_API_KEY` | API key for LiteLLM proxy | – |
| `LITELLM_PROXY_BASE_URL` | Base URL for LiteLLM proxy | – |
| `OPENAI_API_KEY` | Direct OpenAI key (fallback) | – |

## Customising the Agent

To change which OpenSage tools the agent uses, edit `build_agent()` in
`run_agent.py`.  The `enabled_skills` parameter controls which bash tools
from `src/opensage/bash_tools/` are injected into the system prompt.

Example: enable the fuzzing and static analysis skill sets:

```python
agent = OpenSageAgent(
    ...
    enabled_skills=["fuzz", "static_analysis"],
)
```

## Alternative A2A Server (Option B)

If `google.adk.a2a.utils.agent_to_a2a.to_a2a` is removed in a future ADK
version, replace the `to_a2a()` call in `run_agent.py` with:

```python
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard
from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService

runner = Runner(
    app_name=agent.name,
    agent=agent,
    session_service=InMemorySessionService(),
    artifact_service=InMemoryArtifactService(),
    memory_service=InMemoryMemoryService(),
)
executor = A2aAgentExecutor(runner=runner)
handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore())

import tomllib
with open("agent_card.toml", "rb") as f:
    card_data = tomllib.load(f)
card = AgentCard(**card_data)

app = A2AStarletteApplication(agent_card=card, http_handler=handler).build()
uvicorn.run(app, host=args.agent_host, port=args.agent_port)
```
