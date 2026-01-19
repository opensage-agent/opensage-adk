---
name: filemap
description: Collection of tools for filemap
should_run_in_sandbox: main
tools:
- name: filemap
  description: Print the contents of a Python file, skipping lengthy function and
    method definitions.
  script: filemap.py
  parameters:
  - name: file_path
    description: The path to the file to be read
    type: string
    required: true
    positional: true
    position: 0
  returns_json: false
---

# filemap

> [!NOTE]
> This tool group includes an `install.sh` script which may need to be run to set up dependencies.

## Requires Sandbox
main

## Tools

### filemap

Print the contents of a Python file, skipping lengthy function and method definitions.

#### Usage

```bash
/bash_tools/swe_agent/filemap/scripts/filemap.py <FILE_PATH>
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| file_path | string | The path to the file to be read | true |
