# Static Vulnerability Detection Agent

**Source:** [`agent_library/agents/vul_agent_static_tools`](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents/vul_agent_static_tools)

## What This Agent Does

Given a target C/C++ function, this agent decides whether a vulnerability exists in it. The workflow is purely static:

1. Read the function.
2. Pull its **call-graph context** (callers, callees) from a pre-built Joern/Neo4j code-property graph.
3. Optionally scan related files and lines using `grep` and `list_functions_in_file`.
4. Reason about whether any of the common memory-safety or logic-bug patterns apply given the context.

No program is executed. No PoC is generated. The agent answers a single question: *does this function have a vulnerability, and if so, what kind?*

## Key Design

- **Joern-backed retrieval.** `search_function`, `get_caller`, `get_callee`, and `neo4j_query` hit a Joern-generated code-property graph stored in Neo4j. This gives exact, query-able call edges rather than LLM-guessed "probably called from".
- **Tight toolset.** No `finish_task` and no `create_subagent`. A higher-level orchestrator calls this agent as a sub-agent and provides those primitives.

## Agent Source

```python title="agent_library/agents/vul_agent_static_tools/agent.py"
from google.adk.models.lite_llm import LiteLlm

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.session import get_opensage_session
from opensage.toolbox.general.bash_tool import bash_tool_main
from benchmarks.common_tools.retrieval import (
    get_line_around_linenum_in_file, grep_tool, list_functions_in_file,
)
from benchmarks.common_tools.static_analysis import (
    get_callee, get_caller, neo4j_query, search_function,
)

def mk_agent(opensage_session_id="vulnerability-detection-agent-session"):
    opensage_session = get_opensage_session(opensage_session_id)
    return OpenSageAgent(
        name="vulnerability_detection_agent",
        model=LiteLlm(model="anthropic/claude-sonnet-4-5-20250929"),
        description="find vulnerabilities existing in this function.",
        instruction="""
        You are an expert in vulnerability research. Given a function, detect if any
        vulnerability exists in this function. You need to first understand the
        function, and extract context of this function (including caller, callee,
        etc). And then analyze if any vulnerability exists in this function based
        on the context.
        """,
        tools=[
            grep_tool,
            search_function,
            get_caller, get_callee, neo4j_query,
            list_functions_in_file, get_line_around_linenum_in_file,
            bash_tool_main,
        ],
    )
```

## Run It

The repo ships two configs: `vul_detect_config.toml` for self-hosted runs and `cybergym_vul_detect_config.toml` tailored for the CyberGym harness. Both declare a Joern sandbox and a Neo4j sandbox so the CPG queries have somewhere to hit.

```bash
uv run opensage web \
  --agent agent_library/agents/vul_agent_static_tools \
  --config agent_library/agents/vul_agent_static_tools/vul_detect_config.toml \
  --port 8000
```

## Prerequisites

- Joern container (`[sandbox.sandboxes.joern]` in the config) to produce the CPG.
- Neo4j container to store and serve the CPG for `neo4j_query`.
- Source code of the target program mounted at `/shared/code`.
