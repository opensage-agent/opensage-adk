curl -sS -X POST "http://localhost:3001/message?sessionId=81f0e295-f405-47d7-be5b-ca24b01c4869" \ 
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "clientInfo": { "name": "hongwei-script", "version": "0.1" }
    }
  }'

  curl -sS -X POST "http://localhost:3001/message?sessionId=a532ed2b-4237-4d30-9997-a7440bf66a6d" \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": {}
  }'
