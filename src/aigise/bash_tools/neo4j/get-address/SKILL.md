---
name: get-neo4j-address
description: Get the IP address of the Neo4j container.
should_run_in_sandbox: main
returns_json: true

---

# Get Neo4j Address Tool

Tool to get the IP address of the Neo4j container. This is used to allow other containers to connect to the Neo4j database.

## Usage

```bash
scripts/get_neo4j_address.sh
```

## Parameters

None.

## Return Value

Returns a JSON object with key "result" containing the IP address.
Example: `{"result": "172.17.0.3"}`

## Requires Sandbox

neo4j
