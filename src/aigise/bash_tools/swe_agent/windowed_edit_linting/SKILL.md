---
name: windowed_edit_linting
description: Collection of tools for windowed_edit_linting
should_run_in_sandbox: main
tools:
- name: edit
  description: 'Replaces lines <start_line> through <end_line> (inclusive) with the
    given text in the open file. All of the <replacement text> will be entered, so
    make sure your indentation is formatted properly.

    Please note that THIS COMMAND REQUIRES PROPER INDENTATION. If you''d like to add
    the line ''        print(x)'' you must fully write that out, with all those spaces
    before the code!'
  script: edit.py
  parameters:
  - name: start_line
    description: the line number to start the edit at
    type: integer
    required: true
    positional: true
    position: 0
  - name: end_line
    description: the line number to end the edit at (inclusive)
    type: integer
    required: true
    positional: true
    position: 1
  - name: replacement_text
    description: the text to replace the current selection with
    type: string
    required: true
    positional: true
    position: 2
  returns_json: false
---

# windowed_edit_linting

> [!NOTE]
> This tool group includes an `install.sh` script which may need to be run to set up dependencies.

## Requires Sandbox
main

## Tools

### edit

Replaces lines <start_line> through <end_line> (inclusive) with the given text in the open file. All of the <replacement text> will be entered, so make sure your indentation is formatted properly.
Please note that THIS COMMAND REQUIRES PROPER INDENTATION. If you'd like to add the line '        print(x)' you must fully write that out, with all those spaces before the code!

#### Usage

```bash
/bash_tools/swe_agent/windowed_edit_linting/scripts/edit.py <START_LINE> <END_LINE> <REPLACEMENT_TEXT>
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| start_line | integer | the line number to start the edit at | true |
| end_line | integer | the line number to end the edit at (inclusive) | true |
| replacement_text | string | the text to replace the current selection with | true |
