# Gdb MCP
- some codes refered from [pwno-mcp](https://github.com/pwno-io/pwno-mcp)

# Available Tools
 - execute: Execute arbitrary GDB/pwndbg command
 - set_file: Load a binary file for debugging
 - run: Run the loaded binary,requires at least one enabled breakpoint to be set before running.
 - step_control: Execute stepping commands (continue, n, s, ni, si).
 - finish: Run until the current function returns.
 - get_context: Get debugging context (registers, stack, disassembly, code, backtrace).
 - set_breakpoint: Set a breakpoint at the specified location.
 - list_breakpoints: List all breakpoints.
 - delete_breakpoint: Delete a breakpoint by number.
 - toggle_breakpoint: Toggle a breakpoint's state.
 - get_memory: Read memory at the specified address.
 - disassemble: Disassemble the specified address.
 - get_session_info: Get current debugging session information.

# Install
```bash
apt-get update && apt-get install -y curl python3 wget
curl -LsSf https://astral.sh/uv/install.sh | sh
curl -qsL 'https://install.pwndbg.re' | sh -s -- -t pwndbg-gdb
# export PATH="/root/.local/bin:$PATH"
# disable pwndbg colors
# https://github.com/pwndbg/pwndbg/blob/dev/docs/tutorials/env-vars.md?plain=1#L9
export PWNDBG_DISABLE_COLORS=1 NO_COLORS=1
# install dependencies
uv venv --python 3.12
uv pip install mcp
uv pip install pygdbmi
```

# Docker
[Dockerfile](./Dockerfile)
```bash
docker build -t gdb_mcp .
docker run -p 1111:1111 -ti --rm gdb_mcp
```
The baseImage is n132/arvo:63824-vul.

# How to use
```bash
GDB_MCP_SSE_PORT=1234 uv run gdb_mcp_server.py
```
