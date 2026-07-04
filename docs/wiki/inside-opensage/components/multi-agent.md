# Multi-Agent

OpenSage-ADK runs one agent as the simple case and treats **multi-agent orchestration** as the default for larger work. A root `OpenSageAgent` declares specialists ahead of time or creates them at runtime, delegates a sub-task to a chosen model, and collects results synchronously or through an inbox. The model drives all of this by calling tools; no out-of-band orchestration code is required.

Source:

- Agent manager: [`src/opensage/orchestration/manager.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/orchestration/manager.py)
- Instance types and state: [`src/opensage/orchestration/types.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/orchestration/types.py)
- Sub-agent tools: [`src/opensage/toolbox/general/orchestration_tools.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/toolbox/general/orchestration_tools.py)
- Self-reflection tools: [`src/opensage/toolbox/general/agent_tools.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/toolbox/general/agent_tools.py)
- Inbox delivery: [`src/opensage/orchestration/plugins/inbox_delivery.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/orchestration/plugins/inbox_delivery.py)

## The Agent Manager

`session.agent_manager` is an `AgentManager`. It holds two structures: a registry that maps an agent name to its `OpenSageAgent` template, and a set of live instances keyed by `session_id`. Declaring an agent adds a template; running one creates an instance.

`register_agent_tree(root)` registers the root and every statically declared sub-agent under it. `spawn(agent_name, ...)` builds a fresh instance from a registered template, persists its definition and state to disk under the session directory, and returns the new `session_id`; it does not start an invocation. Each instance carries an `AgentInstanceState` from `orchestration/types.py`: `RUNNING` during an invocation, `SLEEPING` when idle, and `TERMINATING` or `TERMINATED` on shutdown. Persisting to disk lets an instance survive a process restart.

## Declaring Sub-Agents Statically

When the set of specialists is known at construction time, pass them through `subagents=[...]` and give the root the `call_subagent` tool:

```python
from opensage.toolbox.general.orchestration_tools import call_subagent, list_subagents

calculation_agent = OpenSageAgent(name="calculation_agent", model=..., tools=[...])

root_agent = OpenSageAgent(
    name="root",
    subagents=[calculation_agent],
    tools=[call_subagent, list_subagents],
)
```

`OpenSageAgent` rejects two ADK-native alternatives so that one path stays canonical: it raises `ValueError` if you wrap a sub-agent in `AgentTool` and place it in `tools=`, and it raises `ValueError` for ADK's `sub_agents=` keyword. Declare sub-agents with `subagents=` and invoke them with `call_subagent`.

## Creating Sub-Agents Dynamically

When the root cannot know which specialists it needs until runtime, give it `create_subagent` and let it build them:

```python
from opensage.toolbox.general.orchestration_tools import (
    create_subagent, call_subagent, list_subagents,
)
```

`create_subagent(agent_name, instruction, model_name, tools_list=None, enabled_skills=None, description=None)` builds a new `OpenSageAgent`, registers it in the same namespace as the static `subagents=[...]`, and persists its definition to disk. `model_name` must be a name that `get_available_models` returns. `tools_list` defaults to every tool the caller holds; pass a shorter list to restrict the child, since baseline tools such as `run_terminal_command` and the orchestration tools are injected regardless. `enabled_skills` selects bash-tool skills the same way the constructor does: `None` for none, `["all"]` for every top-level skill, or a list of paths. Registration alone does not run the child; follow it with `call_subagent(agent_name, request)`.

`list_subagents()` returns the agents registered so far, so the root can see what it declared or built earlier in the session.

## Delegating to a Specific Model

`call_subagent` accepts an optional `model_name` that overrides the template's model for one spawned instance:

```python
call_subagent("reviewer", request, model_name="openai/gpt-5")
```

`get_available_models()` returns the model names in the session's `LlmRegistry`, which are the valid values for `model_name`. To hedge a single-model mistake, call the same sub-agent several times with different `model_name` values and compare the results in the parent. This replaces the earlier ensemble tools; fan-out is now expressed as ordinary `call_subagent` calls rather than a dedicated primitive.

## Synchronous and Asynchronous Calls

`call_subagent(agent_name, request, mode="sync", use_parent_history=False, model_name=None)` runs in one of two modes. In `"sync"` mode it blocks until the sub-agent finishes and returns the final response text. In `"async"` mode it returns at once; when the sub-agent finishes, its result lands in the caller's inbox, and the caller wakes if it has already ended its turn.

The `InboxDeliveryPlugin` (`orchestration/plugins/inbox_delivery.py`) surfaces inbox messages to an agent between turns, so an async result or a message from a peer reaches the model without polling. Setting `use_parent_history=True` gives the sub-agent the parent's full conversation history; the default of `False` starts the child with only the `request`, which saves context budget for self-contained tasks.

## Self-Reflection Tools

`toolbox/general/agent_tools.py` holds a small family of tools that push the model to externalize meta-reasoning rather than bury it in prose:

| Tool | Purpose |
|---|---|
| `think` | Records the agent's current plan, passed as the argument. Cheap, and it trains the habit of planning before acting. |
| `plan` | Same shape as `think`, framed for longer-horizon planning. |
| `complain` | Marks a stuck or uncertain state for operators to see. |
| `note_suspicious_things` | Records an observation the agent wants to flag before committing to it. |
| `log_finding` | Records a confirmed finding during a task. |
| `critique` | Calls a model chosen through the `model_name` argument with the recent conversation and returns feedback on progress and missed steps. |
| `audit_assumptions` | Calls a chosen model to list assumptions the agent has made without evidence. |
| `validate_claim` | Calls a chosen model to check one specific `claim` against the conversation so far. |

`critique`, `audit_assumptions`, and `validate_claim` are multi-agent in disguise: the agent names a model from `get_available_models`, and the tool runs a fresh call to that model with a specialized prompt, then returns its verdict.

## When to Reach for Which Pattern

| Situation | Pattern |
|---|---|
| Fixed set of helpers known at construction time | Declare `subagents=[...]` and call with `call_subagent`. |
| Decomposition depends on the task | Build specialists at runtime with `create_subagent`. |
| Hedge against a single-model mistake | Call the same sub-agent across models with `call_subagent(model_name=...)`. |
| Continue the parent while a child runs | Use `call_subagent(mode="async")` and read the inbox. |

## Related References

- [Dynamic Sub-Agents example](../../get-started/examples/agents_with_features/sample_dynamic_subagent.md): runnable.
- [Multi-Model Delegation example](../../get-started/examples/agents_with_features/sample_agent_ensemble.md): runnable.
