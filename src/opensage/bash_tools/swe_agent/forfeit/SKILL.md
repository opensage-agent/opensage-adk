---
name: forfeit
description: Collection of tools for forfeit
should_run_in_sandbox: main
tools:
- name: exit_forfeit
  description: Give up on the current challenge and terminate the session.
  script: exit_forfeit.sh
  parameters: []
  returns_json: false
---

# forfeit

## Requires Sandbox
main

## Tools

### exit_forfeit

Give up on the current challenge and terminate the session.

#### Usage

```bash
/bash_tools/swe_agent/forfeit/scripts/exit_forfeit.sh
```
