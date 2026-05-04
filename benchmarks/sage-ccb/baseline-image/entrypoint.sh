#!/usr/bin/env bash
set -euo pipefail

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

# ========================
# MCP Server 4: ghidra-mcp
# ========================
GHIDRA_MCP_PORT=${GHIDRA_MCP_PORT:-8004}
GHIDRA_PORT=${GHIDRA_PORT:-8089}
JAVA_OPTS=${JAVA_OPTS:-"-Xmx4g -XX:+UseG1GC"}
GHIDRA_HOME=${GHIDRA_HOME:-/opt/ghidra}

# Evaluate CLASSPATH for Ghidra
CLASSPATH="/opt/GhidraMCP.jar"
# Add Ghidra Framework JARs
for jar in ${GHIDRA_HOME}/Ghidra/Framework/*/lib/*.jar; do
    CLASSPATH="${CLASSPATH}:${jar}"
done

# Add Ghidra Feature JARs
for jar in ${GHIDRA_HOME}/Ghidra/Features/*/lib/*.jar; do
    CLASSPATH="${CLASSPATH}:${jar}"
done

# Add Ghidra Processor JARs
for jar in ${GHIDRA_HOME}/Ghidra/Processors/*/lib/*.jar; do
    CLASSPATH="${CLASSPATH}:${jar}"
done

java ${JAVA_OPTS} \
    -Dghidra.home=${GHIDRA_HOME} \
    -Dapplication.name=GhidraMCP \
    -classpath "${CLASSPATH}" \
    com.xebyte.headless.GhidraMCPHeadlessServer \
    --port 8089 --bind 127.0.0.1 > /root/ghidra-headless-server.log 2>&1 &

sleep 5
GHIDRA_MCP_URL=http://127.0.0.1:8089 python /opt/ghidra/bridge_mcp_ghidra.py \
    --transport sse --mcp-host 127.0.0.1 --mcp-port ${GHIDRA_MCP_PORT} > /root/ghidra-mcp-bridge.log 2>&1 &
sleep 10

# ============================
# Initialize claude MCP config
# ============================
claude mcp add --transport sse gdb http://localhost:1111
claude mcp add --transport http ida-pro http://localhost:${IDA_MCP_PORT}
claude mcp add --transport sse pyghidra http://localhost:${PYGHIDRA_MCP_PORT}
claude mcp add --transport sse ghidra http://localhost:${GHIDRA_MCP_PORT}

exec "$@"
