#!/usr/bin/env bash
set -euo pipefail

MCP_STARTUP_TIMEOUT=${MCP_STARTUP_TIMEOUT:-120}

wait_for_port() {
    local name="$1"
    local host="$2"
    local port="$3"
    local log_path="$4"
    local deadline=$((SECONDS + MCP_STARTUP_TIMEOUT))

    until (: > "/dev/tcp/${host}/${port}") >/dev/null 2>&1; do
        if [ "${SECONDS}" -ge "${deadline}" ]; then
            echo "Timed out waiting for ${name} MCP server on ${host}:${port}" >&2
            if [ -f "${log_path}" ]; then
                echo "Last ${name} MCP log lines:" >&2
                tail -n 80 "${log_path}" >&2 || true
            fi
            exit 1
        fi
        sleep 1
    done
}

# =====================
# MCP Server 1: gdb-mcp
# =====================
cd /app
uv run python /app/gdb_mcp/gdb_mcp_server.py > /root/gdb-mcp.log 2>&1 &
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

wait_for_port gdb 127.0.0.1 1111 /root/gdb-mcp.log
wait_for_port ida-pro 127.0.0.1 ${IDA_MCP_PORT} /root/idalib-mcp.log
wait_for_port pyghidra 127.0.0.1 ${PYGHIDRA_MCP_PORT} /root/pyghidra-mcp.log

# ============================
# Initialize claude MCP config
# ============================
GHIDRA_MCP_PORT=${GHIDRA_MCP_PORT:-8004}
claude mcp add --transport sse gdb http://localhost:1111
claude mcp add --transport http ida-pro http://localhost:${IDA_MCP_PORT}
claude mcp add --transport sse pyghidra http://localhost:${PYGHIDRA_MCP_PORT}
claude mcp add --transport sse ghidra http://localhost:${GHIDRA_MCP_PORT}

exec "$@"
