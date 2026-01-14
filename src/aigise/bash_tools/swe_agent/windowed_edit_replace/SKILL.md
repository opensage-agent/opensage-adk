---
name: windowed_edit_replace
description: Collection of tools for windowed_edit_replace
should_run_in_sandbox: main
tools:
- name: edit
  description: "Replace first occurrence of <search> with <replace> in the currently\
    \ displayed lines. If replace-all is True , replace all occurrences of <search>\
    \ with <replace>.\nFor example, if you are looking at this file:\ndef fct():\n\
    \    print(\"Hello world\")\n\nand you want to edit the file to read:\ndef fct():\n\
    \    print(\"Hello\")\n    print(\"world\")\n\nyou can search for `Hello world`\
    \ and replace with `\"Hello\"\\n    print(\"world\")` (note the extra spaces before\
    \ the print statement!).\nTips:\n1. Always include proper whitespace/indentation\
    \ 2. When you are adding an if/with/try statement, you need to INDENT the block\
    \ that follows, so make sure to include it in both your search and replace strings!\
    \ 3. If you are wrapping code in a try statement, make sure to also add an 'except'\
    \ or 'finally' block.\nBefore every edit, please\n1. Explain the code you want\
    \ to edit and why it is causing the problem 2. Explain the edit you want to make\
    \ and how it fixes the problem 3. Explain how the edit does not break existing\
    \ functionality"
  script: edit.py
  parameters:
  - name: search
    description: the text to search for (make sure to include proper whitespace if
      needed)
    type: string
    required: true
    positional: true
    position: 0
  - name: replace
    description: the text to replace the search with (make sure to include proper
      whitespace if needed)
    type: string
    required: true
    positional: true
    position: 1
  - name: replace-all
    description: replace all occurrences rather than the first occurrence within the
      displayed lines
    type: boolean
    required: false
    positional: true
    position: 2
  returns_json: false
- name: insert
  description: Insert <text> at the end of the currently opened file or after <line>
    if specified.
  script: insert.py
  parameters:
  - name: text
    description: the text to insert
    type: string
    required: true
    positional: true
    position: 0
  - name: line
    description: the line number to insert the text as new lines after
    type: integer
    required: false
    positional: true
    position: 1
  returns_json: false
---

# windowed_edit_replace

> [!NOTE]
> This tool group includes an `install.sh` script which may need to be run to set up dependencies.

## Requires Sandbox
main

## Tools

### edit

Replace first occurrence of <search> with <replace> in the currently displayed lines. If replace-all is True , replace all occurrences of <search> with <replace>.
For example, if you are looking at this file:
def fct():
    print("Hello world")

and you want to edit the file to read:
def fct():
    print("Hello")
    print("world")

you can search for `Hello world` and replace with `"Hello"\n    print("world")` (note the extra spaces before the print statement!).
Tips:
1. Always include proper whitespace/indentation 2. When you are adding an if/with/try statement, you need to INDENT the block that follows, so make sure to include it in both your search and replace strings! 3. If you are wrapping code in a try statement, make sure to also add an 'except' or 'finally' block.
Before every edit, please
1. Explain the code you want to edit and why it is causing the problem 2. Explain the edit you want to make and how it fixes the problem 3. Explain how the edit does not break existing functionality

#### Usage

```bash
edit <SEARCH> <REPLACE> [REPLACE-ALL]
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| search | string | the text to search for (make sure to include proper whitespace if needed) | true |
| replace | string | the text to replace the search with (make sure to include proper whitespace if needed) | true |
| replace-all | boolean | replace all occurrences rather than the first occurrence within the displayed lines | false |

### insert

Insert <text> at the end of the currently opened file or after <line> if specified.

#### Usage

```bash
insert <TEXT> [LINE]
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| text | string | the text to insert | true |
| line | integer | the line number to insert the text as new lines after | false |
