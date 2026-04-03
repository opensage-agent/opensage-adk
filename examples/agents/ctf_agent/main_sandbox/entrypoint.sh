#!/usr/bin/env bash
set -euo pipefail

# ========================
# MCP Server 1: idalib-mcp
# ========================
IDA_MCP_PORT=${IDA_MCP_PORT:-8002}
uv run idalib-mcp --host 0.0.0.0 --port ${IDA_MCP_PORT} > /root/idalib-mcp.log 2>&1 &

# ==========================
# MCP Server 2: pyghidra-mcp
# ==========================
PYGHIDRA_MCP_PORT=${PYGHIDRA_MCP_PORT:-8003}
GHIDRA_INSTALL_DIR="/opt/ghidra/" pyghidra-mcp \
    -t sse -o 0.0.0.0 -p ${PYGHIDRA_MCP_PORT} > /root/pyghidra-mcp.log 2>&1 &

# ========================
# MCP Server 3: ghidra-mcp
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
    --transport sse --mcp-host 0.0.0.0 --mcp-port ${GHIDRA_MCP_PORT} > /root/ghidra-mcp-bridge.log 2>&1 &
sleep 3

exec "$@"
