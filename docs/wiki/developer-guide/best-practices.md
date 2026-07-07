---
icon: lucide/check-check
---

# Best Practices

Patterns that have held up across OpenSage-ADK agents and tools. Each entry names the rule and, where helpful, the OpenSage call or filesystem convention it relies on.

## Session Management

- Use `opensage.get_opensage_session()` instead of constructing sessions directly. The helper handles config expansion and registry bookkeeping.
- Clean up when finished: `opensage.cleanup_opensage_session(session_id)` cancels agent tasks and, when `auto_cleanup` is set, releases sandbox resources.
- Use a unique `session_id` per run. Two runs sharing one ID will overwrite each other's sandbox state and ADK session records.

## Agent Development

- Keep each agent focused on a single responsibility. Compose larger workflows from sub-agents rather than packing one agent with many roles.
- Use sub-agents (`AgentTool`) for structured hand-offs, and tool combos for related tool groups that share state.
- Document tool parameters and return values in docstrings; the LLM reads the docstring as the tool description.

## Tool Development

### Python Tools

- Keep functions small and focused on one operation.
- Write clear docstrings: the LLM uses them as tool descriptions, so vague docstrings produce vague tool use.
- Return structured values (dict or JSON-serializable objects) for any non-trivial output.

### Agent Skills (Bash Scripts)

- Lay out each Skill as a directory with `SKILL.md` plus a `scripts/` subdirectory.
- Document every parameter in `SKILL.md` with type and description.
- Set `should_run_in_sandbox` in the `SKILL.md` YAML frontmatter for executable skills.
- Declare sandbox requirements under a `## Requires Sandbox` section in `SKILL.md`.
- Return JSON for any structured result so downstream tools can parse it.
- Use proper exit codes: `0` for success, non-zero for errors.
- Handle errors gracefully and emit informative JSON error messages.
- Pick positional or named parameters per script needs, not per habit.
- Set realistic timeout values that match the expected runtime.

### MCP Toolsets

- Decorate MCP toolset getters with `@requires_sandbox` to declare the sandbox types the toolset needs.
- Return `OpenSageMCPToolset` instances from getter functions.
- Document connection parameters and usage in the toolset docstring.

## Configuration

- Use template variables (`${VAR_NAME}`) for environment-specific values rather than hard-coding paths and keys.
- Document non-obvious configuration options inline.
- Validate configuration during initialization so errors surface at startup, not on first tool call.
- Provide sensible defaults; the user should not need to set every field for a typical run.

## Code Organization

- Follow the existing module layout under `src/opensage/`.
- Use relative imports inside the package; use absolute imports in tests.
- Add docstrings to public APIs.

## See Also

- [Adding Tools](adding-tools.md): tool types and patterns.
- [Contributing](../community/contributing.md): contribution workflow.
