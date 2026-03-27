#!/usr/bin/env bash
set -euo pipefail

IDA_MCP_PORT=${IDA_MCP_PORT:-8001}

uv run idalib-mcp --host 0.0.0.0 --port ${IDA_MCP_PORT} > /root/idalib-mcp.log 2>&1 &
exec "$@"