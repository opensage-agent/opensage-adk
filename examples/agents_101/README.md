# SecAgentFramework Examples 101

Examples demonstrating key SecAgentFramework features.

## Examples

### sample_agent
Basic agent with simple tools.
**Run**: `adk web`

### sample_tool_combo
Sequential tool execution with ToolCombo.
**Run**: `adk web`

### sample_reward_func
Reward logging for tools and agents.
**Run**: `adk web`

### sample_dynamic_subagent
Dynamic sub-agent creation at runtime.
**Run**: `adk web`

### mcp_agent
MCP integration with filesystem server.
**Run**:
- Terminal 1: `./fs_server.sh`
- Terminal 2: `adk web`

### mcp_agent/sample_manual_interaction
Manual MCP server interaction demo.
**Run**:
- Terminal 1: `./fs_server.sh`
- Terminal 2: `./connect.sh`
- Terminal 3: Run commands from `manual_request_examples`

### mcp_agent_local
Local MCP server with stdio connection.
**Run**: `adk web`

### mcp_server_tool
Custom MCP server integration.
**Run**:
- Terminal 1: `python my_mcp_server.py`
- Terminal 2: `adk web`

## Quick Start
```bash
cd SecAgentFramework/example_agents_101/<example_name>
# Follow the Run instructions above
```
