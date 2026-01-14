#!/usr/bin/env bash
set -euo pipefail

# Adjust to your FastAPI server address/port
API_BASE="localhost:18824"

# Download agent archive
curl -fSL "${API_BASE}/agent.tar.gz" -o agent.tar.gz

# Create Neo4j container
resp="$(curl -fSL -X POST "${API_BASE}/create_neo4j" -H 'Content-Type: application/json')"
echo "Create response: $resp"

# Parse ports (jq if available, otherwise python)
if command -v jq >/dev/null 2>&1; then
export NEO4J_HOST="$(printf '%s' "$resp" | jq -r '.host')"
export NEO4J_HTTP_PORT="$(printf '%s' "$resp" | jq -r '.http_port')"
export NEO4J_BOLT_PORT="$(printf '%s' "$resp" | jq -r '.bolt_port')"
else
export NEO4J_HOST="$(python - <<'PY'
import json,sys
print(json.loads(sys.stdin.read())["host"])
PY
<<<"$resp")"
export NEO4J_HTTP_PORT="$(python - <<'PY'
import json,sys
print(json.loads(sys.stdin.read())["http_port"])
PY
<<<"$resp")"
export NEO4J_BOLT_PORT="$(python - <<'PY'
import json,sys
print(json.loads(sys.stdin.read())["bolt_port"])
PY
<<<"$resp")"
fi

echo "NEO4J_HOST=$NEO4J_HOST"
echo "NEO4J_HTTP_PORT=$NEO4J_HTTP_PORT"
echo "NEO4J_BOLT_PORT=$NEO4J_BOLT_PORT"
