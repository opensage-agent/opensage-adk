# Sessions

A **session** is the root object of an OpenSage run. Each session holds one `opensage_session_id`, one loaded configuration, and every resource created for the agent it hosts: sandboxes, Neo4j clients, the model registry, and the agent manager that runs sub-agents. The code lives in [`src/opensage/session/opensage_session.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/session/opensage_session.py).

## Creating a Session

When `opensage web` or the evaluation runner starts, it calls `get_opensage_session`:

```python
import opensage

session = opensage.get_opensage_session(
    opensage_session_id,          # user-chosen string; use a UUID if unsure
    config_path=config_path,      # path to your TOML, or None for defaults
    create_if_missing=True,
    agent_dir=agent_dir,          # directory that holds agent.py, or None
)
```

`get_opensage_session` returns an existing `OpenSageSession` from the registry or constructs a new one. Construction loads the TOML through `OpenSageConfig.from_toml(...)`, or `OpenSageConfig.create_default()` when `config_path` is `None`. Template variables of the form `${VAR_NAME}` expand during this load.

The constructor then builds the per-session managers and services listed below.

| Attribute | Type | What It Owns |
|---|---|---|
| `session.sandboxes` | `OpenSageSandboxManager` | Docker, podman, native, and k8s sandbox instances, their initializers, and shared volumes. Look one up with `get_sandbox(sandbox_type)`. |
| `session.neo4j` | `OpenSageNeo4jClientManager` | Neo4j clients that share the bolt endpoint a sandbox provides. Reach one with `await get_async_client(...)`. |
| `session.agent_manager` | `AgentManager` | The root agent and every sub-agent instance, their inboxes, and their run loops. See [Multi-Agent](./multi-agent.md). |
| `session.llms` | `LlmRegistry` | The models named in `[model]`, used when a tool delegates to a specific model. |
| `session.budget` | `BudgetManager` | The runtime token and call budget for the session. |
| `session.session_service` | `OpenSageInMemorySessionService` | ADK conversation state (events, state deltas) for the session. |

`session.artifact_service`, `session.memory_service`, and `session.credential_service` hold the standard ADK in-memory services. The session registry, `OpenSageSessionRegistry`, is the only global singleton in the architecture; it tracks live sessions and runs cleanup at exit.

## Sandbox Pruning via a Dummy Agent

Before launching any sandbox, the runtime instantiates your agent once, inspects which sandbox types it references, and discards the instance. A sandbox declared in `[sandbox.sandboxes.*]` but unused by the agent survives in config yet never starts, so startup time tracks what the agent actually needs.

```python
from opensage.toolbox.sandbox_requirements import collect_sandbox_dependencies

dummy_agent = mk_agent(opensage_session_id=session_id)
sandbox_dependencies = collect_sandbox_dependencies(dummy_agent, config=session.config)
# sandboxes absent from sandbox_dependencies are dropped before launch
```

Tools declare their sandbox needs with the `@requires_sandbox("main", "gdb_mcp")` decorator from `opensage.toolbox.sandbox_requirements`; a skill declares them in a `## Requires Sandbox` section of its `SKILL.md`. `collect_sandbox_dependencies` walks the agent, its sub-agents, and its enabled skills.

## Runtime Loop

Once the session is ready, the runtime instantiates the real agent and hands it to ADK's `Runner`:

```python
root_agent = mk_agent(opensage_session_id=session_id)
plugins = load_plugins(...)
# ADK's Runner drives the reason-act loop over session.session_service
```

The web UI wires these services into a FastAPI app served by Uvicorn. Evaluation mode uses them directly without a web server. The loop matches in both modes: a user message arrives, the model reasons, it calls a tool, the sandbox runs the tool, the result returns, and the cycle repeats until the agent finishes or reaches `max_llm_calls`.

## Reaching Session State Inside Tools

A tool or sub-agent that needs session-scoped resources looks up the session by id:

```python
from opensage.session import get_opensage_session

def my_tool(tool_context):
    session = get_opensage_session(tool_context._invocation_context.session.id)
    sandbox = session.sandboxes.get_sandbox("main")
    ...
```

The built-in MCP factories (`get_gdb_toolset(opensage_session_id)`) and the sub-agent tools (`create_subagent`, `call_subagent`, `list_subagents`) locate their working context the same way.

## Cleanup

`OpenSageSession.cleanup()` is synchronous and idempotent; repeated calls return at once. It cancels any running agent tasks through `agent_manager.cancel_all_tasks()`, and when `auto_cleanup` is set in the config, it releases sandbox resources through `session.sandboxes.cleanup()`. Peer-message inboxes need no flushing here because `AgentManager.start` resets them, and the ADK trajectory file is written incrementally during the run. `cleanup_opensage_session(opensage_session_id)` runs this cleanup and drops the session from the registry; the registry also registers an `atexit` fallback so a session still releases its sandboxes if the process exits early.

## Related References

- [`[sandbox]` configuration reference](../../reference/configuration/sandbox.md): sandbox fields.
- [Sandbox system guide](../../developer-guide/sandbox/index.md): lifecycle internals.
- [Multi-Agent](./multi-agent.md): how `agent_manager` runs sub-agents.
- [Customize Agents](../../get-started/customize/index.md): loading configs in your own code.
