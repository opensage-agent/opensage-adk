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
scripts/search_symbol.sh "symbol_name"
```

## Parameters

- `symbol_name`: The name of the symbol to search for.

## Return Value

Returns text output in ctags format. Each line contains symbol definition information in the format:
`symbol_name	file_path	line_number;pattern`

If no exact match is found, fuzzy matches are shown with a warning message.

## Requires Sandbox

main
