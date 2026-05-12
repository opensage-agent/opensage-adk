from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union

import yaml
from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.mcp_tool.mcp_tool import MCPTool as _AdkMCPTool
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from pydantic import Field

from opensage.features.tool_combo import ToolCombo
from opensage.utils.project_info import PROJECT_PATH

logger = logging.getLogger(__name__)

MCP_TOOL_CALL_TIMEOUT = timedelta(seconds=300)

_TOOLSET_SUMMARY_MARKER = "[[OPENSAGE_TOOLSET_SUMMARY]]"


async def opensage_default_tool_error_handler(
    tool: Any,
    args: dict[str, Any],
    tool_context: Any,
    error: Exception,
) -> dict[str, Any]:
    """Convert otherwise-unhandled tool exceptions into model-visible results."""
    return {
        "success": False,
        "error": (
            f"Tool {getattr(tool, 'name', '<unknown>')!r} failed: "
            f"{type(error).__name__}: {error}\n\nBacktrace:\n"
            f"{traceback.format_exc()}"
        ),
    }


def _append_default_tool_error_handler(
    callback: Optional[Union[Callable[..., Any], List[Callable[..., Any]]]],
) -> Union[Callable[..., Any], List[Callable[..., Any]]]:
    if callback is None:
        return opensage_default_tool_error_handler
    callbacks = list(callback) if isinstance(callback, list) else [callback]
    if opensage_default_tool_error_handler not in callbacks:
        callbacks.append(opensage_default_tool_error_handler)
    return callbacks


class OpenSageMcpTool(_AdkMCPTool):
    """Wraps ADK's MCPTool with per-call timeout and error resilience.

    Fixes two ADK gaps:
    - ``_run_async_impl``: passes ``read_timeout_seconds`` to
      ``session.call_tool()`` so a stuck MCP server doesn't block forever.
    - ``run_async``: catches all exceptions and converts them to an
      ``{"error": ...}`` dict so the LLM can continue gracefully.
    """

    async def _run_async_impl(self, *, args, tool_context, credential):
        from google.adk.agents.readonly_context import ReadonlyContext
        from opentelemetry import propagate

        auth_headers = await self._get_headers(tool_context, credential)
        dynamic_headers = None
        if self._header_provider:
            dynamic_headers = self._header_provider(
                ReadonlyContext(tool_context._invocation_context)
            )

        headers: Dict[str, str] = {}
        if auth_headers:
            headers.update(auth_headers)
        if dynamic_headers:
            headers.update(dynamic_headers)
        final_headers = headers if headers else None

        trace_carrier: Dict[str, str] = {}
        propagate.get_global_textmap().inject(carrier=trace_carrier)
        meta_trace_context = trace_carrier if trace_carrier else None

        session = await self._mcp_session_manager.create_session(headers=final_headers)
        resolved_callback = self._resolve_progress_callback(tool_context)

        response = await session.call_tool(
            self._mcp_tool.name,
            arguments=args,
            read_timeout_seconds=MCP_TOOL_CALL_TIMEOUT,
            progress_callback=resolved_callback,
            meta=meta_trace_context,
        )
        return response.model_dump(exclude_none=True, mode="json")

    async def run_async(self, *, args: dict[str, Any], tool_context) -> Any:
        try:
            return await super().run_async(args=args, tool_context=tool_context)
        except Exception as e:
            logger.warning("MCP tool %s failed: %s", self.name, e, exc_info=True)
            return {
                "error": (f"MCP tool {self.name!r} failed: {type(e).__name__}: {e}")
            }


