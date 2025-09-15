# PDB MCP

Some codes refered from [mcp-pdb](https://github.com/samefarrar/mcp-pdb)

# Available Tools

| Tool | Description |
|------|-------------|
| `start_debug(file_path, use_pytest, args)` | Start a debugging session for a Python file |
| `send_pdb_command(command)` | Send a command to the running PDB instance |
| `set_breakpoint(file_path, line_number)` | Set a breakpoint at a specific line |
| `clear_breakpoint(file_path, line_number)` | Clear a breakpoint at a specific line |
| `list_breakpoints()` | List all current breakpoints |
| `restart_debug()` | Restart the current debugging session |
| `examine_variable(variable_name)` | Get detailed information about a variable |
| `get_debug_status()` | Show the current state of the debugging session |
| `end_debug()` | End the current debugging session |

# How to use
```bash
PDB_MCP_SSE_PORT=1112 uv run pdb_mcp_server.py
```
