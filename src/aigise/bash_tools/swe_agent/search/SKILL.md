---
name: search
description: Collection of tools for search
should_run_in_sandbox: main
tools:
- name: find_file
  description: finds all files with the given name or pattern in dir. If dir is not
    provided, searches in the current directory
  script: find_file.sh
  parameters:
  - name: file_name
    description: the name of the file or pattern to search for. supports shell-style
      wildcards (e.g. *.py)
    type: string
    required: true
    positional: true
    position: 0
  - name: dir
    description: the directory to search in (if not provided, searches in the current
      directory)
    type: string
    required: false
    positional: true
    position: 1
  returns_json: false
- name: search_dir
  description: searches for search_term in all files in dir. If dir is not provided,
    searches in the current directory
  script: search_dir.sh
  parameters:
  - name: search_term
    description: the term to search for
    type: string
    required: true
    positional: true
    position: 0
  - name: dir
    description: the directory to search in (if not provided, searches in the current
      directory)
    type: string
    required: false
    positional: true
    position: 1
  returns_json: false
- name: search_file
  description: searches for search_term in file. If file is not provided, searches
    in the current open file
  script: search_file.sh
  parameters:
  - name: search_term
    description: the term to search for
    type: string
    required: true
    positional: true
    position: 0
  - name: file
    description: the file to search in (if not provided, searches in the current open
      file)
    type: string
    required: false
    positional: true
    position: 1
  returns_json: false
---

# search

> [!NOTE]
> This tool group includes an `install.sh` script which may need to be run to set up dependencies.

## Requires Sandbox
main

## Tools

### find_file

finds all files with the given name or pattern in dir. If dir is not provided, searches in the current directory

#### Usage

```bash
/bash_tools/swe_agent/search/scripts/find_file.sh <FILE_NAME> [DIR]
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| file_name | string | the name of the file or pattern to search for. supports shell-style wildcards (e.g. *.py) | true |
| dir | string | the directory to search in (if not provided, searches in the current directory) | false |

### search_dir

searches for search_term in all files in dir. If dir is not provided, searches in the current directory

#### Usage

```bash
/bash_tools/swe_agent/search/scripts/search_dir.sh <SEARCH_TERM> [DIR]
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| search_term | string | the term to search for | true |
| dir | string | the directory to search in (if not provided, searches in the current directory) | false |

### search_file

searches for search_term in file. If file is not provided, searches in the current open file

#### Usage

```bash
/bash_tools/swe_agent/search/scripts/search_file.sh <SEARCH_TERM> [FILE]
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| search_term | string | the term to search for | true |
| file | string | the file to search in (if not provided, searches in the current open file) | false |