class OpenSageMCPToolset(McpToolset):
    """An ADK McpToolset that also carries a stable `name` for OpenSage.

    Why this exists:
    - ADK's McpToolset does not define a stable `name` attribute.
    - OpenSage dynamic subagent creation (`create_subagent`) validates requested Python tools by name via `extract_tools_from_agent()`.

    With this wrapper, callers can pass toolset names into `create_subagent`:
    - `tools_list=["gdb_mcp"]` injects the entire MCP toolset into the subagent.

    Notes:
    - This class does NOT enumerate MCP tools at init time (no async/network).
    - We **enforce** `tool_name_prefix == name` to reduce collisions across MCP
      servers that may expose overlapping tool names, and to make it possible to
      map a prefixed tool name like "gdb_mcp_step_control" back to the toolset
      name "gdb_mcp" deterministically.
    """

    def __init__(
        self,
        *,
        name: str,
        tool_name_prefix: Optional[
            str
        ] = None,  # TODO: this means it is a prefixed tool name? or prefix added to the tool name?
        **kwargs,
    ):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("OpenSageMCPToolset requires a non-empty name")

        resolved_prefix = tool_name_prefix if tool_name_prefix is not None else name
        if resolved_prefix != name:
            raise ValueError(
                "OpenSageMCPToolset requires tool_name_prefix == name "
                f"(got name={name!r}, tool_name_prefix={resolved_prefix!r})"
            )
        super().__init__(tool_name_prefix=resolved_prefix, **kwargs)
        self.name = name

    def _mcp_log_context(self) -> str:
        connection_params = getattr(self, "_connection_params", None)
        parts = [f"name={self.name!r}"]

        url = getattr(connection_params, "url", None)
        if url:
            parts.append(f"url={url!r}")
        else:
            server_params = getattr(connection_params, "server_params", None)
            command = getattr(connection_params, "command", None) or getattr(
                server_params, "command", None
            )
            args = getattr(connection_params, "args", None) or getattr(
                server_params, "args", None
            )
            if command:
                parts.append(f"command={command!r}")
            if args:
                parts.append(f"args={args!r}")

        return ", ".join(parts)

    async def _execute_with_session(
        self,
        coroutine_func: Any,
        error_message: str,
        readonly_context: Optional[Any] = None,
    ) -> Any:
        enriched_message = f"{error_message} ({self._mcp_log_context()})"
        return await super()._execute_with_session(
            coroutine_func, enriched_message, readonly_context
        )

    async def get_tools(self, readonly_context=None):
        try:
            tools = await super().get_tools(readonly_context)
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.warning(
                "MCP server %s unreachable, returning empty tool list "
                "for this turn (%s): %s",
                self.name,
                self._mcp_log_context(),
                e,
            )
            return []

        wrapped = []
        for tool in tools:
            if isinstance(tool, _AdkMCPTool) and not isinstance(tool, OpenSageMcpTool):
                wrapped.append(
                    OpenSageMcpTool(
                        mcp_tool=tool.raw_mcp_tool,
                        mcp_session_manager=tool._mcp_session_manager,
                        auth_scheme=self._auth_scheme,
                        auth_credential=self._auth_credential,
                        require_confirmation=tool._require_confirmation,
                        header_provider=tool._header_provider,
                        progress_callback=getattr(tool, "_progress_callback", None),
                    )
                )
            else:
                wrapped.append(tool)
        return wrapped


