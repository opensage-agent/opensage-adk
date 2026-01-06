# Common Patterns

## Pattern: Agent Skill (Bash Script Tool)

Agent Skills are bash scripts organized in a structured format with `SKILL.md` metadata:

```
src/aigise/bash_tools/
└── category/
    └── tool-name/
        ├── SKILL.md
        └── scripts/
            └── tool_script.sh
```

**SKILL.md example:**

```markdown
---
name: my-tool
description: Tool description
---

# My Tool

## Parameters

### input (required, positional position 0)

**Type**: `str`

Input parameter description.

### --option (optional, named parameter)

**Type**: `str`

Option description.

## Requires Sandbox

main

## Timeout

60 seconds
```

**Bash script example:**

```bash
#!/bin/bash

INPUT="$1"
OPTION=""

# Parse named parameters
while [[ $# -gt 0 ]]; do
    case $1 in
        --option)
            OPTION="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

# Tool logic
RESULT=$(process "$INPUT" "$OPTION")

# Return JSON
echo "{\"success\": true, \"result\": \"$RESULT\"}"
```

## Pattern: Multi-Sandbox Skill

For tools that require multiple sandboxes, specify in `SKILL.md`:

```markdown
## Requires Sandbox

main, joern
```

The framework automatically ensures both sandboxes are available before executing the tool.

## Pattern: MCP Toolset

MCP toolsets provide integration with external services:

```python
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseConnectionParams
from aigise.toolbox.decorators import requires_sandbox, safe_tool_execution
from aigise.utils.agent_utils import get_mcp_url_from_session_id

@safe_tool_execution
@requires_sandbox("gdb_mcp")
def get_gdb_toolset(aigise_session_id: str) -> MCPToolset:
    """Create MCPToolset for GDB debugging."""
    url = get_mcp_url_from_session_id("gdb_mcp", aigise_session_id)
    return MCPToolset(connection_params=SseConnectionParams(url=url))
```

## Pattern: Dynamic Tool Discovery

Tools are automatically discovered from:
- `src/aigise/bash_tools/` (built-in tools)
- `~/.local/plugins/aigise/tools/` (user plugins)

The framework scans for `SKILL.md` files and loads them automatically. No manual registration needed.

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
