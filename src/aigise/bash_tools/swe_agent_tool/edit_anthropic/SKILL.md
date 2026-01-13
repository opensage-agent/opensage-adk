---
name: edit_anthropic
description: Collection of tools for edit_anthropic
should_run_in_sandbox: main
tools:
- name: str_replace_editor
  description: 'Custom editing tool for viewing, creating and editing files * State
    is persistent across command calls and discussions with the user * If `path` is
    a file, `view` displays the result of applying `cat -n`. If `path` is a directory,
    `view` lists non-hidden files and directories up to 2 levels deep * The `create`
    command cannot be used if the specified `path` already exists as a file * If a
    `command` generates a long output, it will be truncated and marked with `<response
    clipped>` * The `undo_edit` command will revert the last edit made to the file
    at `path`

    Notes for using the `str_replace` command: * The `old_str` parameter should match
    EXACTLY one or more consecutive lines from the original file. Be mindful of whitespaces!
    * If the `old_str` parameter is not unique in the file, the replacement will not
    be performed. Make sure to include enough context in `old_str` to make it unique
    * The `new_str` parameter should contain the edited lines that should replace
    the `old_str`'
  script: str_replace_editor.py
  parameters:
  - name: command
    description: 'The commands to run. Allowed options are: `view`, `create`, `str_replace`,
      `insert`, `undo_edit`.'
    type: string
    required: true
    positional: true
    position: 0
  - name: path
    description: Absolute path to file or directory, e.g. `/testbed/file.py` or `/testbed`.
    type: string
    required: true
    positional: true
    position: 1
  - name: file_text
    description: Required parameter of `create` command, with the content of the file
      to be created.
    type: string
    required: false
    positional: true
    position: 2
  - name: old_str
    description: Required parameter of `str_replace` command containing the string
      in `path` to replace.
    type: string
    required: false
    positional: true
    position: 3
  - name: new_str
    description: Optional parameter of `str_replace` command containing the new string
      (if not given, no string will be added). Required parameter of `insert` command
      containing the string to insert.
    type: string
    required: false
    positional: true
    position: 4
  - name: insert_line
    description: Required parameter of `insert` command. The `new_str` will be inserted
      AFTER the line `insert_line` of `path`.
    type: integer
    required: false
    positional: true
    position: 5
  - name: view_range
    description: Optional parameter of `view` command when `path` points to a file.
      If none is given, the full file is shown. If provided, the file will be shown
      in the indicated line number range, e.g. [11, 12] will show lines 11 and 12.
      Indexing at 1 to start. Setting `[start_line, -1]` shows all lines from `start_line`
      to the end of the file.
    type: array
    required: false
    positional: true
    position: 6
  returns_json: false
---

# edit_anthropic

> [!NOTE]
> This tool group includes an `install.sh` script which may need to be run to set up dependencies.

## Requires Sandbox
main

## Tools

### str_replace_editor

Custom editing tool for viewing, creating and editing files * State is persistent across command calls and discussions with the user * If `path` is a file, `view` displays the result of applying `cat -n`. If `path` is a directory, `view` lists non-hidden files and directories up to 2 levels deep * The `create` command cannot be used if the specified `path` already exists as a file * If a `command` generates a long output, it will be truncated and marked with `<response clipped>` * The `undo_edit` command will revert the last edit made to the file at `path`
Notes for using the `str_replace` command: * The `old_str` parameter should match EXACTLY one or more consecutive lines from the original file. Be mindful of whitespaces! * If the `old_str` parameter is not unique in the file, the replacement will not be performed. Make sure to include enough context in `old_str` to make it unique * The `new_str` parameter should contain the edited lines that should replace the `old_str`

#### Usage

```bash
str_replace_editor <COMMAND> <PATH> [FILE_TEXT] [OLD_STR] [NEW_STR] [INSERT_LINE] [VIEW_RANGE]
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| command | string | The commands to run. Allowed options are: `view`, `create`, `str_replace`, `insert`, `undo_edit`. | true |
| path | string | Absolute path to file or directory, e.g. `/testbed/file.py` or `/testbed`. | true |
| file_text | string | Required parameter of `create` command, with the content of the file to be created. | false |
| old_str | string | Required parameter of `str_replace` command containing the string in `path` to replace. | false |
| new_str | string | Optional parameter of `str_replace` command containing the new string (if not given, no string will be added). Required parameter of `insert` command containing the string to insert. | false |
| insert_line | integer | Required parameter of `insert` command. The `new_str` will be inserted AFTER the line `insert_line` of `path`. | false |
| view_range | array | Optional parameter of `view` command when `path` points to a file. If none is given, the full file is shown. If provided, the file will be shown in the indicated line number range, e.g. [11, 12] will show lines 11 and 12. Indexing at 1 to start. Setting `[start_line, -1]` shows all lines from `start_line` to the end of the file. | false |