class ToolLoader:
    """Loads tools from local filesystem into sandboxes."""

    def __init__(
        self,
        search_paths: Optional[List[Path]] = None,
        enabled_skills: Optional[Union[List[str], str]] = None,
    ):
        """Initialize ToolLoader.

        Args:
            search_paths (Optional[List[Path]]): List of paths to search for tools.
            enabled_skills (Optional[Union[List[str], str]]): Controls which skills are loaded.
                          - None (default): Load NO skills.
                          - "all": Load ONLY top-level skills: `<root>/*/SKILL.md`.
                          - List[str]: Load skills by exact path to the skill directory
                            under the root (e.g. "fuzz" or "fuzz/run-fuzzing-campaign").
                            When a list entry refers to a directory, all skills under
                            that prefix are loaded recursively (i.e. entry is treated
                            as a prefix allowlist)."""
        self._filter_skills: Optional[Set[str]] = None
        self._enabled_skills = enabled_skills

        if enabled_skills == "all" or enabled_skills == ["all"]:
            self._filter_skills = None  # No filtering, load all
        elif enabled_skills is None:
            self._filter_skills = set()  # Filter everything (load nothing)
        else:
            self._filter_skills = set(enabled_skills)  # Filter by allowlist

        if search_paths:
            self.search_paths = search_paths
        else:
            self.search_paths = [
                PROJECT_PATH / "src/opensage/bash_tools",
                Path.home() / ".local/opensage/bash_tools",
            ]

    def load_tools(self) -> List[Dict[str, Any]]:
        """
        Synchronously load all tools found in search paths, ONLY returning metadata.
        Does NOT copy files to sandbox.

        Structure supported:
        - root/tool_name/SKILL.md
        - root/group_name/tool_name/SKILL.md

        Returns:
            List[Dict[str, Any]]: List of tool metadata extracted from SKILL.md for all found tools.
        """
        discovered_tools = set()
        loaded_tools_metadata = []

        for search_path in self.search_paths:
            if not search_path.exists():
                continue

            # enabled_skills behavior:
            # - None: load nothing
            # - "all": load only top-level skills (search_path/*/SKILL.md), do not descend
            # - List[str]: resolve each entry directly to <search_path>/<entry>/SKILL.md
            if self._enabled_skills is None:
                continue

            if self._enabled_skills == "all" or self._enabled_skills == ["all"]:
                for item in search_path.iterdir():
                    if not item.is_dir():
                        continue
                    if (item / "SKILL.md").exists():
                        self._process_tool(
                            item,
                            item.name,
                            None,
                            discovered_tools,
                            loaded_tools_metadata,
                        )
                continue

            if isinstance(self._enabled_skills, list):
                for entry in self._enabled_skills:
                    entry_path = Path(entry)
                    if entry_path.is_absolute():
                        logger.warning(
                            "enabled_skills entry must be relative to the skill root; "
                            "skipping absolute path: %s",
                            entry,
                        )
                        continue

                    tool_dir = (search_path / entry).resolve()
                    if not tool_dir.is_dir():
                        continue
                    # Recursively load all SKILL.md under this entry directory.
                    # The allowlist is applied as a prefix match in _process_tool.
                    for skill_file in tool_dir.rglob("SKILL.md"):
                        skill_dir = skill_file.parent
                        try:
                            tool_name = str(skill_dir.relative_to(search_path))
                        except ValueError:
                            continue
                        sandbox_name = (
                            tool_name.split("/", 1)[0] if "/" in tool_name else None
                        )
                        self._process_tool(
                            skill_dir,
                            tool_name,
                            sandbox_name,
                            discovered_tools,
                            loaded_tools_metadata,
                        )
                continue

            # Fallback: keep the old scan behavior (should not happen in practice).
            for item in search_path.iterdir():
                if not item.is_dir():
                    continue
                if (item / "SKILL.md").exists():
                    self._process_tool(
                        item,
                        item.name,
                        None,
                        discovered_tools,
                        loaded_tools_metadata,
                    )
                else:
                    sandbox_name = item.name
                    for subitem in item.iterdir():
                        if subitem.is_dir() and (subitem / "SKILL.md").exists():
                            tool_name = f"{sandbox_name}/{subitem.name}"
                            self._process_tool(
                                subitem,
                                tool_name,
                                sandbox_name,
                                discovered_tools,
                                loaded_tools_metadata,
                            )

        return loaded_tools_metadata

    def _process_tool(
        self,
        tool_path: Path,
        tool_name: str,
        sandbox_name: Optional[str],
        discovered_tools: set,
        loaded_tools_metadata: list,
    ) -> None:
        """Helper to process a single tool synchronously (metadata only)."""

        # Filter by enabled_skills if specified
        if self._filter_skills is not None:
            # Treat enabled_skills entries as a prefix allowlist.
            # This lets users specify a toolset folder (e.g. "static_analysis") and
            # still load all nested tools under it recursively.
            allowed = False
            for prefix in self._filter_skills:  # TODO: the prefix here still a little confusing, it means prefixed tool name or prefix of a tool name? what is our tool naming system
                if tool_name == prefix or tool_name.startswith(f"{prefix}/"):
                    allowed = True
                    break
            if not allowed:
                return

        if tool_name not in discovered_tools:
            discovered_tools.add(tool_name)

            metadata = self._parse_skill_metadata(tool_path, tool_name)
            if metadata:
                loaded_tools_metadata.append(metadata)

    @staticmethod
    def _parse_requires_sandboxes_from_markdown(content: str) -> list[str]:
        """Parse dependency sandboxes from a SKILL.md '## Requires Sandbox' section.

        This is a best-effort parser used because many SKILL.md files specify
        dependency requirements in Markdown.
        """
        header = "## Requires Sandbox"
        idx = content.find(header)
        if idx < 0:
            return []

        after = content[idx + len(header) :]
        lines = after.splitlines()

        # Skip initial blank lines.
        i = 0
        while i < len(lines) and not lines[i].strip():
            i += 1

        sandboxes = set()
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            if line.startswith("#"):
                break  # next section
            if line.startswith("- "):
                line = line[2:].strip()

            # Common patterns in repo: "fuzz" or "joern, main, neo4j, codeql".
            for token in line.split(","):
                token = token.strip()
                if not token:
                    continue
                if token.lower() in ("none", "n/a", "na"):
                    continue
                sandboxes.add(token)
            i += 1

        return sorted(sandboxes)

    @staticmethod
    def _is_executable_skill_dir(tool_path: Path) -> bool:
        """Returns True if the skill directory contains runnable scripts."""
        scripts_dir = tool_path / "scripts"
        if not scripts_dir.exists() or not scripts_dir.is_dir():
            return False
        for p in scripts_dir.iterdir():
            if p.is_file() and p.suffix in (".sh", ".py"):
                return True
        return False

    @staticmethod
    def _parse_usage_from_markdown(content: str) -> str:
        """Parse a short usage snippet from a SKILL.md '## Usage' section."""
        header = "## Usage"
        idx = content.find(header)
        if idx < 0:
            return ""

        after = content[idx + len(header) :]
        lines = after.splitlines()

        # Move to first non-empty line.
        i = 0
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            return ""

        # Prefer the first fenced code block.
        if lines[i].strip().startswith("```"):
            i += 1
            block = []
            while i < len(lines):
                if lines[i].strip().startswith("```"):
                    break
                block.append(lines[i].rstrip())
                i += 1
            snippet = "\n".join(block).strip()
            return snippet

        # Otherwise, take the first paragraph until next header.
        block = []
        while i < len(lines):
            line = lines[i].rstrip()
            if line.strip().startswith("#"):
                break
            if not line.strip() and block:
                break
            block.append(line)
            i += 1

        return "\n".join(block).strip()

    def _parse_skill_metadata(
        self, tool_path: Path, tool_name: str
    ) -> Optional[Dict[str, Any]]:
        """Parse SKILL.md metadata.

        Raises:
          ValueError: Raised when this operation fails."""
        skill_file = tool_path / "SKILL.md"
        if not skill_file.exists():
            logger.warning(f"SKILL.md not found for tool {tool_name} at {tool_path}")
            return None

        try:
            content = skill_file.read_text()
            requires_sandboxes = self._parse_requires_sandboxes_from_markdown(content)

            # Extract YAML frontmatter (required)
            if not content.startswith("---"):
                raise ValueError("Missing YAML frontmatter (must start with '---').")

            parts = content.split("---", 2)
            if len(parts) < 3:
                raise ValueError("Invalid YAML frontmatter (missing closing '---').")

            yaml_content = parts[1]
            data = yaml.safe_load(yaml_content)
            if not isinstance(data, dict):
                raise ValueError("Invalid YAML frontmatter (must parse to a dict).")

            # Strong schema:
            # - should_run_in_sandbox: execution location (required for executable Skills)
            # - ## Requires Sandbox: dependency sandboxes (optional; parsed from Markdown)
            # - sandbox/sandboxes are not accepted.
            if "sandbox" in data or "sandboxes" in data:
                raise ValueError(
                    "Deprecated field in SKILL.md YAML frontmatter: "
                    "use 'should_run_in_sandbox' (execution) and "
                    "'## Requires Sandbox' (dependencies); "
                    "do not use 'sandbox'/'sandboxes'."
                )

            is_executable = self._is_executable_skill_dir(tool_path)
            exec_sandbox = data.get("should_run_in_sandbox")
            if is_executable:
                if not isinstance(exec_sandbox, str) or not exec_sandbox.strip():
                    raise ValueError(
                        "Executable Skill is missing required "
                        "'should_run_in_sandbox' in YAML frontmatter."
                    )
                data["should_run_in_sandbox"] = exec_sandbox.strip()
            else:
                # Non-executable skill groupings may omit execution sandbox.
                if isinstance(exec_sandbox, str) and exec_sandbox.strip():
                    data["should_run_in_sandbox"] = exec_sandbox.strip()
                else:
                    data.pop("should_run_in_sandbox", None)

            if requires_sandboxes:
                data["requires_sandboxes"] = requires_sandboxes
            else:
                data.pop("requires_sandboxes", None)

            # Ensure path and description are present
            # Use tool_name as path if not specified
            if "path" not in data:
                data["path"] = tool_name
            return data
        except Exception as e:
            raise ValueError(
                f"Invalid SKILL.md for {tool_name} at {tool_path}: {e}"
            ) from e

    @staticmethod
    def generate_system_prompt_part(
        tools_metadata: List[Dict[str, Any]],
        sandbox_name: Optional[str] = None,
        remote_root: str = "/bash_tools",
    ) -> tuple[str, Set[str]]:
        """Generate system prompt from tool metadata.

        Returns:
            tuple[str, Set[str]]: Tuple of (prompt_text, required_sandboxes)
            - prompt_text: The generated prompt text
            - required_sandboxes: Set of sandbox types required by the tools
        """
        lines = []
        required_sandboxes: Set[str] = set()

        for tool in tools_metadata:
            path = tool.get("path", "")
            description = tool.get("description", "")
            should_run_in_sandbox = tool.get("should_run_in_sandbox", "")
            requires_sandboxes = tool.get("requires_sandboxes", [])

            # Construct absolute path if it looks like a relative tool name
            if path and not path.startswith("/"):
                path = f"{remote_root}/{path}"

            # required_sandboxes = execution sandbox union dependency sandboxes.
            if isinstance(should_run_in_sandbox, str) and should_run_in_sandbox:
                required_sandboxes.add(should_run_in_sandbox)
            if isinstance(requires_sandboxes, list) and requires_sandboxes:
                for sb in requires_sandboxes:
                    if isinstance(sb, str) and sb:
                        required_sandboxes.add(sb)

            if path and description:
                lines.append(f"- path: {path}")
                lines.append(f"  description: {description}")
                if should_run_in_sandbox:
                    lines.append(f"  should_run_in_sandbox: {should_run_in_sandbox}")
                lines.append("")

        prompt_text = "\n".join(lines)
        return prompt_text, required_sandboxes

    @staticmethod
    def generate_sandbox_structure_description(
        required_sandboxes: Set[str],
        *,
        agent_name: Optional[str] = None,
    ) -> str:
        """Generate description of sandbox structure for required sandboxes.

        Args:
            required_sandboxes (Set[str]): Set of sandbox type names that are actually required
        Returns:
            str: Description text about sandbox structure and mount points
        """
        if not required_sandboxes:
            return ""

        # Sort for consistent output
        sandbox_list = sorted(required_sandboxes)

        lines = [
            "\n## Sandbox Environment",
            "",
            "The following sandboxes are available for the tools you can use:",
            "",
        ]

        for sandbox_type in sandbox_list:
            lines.append(f"- **{sandbox_type}**")

        lines.extend(
            [
                "",
                "### Shared Mount Points",
                "",
                "All sandboxes share the following mount points:",
                "",
                "- **`/shared`**: Read-write shared directory accessible across all sandboxes. ",
                "  Use this for storing data that needs to be shared between sandboxes or persisted.",
                "",
                "- **`/sandbox_scripts`**: Read-only shared directory containing sandbox initialization scripts. ",
                "",
                "- **`/bash_tools`**: Read-write directory containing bash tool scripts (Skills). ",
                "  This is where the tool paths mentioned above are located.",
                "",
                "### Python Environment",
                "",
                "**Python is managed by `uv`**: the sandbox image creates a project-local virtual environment under `/app` using `uv` for the main sandbox",
                "",
                "Key points:",
                "- A venv is created at **`/app/.venv`** via `RUN uv venv --python 3.12`",
                "- Note: command execution is non-persistent, so `source /app/.venv/bin/activate` will not carry over to the next command; prefer explicit `/app/.venv/bin/python ...`",
                "",
                "### Command Execution Model",
                "",
                "**Important**: Commands are executed as **non-persistent sessions**. Each command runs as a new independent process via `bash -c` or `sh -c`, not in a persistent interactive shell session.",
                "",
                "This means:",
                "- Each command starts with a fresh environment (environment variables, working directory, shell state are not preserved between commands)",
                "- To change directory or set environment variables, include them in the command itself (e.g., `cd /path && command` or `VAR=value command`)",
                "",
            ]
        )
        if "neo4j" in required_sandboxes:
            idx = lines.index("### Python Environment")
            neo4j_lines = [
                "### Neo4j",
                "",
                "The `neo4j` sandbox provides Neo4j as structured storage. The "
                "`analysis` database holds the code property graph populated by "
                "Joern/CodeQL initializers; query it with the `static_analysis` "
                "or `neo4j-query` skills (see their `SKILL.md` for schema).",
                "",
            ]
            lines[idx:idx] = neo4j_lines

        return "\n".join(lines)


