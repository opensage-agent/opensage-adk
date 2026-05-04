# `[build]` Reference

Field reference for the `[build]` section. For how to use it, see the [Extra Build Configurations guide](../../get-started/customize/build.md).

## Fields

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `poc_dir` | `string` | Directory path for proof-of-concept code | `None` |
| `compile_command` | `string` | Command to compile the target program | `None` |
| `run_command` | `string` | Command to run the target program | `None` |
| `target_type` | `string` | Type of target (e.g., `"default"`, `"binary"`) | `None` |
| `target_binary` | `string` | Path to target binary | `None` |
