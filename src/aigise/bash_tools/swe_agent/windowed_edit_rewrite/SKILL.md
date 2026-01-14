---
name: windowed_edit_rewrite
description: Collection of tools for windowed_edit_rewrite
should_run_in_sandbox: main
tools:
- name: edit
  description: Replace the currently displayed lines with <text>.
  script: edit.py
  parameters:
  - name: text
    description: the text to replace the currently displayed lines with
    type: string
    required: true
    positional: true
    position: 0
  returns_json: false
---

# windowed_edit_rewrite

> [!NOTE]
> This tool group includes an `install.sh` script which may need to be run to set up dependencies.

## Requires Sandbox
main

## Tools

### edit

Replace the currently displayed lines with <text>.

#### Usage

```bash
edit <TEXT>
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| text | string | the text to replace the currently displayed lines with | true |
