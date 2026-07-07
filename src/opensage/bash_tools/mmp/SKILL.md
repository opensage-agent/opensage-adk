---
name: mmp
description: Standalone `mmp` executable for managing multiple MCP servers from a loaded gateway config.
should_run_in_sandbox: main
---

# MMP

Use the standalone `mmp` executable inside the main sandbox. `mmp` is a CLI-first MCP multiplexing proxy that supports `http`, `sse`, and `stdio` transports, persistent session handles, and session reuse across calls.

## Usage

```bash
mmp load gateway.toml
```

```bash
mmp ls
```

```bash
mmp call demo-http tools/list
```

```bash
mmp call demo-stdio echo --arg '{"value":"hello"}'
```

```bash
mmp call --id 9de6d26 resources/list
```

```bash
mmp ps
```

```bash
mmp rm --id 9de6d26
```

```bash
mmp rm --all
```

## Parameters

### load (optional, positional position 0)

**Type**: `subcommand`

Load a TOML gateway config file and persist it as the active config for later `mmp` commands.

### ls (optional, positional position 0)

**Type**: `subcommand`

List servers from the active loaded config.

### call (optional, positional position 0)

**Type**: `subcommand`

Create a new persistent session or resume an existing session and call an MCP method.

### ps (optional, positional position 0)

**Type**: `subcommand`

List tracked persistent sessions and their liveness.

### rm (optional, positional position 0)

**Type**: `subcommand`

Remove one persistent session or all sessions.

### path (optional, positional position 1)

**Type**: `str`

Gateway TOML path for `mmp load`.

### server (optional, positional position 1)

**Type**: `str`

Server name from the active loaded config for `mmp call <server> <method>`.

### method (optional, positional position 2)

**Type**: `str`

MCP method name such as `tools/list` or `resources/list`.

### --arg (optional, named parameter)

**Type**: `str`

JSON string passed as arguments to `mmp call`.

### --id (optional, named parameter)

**Type**: `str`

Existing session handle to resume with `mmp call --id <id> <method>` or remove with `mmp rm --id <id>`.

### --all (optional, flag)

**Type**: `bool` (default: `false`)

Remove all tracked sessions with `mmp rm --all`.

## Return Value

Returns the underlying `mmp` CLI stdout/stderr. `mmp call` often returns JSON, while `load`, `ls`, `ps`, and `rm` usually return human-readable text tables or status lines.

## Requires Sandbox

main

## Timeout

Default timeout: 60 seconds
