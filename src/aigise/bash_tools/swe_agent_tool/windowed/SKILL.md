---
name: windowed
description: Collection of tools for windowed
should_run_in_sandbox: main
tools:
- name: goto
  description: moves the window to show <line_number>
  script: goto.py
  parameters:
  - name: line_number
    description: the line number to move the window to
    type: integer
    required: true
    positional: true
    position: 0
  returns_json: false
- name: open
  description: opens the file at the given path in the editor. If line_number is provided,
    the window will be move to include that line
  script: open.py
  parameters:
  - name: path
    description: the path to the file to open
    type: string
    required: true
    positional: true
    position: 0
  - name: line_number
    description: the line number to move the window to (if not provided, the window
      will start at the top of the file)
    type: integer
    required: false
    positional: true
    position: 1
  returns_json: false
- name: create
  description: creates and opens a new file with the given name
  script: create.py
  parameters:
  - name: filename
    description: the name of the file to create
    type: string
    required: true
    positional: true
    position: 0
  returns_json: false
- name: scroll_up
  description: moves the window up {WINDOW} lines
  script: scroll_up.py
  parameters: []
  returns_json: false
- name: scroll_down
  description: moves the window down {WINDOW} lines
  script: scroll_down.py
  parameters: []
  returns_json: false
---

# windowed

> [!NOTE]
> This tool group includes an `install.sh` script which may need to be run to set up dependencies.

## Requires Sandbox
main

## Tools

### goto

moves the window to show <line_number>

#### Usage

```bash
goto <LINE_NUMBER>
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| line_number | integer | the line number to move the window to | true |

### open

opens the file at the given path in the editor. If line_number is provided, the window will be move to include that line

#### Usage

```bash
open <PATH> [LINE_NUMBER]
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| path | string | the path to the file to open | true |
| line_number | integer | the line number to move the window to (if not provided, the window will start at the top of the file) | false |

### create

creates and opens a new file with the given name

#### Usage

```bash
create <FILENAME>
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| filename | string | the name of the file to create | true |

### scroll_up

moves the window up {WINDOW} lines

#### Usage

```bash
scroll_up
```

### scroll_down

moves the window down {WINDOW} lines

#### Usage

```bash
scroll_down
```
