# Multi-Agent

OpenSage-ADK treats "one agent" as the simple case and **multi-agent orchestration** as the default for non-trivial workloads. A root `OpenSageAgent` can spawn specialists, fan a sub-task across several models, chain tools as mini sequential agents, and share state through append-only message boards, all using tools the model calls directly with no out-of-band orchestration code.

Source:

- Dynamic sub-agent tools: [`src/opensage/toolbox/general/dynamic_subagent.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/toolbox/general/dynamic_subagent.py)
- Dynamic-agent manager: [`src/opensage/session/opensage_dynamic_agent_manager.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/session/opensage_dynamic_agent_manager.py)
- Ensemble manager: [`src/opensage/session/opensage_ensemble_manager.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/session/opensage_ensemble_manager.py)
- Ensemble tools: [`src/opensage/toolbox/general/agent_tools.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/toolbox/general/agent_tools.py)
- Message board: [`src/opensage/session/message_board.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/session/message_board.py)
- ToolCombo: [`src/opensage/features/tool_combo.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/features/tool_combo.py)

## Three Patterns

### 1. Sub-Agent as a Tool (`AgentTool`)

The simplest composition: wrap one agent in `AgentTool(agent=...)` and add it to the parent's `tools=[...]` list. The parent calls it like any other tool; the sub-agent runs to completion and returns its final answer as the tool result. This is ADK's native primitive; OpenSage does not modify it.

```python
from google.adk.tools.agent_tool import AgentTool

calculation_agent = OpenSageAgent(name="calculator", model=..., tools=[...])

root_agent = OpenSageAgent(
    name="root",
    tools=[AgentTool(agent=calculation_agent)],
)
```

Good for **statically-known specialists**: the set of sub-agents is fixed at agent-construction time.

### 2. Dynamic Sub-Agents

When you do not know which specialists the root will need until runtime, hand it three tools and let it decide:

```python
from opensage.toolbox.general.dynamic_subagent import (
    create_subagent, call_subagent_as_tool, list_active_agents,
)
```

- **`create_subagent(name, model, instruction, tools_list, enabled_skills)`**: spawn a new `OpenSageAgent` inside the session, register it in `DynamicAgentManager`, and return its id. Status starts at `CREATED`.
- **`call_subagent_as_tool(subagent_id, request)`**: wrap the sub-agent in `AgentTool`, invoke it, stringify the result.
- **`list_active_agents()`**: introspection for the root, so it knows what it spawned earlier in the session.

The `DynamicAgentManager` keeps the parent-child tree in memory (and optionally mirrors metadata to `~/.local/opensage/dynamic_agents/` for cross-session persistence). Status transitions: `CREATED -> ACTIVE -> PAUSED / STOPPED / ERROR / PENDING_TOOLS`.

**Tool inheritance.** `tools_list` is a list of names requested from the parent's tool surface. `extract_tools_from_agent()` validates each name against the parent's tools and toolsets; a `tool_name_prefix` match pulls in a whole MCP toolset. If a requested tool does not exist, the child is parked in `PENDING_TOOLS` until it is available (useful when a sibling agent is still registering a tool).

**Skill inheritance** is independent. `None` gives the child no skills; `"all"` gives every top-level skill; a list of path prefixes gives the intersection. The child's instruction is augmented with a guardrail documenting the restriction.

### 3. Ensembles (Multi-Model Fan-Out)

When a single model is unreliable, fan the same sub-task across several models in parallel and aggregate:

```python
from opensage.toolbox.general.agent_tools import (
    agent_ensemble, agent_ensemble_pairwise,
    get_available_agents_for_ensemble, get_available_models,
)
```

- **`agent_ensemble(agent_name, instruction, model_name_to_count)`**: launch `N` instances of `agent_name`, one per model in the count-dict (e.g. `{"anthropic/claude-opus-4-7": 2, "openai/gpt-5": 1}`), each receiving the same instruction. Results are aggregated by the `summarize` model profile, which is explicitly prompted to highlight consensus and disagreement.
- **`agent_ensemble_pairwise`**: each parallel task gets **its own** instruction and model. Useful for "explore from two angles" rather than "verify with two models".

