# Common Patterns

## Pattern: Session-Scoped Tool

```python
@requires_sandbox("main")
async def my_tool(param: str, tool_context: ToolContext) -> dict:
    from aigise import get_aigise_session
    from aigise.utils.agent_utils import get_aigise_session_id_from_context

    session_id = get_aigise_session_id_from_context(tool_context)
    session = get_aigise_session(session_id)
    sandbox = session.sandboxes.get_sandbox("main")
    # ... use sandbox ...
```

## Pattern: Multi-Sandbox Tool

```python
@requires_sandbox("main", "joern")
async def analyze_code(path: str, tool_context: ToolContext) -> dict:
    session_id = get_aigise_session_id_from_context(tool_context)
    session = get_aigise_session(session_id)

    main_sandbox = session.sandboxes.get_sandbox("main")
    joern_sandbox = session.sandboxes.get_sandbox("joern")
    # ... use both sandboxes ...
```

## Pattern: Dynamic Tool Loading

```python
# Tools are automatically loaded from:
# - src/aigise/sandbox_scripts/bash_tools/
# - ~/.local/plugins/aigise/tools/
# Each tool directory should have SKILL.md with metadata
```

## Pattern: Agent Composition

```python
def mk_agent(aigise_session_id: str) -> AigiseAgent:
    sub_agent = AigiseAgent(...)
    sub_agent_tool = AgentTool(agent=sub_agent)

    root_agent = AigiseAgent(
        tools=[sub_agent_tool, ...],
        sub_agents=[...]
    )
    return root_agent
```

## Pattern: Code Understanding Agent with Memory Caching

The Code Understanding Agent is a utility agent that caches question-answer pairs in Neo4j to avoid redundant computation. It can be used as a tool by other agents.

**Basic Usage:**

```python
from examples.agents.code_understanding_agent import create_code_understanding_agent_tool
from google.adk.models import BaseLlm, Gemini
from aigise.agents.aigise_agent import AigiseAgent

# Create code understanding agent tool
code_tool = create_code_understanding_agent_tool(
    model=Gemini(model="gemini-2.5-flash"),
    name="code_understanding_agent",
)

# Use in another agent
orchestrator = AigiseAgent(
    name="orchestrator",
    model=Gemini(model="gemini-2.5-flash"),
    tools=[code_tool, other_tools...],
)
```

**How It Works:**

1. **Cache Lookup**: Before answering a question, the agent first checks for semantically similar cached answers using `lookup_similar_answers`
2. **Smart Reuse**: If a highly similar answer exists (similarity > 0.85), it reuses the cached answer directly
3. **Fresh Analysis**: If no similar answer exists, it performs fresh code analysis using available tools
4. **Cache Storage**: After generating a new answer, it stores it using `cache_qa_pair` for future use

**Available Tools:**

- `lookup_similar_answers`: Find semantically similar cached Q&A pairs
- `cache_qa_pair`: Store a new Q&A pair in the cache
- `list_cached_questions`: Browse cached questions
- `get_cached_answer_by_id`: Retrieve full answer content by ID
- Code analysis tools: `search_function`, `grep_tool`, `list_functions_in_file`, etc.

**Benefits:**

- Reduces redundant computation for repeated or similar questions
- Improves response time for cached queries
- Maintains context across multiple agent invocations
- Works seamlessly with Neo4j-based memory system

See `examples/agents/code_understanding_agent/README.md` for more details.

## See Also

- [Best Practices](Best-Practices.md) - Best practices
- [Development Guides](Development-Guides.md) - Development guides
- [Core Concepts](Core-Concepts.md) - Core concepts
