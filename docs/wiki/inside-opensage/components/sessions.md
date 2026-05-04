# Sessions

A **session** is the root object of an OpenSage run. One session owns one `session_id`, one loaded configuration, and every resource spawned for the agent it hosts: sandboxes, Neo4j connections, dynamic sub-agents, ensembles, message boards. The code lives in [`src/opensage/session/opensage_session.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/session/opensage_session.py).

## Creating a Session

When `opensage web` or the evaluation runner starts, it calls:

```python
import opensage

session = opensage.get_session(
    session_id=session_id,       # user-chosen string; use a UUID if unsure
    config_path=config_path,     # path to your TOML, or None for defaults
)
```

`get_session` constructs an `OpenSageSession`, which:

1. Loads the TOML file through `OpenSageConfig.from_toml(...)` (or `create_default()` if no path is given). Template variables (`${VAR_NAME}`) are expanded here.
2. If `memory.database.long_term` is configured, wires up the long-term-memory settings (Neo4j embeddings, entity schema).
3. Instantiates five per-session managers:

| Manager | Source | What it owns |
|---|---|---|
| `DynamicAgentManager` | `opensage_dynamic_agent_manager.py` | Sub-agents spawned at runtime, plus their lifecycle state (`CREATED` / `ACTIVE` / `PAUSED` / `STOPPED` / `PENDING_TOOLS`). |
| `OpenSageSandboxManager` | `opensage_sandbox_manager.py` | Docker / native / k8s sandbox instances, their initializers, and shared volumes. |
| `OpenSageNeo4jClientManager` | `opensage_neo4j_client_manager.py` | Lazy Neo4j client(s) sharing the session's sandbox-provided bolt endpoint. |
| `OpenSageEnsembleManager` | `opensage_ensemble_manager.py` | Model-ensemble fan-out and message-board coordination. |
| `MessageBoardManager` (on-demand) | `message_board.py` | Append-only JSONL boards used during ensemble runs. |

## Sandbox Pruning via a Dummy Agent

Before any sandbox is launched, the runtime **instantiates your agent once, throws it away, and inspects which sandbox types it references.** Anything declared in `[sandbox.sandboxes.*]` but unused by the agent's tools is pruned, so startup time stays proportional to what the agent actually needs.

```python
mk_agent = _load_mk_agent_from_dir(agent_dir)
dummy_agent = mk_agent(session_id=session_id)
sandbox_dependencies = collect_sandbox_dependencies(dummy_agent)
# unused sandboxes are removed from session.config.sandbox.sandboxes before launch
```

Tools and skills declare their sandbox needs via `@requires_sandbox("main", "gdb_mcp")` or a `## Requires Sandbox` section in a skill's `SKILL.md`. `collect_sandbox_dependencies()` walks the agent, its sub-agents, and its enabled skills transitively.

## Runtime Loop

Once the session is ready, the real agent is instantiated (the dummy is discarded):

```python
session_service = InMemorySessionServiceBridge()
artifact_service = InMemoryArtifactService()
memory_service = InMemoryMemoryService()
credential_service = InMemoryCredentialService()

root_agent = mk_agent(session_id=session_id)
plugins = load_plugins(...)

# ADK's Runner drives the reason-act loop
```

In the **web UI**, these services are wired into a FastAPI app served by Uvicorn. In **evaluation mode**, they are used directly without a web server. The core loop is identical in both modes: user message -> LLM reasons -> tool call -> sandbox executes -> result returns -> repeat until completion or `max_llm_calls`.

## Accessing Session State Inside Tools

Any tool or sub-agent that needs session-scoped resources looks up the session by id:

```python
from opensage.session import get_opensage_session

def my_tool(tool_context):
    session = get_opensage_session(tool_context.session.id)
    neo4j = session.neo4j.get_client("main")
    sandbox = session.sandboxes.get("main")
    ...
```

This is how the built-in MCP factories (`get_gdb_toolset(opensage_session_id)`), the dynamic sub-agent tools (`create_subagent`, `call_subagent_as_tool`), and the ensemble helpers all locate their working context.

## Cleanup

When a session ends (Ctrl+C in the web UI, or task completion in evaluation mode), `cleanup_all_sessions()` is called:

1. Sandbox containers are stopped.
2. Shared volumes are cleaned up if `auto_cleanup = true` in the root config.
3. Neo4j clients are closed.
4. Dynamic-agent metadata is flushed to `~/.local/opensage/dynamic_agents/` if persistence is enabled.
5. The in-memory session registry is cleared.

## Related References

- [`[sandbox]` configuration reference](../../reference/configuration/sandbox.md): sandbox fields.
- [Sandbox system guide](../../developer-guide/sandbox/index.md): lifecycle internals.
- [Customize Agents](../../get-started/customize/index.md): loading configs in your own code.