`OpenSageEnsembleManager` runs each instance in its own `asyncio` task. All instances share a **message board** (see below) scoped by an ensemble id, so they can post partial findings that the others can read.

The `available_models_for_ensemble` list in `[agent_ensemble]` bounds which models the root can choose from; `get_available_models()` returns that list to the LLM.

## Message Boards

For coordination across parallel agents in an ensemble, OpenSage provides an **append-only JSONL board** (`MessageBoardManager`, `message_board.py`). Each ensemble run gets a unique `board_id`; every participant posts timestamped, author-labeled messages to the board with `post_to_board(...)`. Writes are lock-free; each agent keeps its own read cursor.

The `message_board_diff_plugin` (when enabled) surfaces new board entries to each agent between turns; tool responses grow a `_message_board_diff` field listing posts the agent has not seen yet. This turns the board into a lightweight shared blackboard without requiring the model to poll it.

## Lightweight Self-Reflection Tools

A small family of tools in `toolbox/general/agent_tools.py` encourage the model to externalize meta-reasoning instead of burying it in prose:

| Tool | Purpose |
|---|---|
| `think` | A no-op: the agent is asked to write its current plan as the argument. Cheap, trains the habit of planning before acting. |
| `plan` | Same shape, framed for longer-horizon planning. |
| `complain` | Marker for a stuck/uncertain state, visible to operators. |
| `note_suspicious_things` | Records an observation the agent wants to flag without committing to it yet. |
| `critique` | Calls a registered model (chosen by the agent via `model_name` parameter) with the recent conversation and returns critical feedback on progress, missed steps, and unjustified claims. |
| `flag_unjustified_claims` | Sends conversation history to a registered model (chosen via `model_name`) to enumerate claims that were not substantiated by evidence. |

The critique/flag-claims tools are multi-agent in disguise. The agent picks a model from the registry (see `get_available_models`) and passes it as an argument; under the hood the tool spins up a fresh LLM call with that model and a specialized prompt, and returns its verdict.

## ToolCombo as a Sequential Mini-Agent

`ToolCombo` is a small primitive for chaining several tools into one atomic "tool" call:

```python
from opensage.features.tool_combo import ToolCombo

combo = ToolCombo(
    name="add_then_double",
    tool_sequences=[add_numbers, multiply_by_two],
    model=LiteLlm(model="openai/gpt-5.4"),
    return_history=True,
)

root_agent = OpenSageAgent(..., tool_combos=[combo])
```

Under the hood, `ToolCombo` builds a `SequentialAgent` (ADK primitive) where each step is itself a tiny `OpenSageAgent` running the next tool with context-aware instructions. The final step optionally gets a `delegate_to_parent` tool so the chain can return early.

Two modes:

- **`return_history=True`**: the chain is exposed as a sub-agent, and intermediate steps are visible in the root's history.
- **`return_history=False`**: wrapped in `AgentTool`; the root sees only the final result. Cheaper for the caller's context.

See the [Agent with Tool Combo](../../get-started/examples/agents_with_features/sample_tool_combo.md) example for a runnable demo.

## When to Reach for Which Pattern

| Situation | Pattern |
|---|---|
| Fixed set of helpers known at design time | **`AgentTool`** (sub-agent as a tool). |
| Task decomposition is task-dependent | **Dynamic sub-agents**. |
| Hedge against a single-model mistake | **`agent_ensemble`**. |
| Multiple approaches worth trying in parallel | **`agent_ensemble_pairwise`**. |
| Agents need to coordinate mid-run | **Message board** (enabled automatically in ensembles). |
| A fixed sequence of tools that the root should not see individually | **`ToolCombo(return_history=False)`**. |

## Related References

- [Customize -> Agent Ensembles](../../get-started/customize/agent-ensemble.md): configuration.
- [`[agent_ensemble]` reference](../../reference/configuration/agent-ensemble.md): fields.
- [Agent Ensemble example](../../get-started/examples/agents_with_features/sample_agent_ensemble.md): runnable.
- [Dynamic Sub-Agents example](../../get-started/examples/agents_with_features/sample_dynamic_subagent.md): runnable.
- [Tool Combo example](../../get-started/examples/agents_with_features/sample_tool_combo.md): runnable.