class OpenSageAgent(LlmAgent):
    tool_combos: Optional[List[ToolCombo]] = Field(default=None)

    def __init__(
        self,
        *args,
        tools: Optional[List] = None,  # TODO: this should be the initial tool list?
        tool_combos: Optional[List[ToolCombo]] = None,
        enabled_skills: Optional[Union[List[str], str]] = None,
        subagents: Optional[List["OpenSageAgent"]] = None,
        **kwargs,
    ):
        # --- Ban AgentTool in tools list ---
        tools = list(tools) if tools else []
        try:
            from google.adk.tools.agent_tool import AgentTool as _AgentTool

            offending_names = [
                getattr(getattr(t, "agent", None), "name", "?")
                for t in tools
                if isinstance(t, _AgentTool)
            ]
            if offending_names:
                raise ValueError(
                    f"AgentTool is forbidden in OpenSageAgent. "
                    f"Found AgentTool(s) wrapping: {offending_names}. "
                    f"Use subagents=[...] to declare sub-agents; use the "
                    f"call_subagent / continue_agent_instance tools to invoke them."
                )
        except ImportError:
            pass

        # --- Validate and stash subagents (OpenSage's own declaration field) ---
        subagents_list = list(subagents or [])
        for sa in subagents_list:
            if not isinstance(sa, OpenSageAgent):
                raise ValueError(
                    f"subagents must contain OpenSageAgent instances; got "
                    f"{type(sa).__name__}"
                )

        sub_agents = kwargs.get("sub_agents", [])
        for combo in tool_combos or []:  # TODO: why tool combos for sub-agents?
            if combo.return_history:
                sub_agents.append(combo.sequential_agent)
            else:
                if combo.agent_tool not in tools:
                    tools.append(combo.agent_tool)

        # Ensure all tools are safe and dict-shaped (including MCP-expanded tools).
        # We intentionally do this before calling the ADK LlmAgent constructor so the
        # runner always sees wrapped tools.
        from opensage.toolbox.tool_normalization import make_toollikes_safe_dict

        tools = make_toollikes_safe_dict(tools)

        kwargs["sub_agents"] = sub_agents
        kwargs["tools"] = tools

        # Append default tool error handler
        kwargs["on_tool_error_callback"] = _append_default_tool_error_handler(
            kwargs.get("on_tool_error_callback")
        )

        # Initialize the parent class first
        super().__init__(*args, **kwargs)

        # Store the OpenSage-specific subagents declaration (distinct from ADK's
        # sub_agents field). These are registered to AgentManager.agents at
        # startup and are invoked via call_subagent / continue_agent_instance.
        self._subagents = subagents_list

        # Inject subagent coordination policy when orchestration tools are present.
        _orch_names = {
            "call_subagent",
            "create_subagent",
            "continue_agent_instance",
        }
        if _orch_names & {getattr(t, "__name__", "") for t in tools}:
            self.instruction = (self.instruction or "") + (
                "\n\n## Subagent Coordination Policy\n"
                "Do NOT poll or check on subagent progress in any way — not via "
                "list_subagents, not via terminal commands (find, ls, cat), not "
                "by any other means. Both sync and async subagents automatically "
                "report results back to you when they finish. While waiting, "
                "work on a different task or do nothing.\n"
                "\n"
                "## Peer-to-Peer Collaboration\n"
                "You are part of a multi-agent system where specialist agents "
                "collaborate toward a shared goal. You have orchestration tools "
                "(create_subagent, call_subagent, send_message, list_subagents, "
                "etc.) and can communicate directly with other agents.\n"
                "\n"
                "- Use `list_subagents` to discover other active agents and their capabilities.\n"
                "- Use `send_message` to directly request information from a peer agent — "
                "do NOT route peer requests through the root agent.\n"
                "- Use `create_subagent` / `call_subagent` to spawn specialists for "
                "subtasks you cannot handle with your own tools.\n"
                "\n"
                "When to message a peer directly:\n"
                "- You need a different tool that another agent has (e.g., ask a GDB agent "
                "to set a breakpoint, ask a Ghidra agent to decompile a function).\n"
                "- You found something another agent needs to know about (e.g., a memory "
                "address, a vulnerability, a key insight).\n"
                "- You want a second opinion or verification from another specialist.\n"
            )

        # Store enabled_skills for dependency collection
        self._enabled_skills = enabled_skills
        loader = ToolLoader(
            enabled_skills=enabled_skills
        )  # No sandbox needed for metadata
        metadata = loader.load_tools()
        tool_prompt, required_sandboxes = ToolLoader.generate_system_prompt_part(
            metadata
        )

        if tool_prompt:
            # Preamble describing the skill structure
            description_preamble = (
                "Each tool path below is a Skill directory:\n"
                "- `SKILL.md`: documentation/usage.\n"
                "- Toolset Skills may not have `scripts/`.\n"
                "- Executable Skills have `scripts/` with runnable tools.\n"
            )

            tool_usage_policy = (
                "Tool usage policy:\n"
                "- When planning or describing how you will accomplish a task, prefer using the provided Skills under "
                "`/bash_tools/...` (i.e., the tool scripts described below).\n"
                "- Only fall back to generic shell commands when there is **no** suitable `/bash_tools` Skill for the job.\n"
                "- Before starting work, survey the tool ecosystem broadly:\n"
                "  - Skill paths and descriptions are listed in your prompt above; "
                "`cat /bash_tools/<path>/SKILL.md` to read a Skill's full docs when needed.\n"
                "  - Inspect multiple relevant toolsets (e.g., retrieval + static_analysis + neo4j), not just one.\n"
                "  - If a Skill exists, use it instead of generic shell.\n"
                "- If a workflow is repetitive, prefer writing a small wrapper script (or a new Skill) to automate it. "
                "You may compose existing `/bash_tools` Skills, and you may also adapt/extend them.\n"
                "- Do NOT edit existing `/bash_tools/...` Skills in place. If you need changes, copy/adapt into a new "
                "Skill/script under `/bash_tools/new_tools/<tool_name>/` (with a `SKILL.md`). You can use "
                "`/bash_tools/new_tool_creator` to scaffold the initial directory structure.\n"
            )

            toolset_summary = self._build_toolset_summary(tools)
            if toolset_summary and _TOOLSET_SUMMARY_MARKER not in (
                self.instruction or ""
            ):
                tool_usage_policy += f"\n\n{toolset_summary}"

            # Generate sandbox structure description based on required sandboxes.
            sandbox_description = ToolLoader.generate_sandbox_structure_description(
                required_sandboxes,
                agent_name=self.name,
            )

            self.instruction += (
                "\n\nHere are the available bash tools you can use:\n"
                f"{description_preamble}\n{tool_prompt}{sandbox_description}\n\n"
                "## Tool Usage Policy (MUST FOLLOW)\n\n"
                f"{tool_usage_policy}"
            )  # TODO: is this instruction too long and some should be written as skills?
        else:
            logger.info("No dynamically loaded tool descriptions found")

    @staticmethod
    def _build_toolset_summary(tools: List[Any]) -> str:
        """Build a short, synchronous toolset summary for the agent system prompt.

        This is used to help the model (and users) understand that some
        capabilities are provided via toolsets (e.g., MCP toolsets), and that a
        toolset can be passed by name into `create_subagent.tools_list` to inject
        the full toolset into a new subagent.

        Important:
        - This method must NOT trigger any async enumeration or network I/O.
        - It only lists toolsets that already have a stable `name` attribute.
        """

        def _tool_name(obj: Any) -> Optional[str]:
            """Best-effort tool name extraction (synchronous, no I/O)."""
            name = getattr(obj, "name", None)
            if isinstance(name, str) and name.strip():
                return name
            name = getattr(obj, "__name__", None)
            if isinstance(name, str) and name.strip():
                return name
            func = getattr(obj, "func", None)
            name = getattr(func, "__name__", None)
            if isinstance(name, str) and name.strip():
                return name
            return None

        has_create_subagent = any(
            _tool_name(t) == "create_subagent" for t in (tools or [])
        )
        has_mcp_toolset = any(isinstance(t, McpToolset) for t in (tools or []))

        toolsets: dict[str, BaseToolset] = {}
        for t in tools or []:
            if isinstance(t, BaseToolset) and getattr(t, "name", None):
                toolsets[str(t.name)] = t

        if not toolsets:
            return ""

        names_sorted = sorted(toolsets.keys())
        lines = [
            _TOOLSET_SUMMARY_MARKER,
            "Available Python toolsets (inject by name via `create_subagent.tools_list`):",
        ]
        for name in names_sorted:
            prefix = getattr(toolsets[name], "tool_name_prefix", None)
            if isinstance(prefix, str) and prefix.strip():
                lines.append(f"- {name} (tool_name_prefix={prefix})")
            else:
                lines.append(f"- {name}")

        if has_mcp_toolset and has_create_subagent:
            lines.append(
                "Policy: for MCP toolsets, do NOT call expanded MCP tools directly from this agent; "
                "always use `create_subagent` and inject the toolset by name, then perform MCP actions inside that subagent."
            )

        lines.append(
            "Note: toolsets (especially MCP toolsets) expand their individual tools at runtime."
        )
        return "\n".join(lines).strip()
