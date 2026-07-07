# Extra Build Configurations

**Section:** `[build]`

Tells OpenSage how to compile and run a **target program** as the external artifact the agent is meant to analyze, patch, or exploit. Mostly used by benchmark agents (CTF, debugging, patching) that repeatedly rebuild and execute user-supplied code. You do not need this section for conversational or tool-use agents.

## Example

```toml title="config.toml"
[build]
poc_dir = "/tmp/poc"
compile_command = "gcc -o target target.c"
run_command = "./target"
target_type = "binary"
target_binary = "/tmp/poc/target"
```

- `compile_command` and `run_command` are shell strings; they execute inside the main sandbox.
- `poc_dir` is where the agent writes candidate proof-of-concept inputs that it wants `run_command` to consume.
- `target_type` / `target_binary` let tools that inspect the built artifact know what to look at.

---

See the [`[build]` field reference](../../reference/configuration/build.md) for all fields.
