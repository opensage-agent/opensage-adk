---
name: search-symbol
description: Search the codebase inside the running container for the definition of a given symbol.
should_run_in_sandbox: main
returns_json: false
---

# Search Symbol Tool

Search the codebase inside the running container for the definition of a given symbol.
If the symbol is a method in a class, do not include the class name in the symbol_name.
E.g. if the symbol name is "MyClass::myMethod", do not include "MyClass" in the symbol_name, only include "myMethod".
Do not include any punctuation such as parentheses in the symbol_name.

## Usage

```bash
/bash_tools/retrieval/search-symbol/scripts/search_symbol.sh "symbol_name"
```

## Parameters

### symbol_name (required, positional position 0)

**Type**: `str`

Symbol name (omit class/namespace prefix and omit parentheses).

## Return Value

Returns text output (ctags-like), one match per line:
`symbol_name<TAB>file_path<TAB>line_number;pattern`

## Requires Sandbox

main
