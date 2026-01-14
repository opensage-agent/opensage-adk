---
name: registry
description: Tools for reading and writing to the agent registry.
should_run_in_sandbox: main
tools:
- name: read_registry
  description: Reads a value from the registry.
  script: read_registry.sh
  parameters:
  - name: key
    description: The key to read from the registry.
    type: string
    required: true
    positional: true
    position: 0
  - name: default
    description: The default value to return if the key is not found.
    type: string
    required: false
    positional: true
    position: 1
  returns_json: false
- name: write_registry
  description: Writes a value to the registry.
  script: write_registry.sh
  parameters:
  - name: key
    description: The key to write to the registry.
    type: string
    required: true
    positional: true
    position: 0
  - name: value
    description: The value to write to the registry.
    type: string
    required: true
    positional: true
    position: 1
  returns_json: false
---

# registry

> [!NOTE]
> This tool group includes an `install.sh` script which may need to be run to set up dependencies.

## Requires Sandbox
main

## Tools

### read_registry

Reads a value from the registry.

#### Usage

```bash
read_registry <KEY> [DEFAULT]
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| key | string | The key to read from the registry. | true |
| default | string | The default value to return if the key is not found. | false |

### write_registry

Writes a value to the registry.

#### Usage

```bash
write_registry <KEY> <VALUE>
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| key | string | The key to write to the registry. | true |
| value | string | The value to write to the registry. | true |

## Requires Sandbox
main

## Priority

0
