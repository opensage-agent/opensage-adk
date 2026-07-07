#!/usr/bin/env bash
set -euo pipefail

# =====================
# MCP Server 1: gdb-mcp
# =====================
cd /app
PYTHONPATH=/app uv run python -m gdb_mcp.gdb_mcp_server > /root/gdb-mcp.log 2>&1 &
cd /workspace

# ========================
# MCP Server 2: idalib-mcp
# ========================
IDA_MCP_PORT=${IDA_MCP_PORT:-8002}
uv run idalib-mcp --host 127.0.0.1 --port ${IDA_MCP_PORT} > /root/idalib-mcp.log 2>&1 &

# ==========================
# MCP Server 3: pyghidra-mcp
# ==========================
PYGHIDRA_MCP_PORT=${PYGHIDRA_MCP_PORT:-8003}
GHIDRA_INSTALL_DIR="/opt/ghidra/" pyghidra-mcp \
    -t sse -o 127.0.0.1 -p ${PYGHIDRA_MCP_PORT} > /root/pyghidra-mcp.log 2>&1 &

# ============================
# Initialize claude MCP config
# ============================
claude mcp add --transport sse gdb http://localhost:1111
claude mcp add --transport http ida-pro http://localhost:${IDA_MCP_PORT}
claude mcp add --transport sse pyghidra http://localhost:${PYGHIDRA_MCP_PORT}

exec "$@"
