import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

import yaml
from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from pydantic import Field

from aigise.features.tool_combo import ToolCombo
from aigise.utils.project_info import PROJECT_PATH

logger = logging.getLogger(__name__)


class ToolLoader:
    """Loads tools from local filesystem into sandboxes."""

    def __init__(
        self,
        search_paths: Optional[List[Path]] = None,
        enabled_skills: Optional[Union[List[str], str]] = None,
    ):
        """Initialize ToolLoader.

        Args:
            search_paths: List of paths to search for tools.
            enabled_skills: Controls which skills are loaded.
                          - None (default): Load NO skills.
                          - "all": Load ONLY top-level skills: `<root>/*/SKILL.md`.
                          - List[str]: Load skills by exact path to the skill directory
                            under the root (e.g. "fuzz" or "fuzz/run-fuzzing-campaign").
                            When a list entry refers to a directory, all skills under
                            that prefix are loaded recursively (i.e. entry is treated
                            as a prefix allowlist).
        """
        self._filter_skills: Optional[Set[str]] = None
        self._enabled_skills = enabled_skills

        if enabled_skills == "all":
            self._filter_skills = None  # No filtering, load all
        elif enabled_skills is None:
            self._filter_skills = set()  # Filter everything (load nothing)
        else:
            self._filter_skills = set(enabled_skills)  # Filter by allowlist

        if search_paths:
            self.search_paths = search_paths
        else:
            self.search_paths = [
                PROJECT_PATH / "src/aigise/bash_tools",
                Path.home() / ".local/plugins/aigise/tools",
            ]

    def load_tools(self) -> List[Dict[str, Any]]:
        """
        Synchronously load all tools found in search paths, ONLY returning metadata.
        Does NOT copy files to sandbox.

        Structure supported:
        - root/tool_name/SKILL.md
        - root/group_name/tool_name/SKILL.md

        Returns:
            List of tool metadata extracted from SKILL.md for all found tools.
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

            if self._enabled_skills == "all":
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
            for prefix in self._filter_skills:
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
            fence = lines[i].strip()
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
        """Parse SKILL.md metadata."""
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
            Tuple of (prompt_text, required_sandboxes)
            - prompt_text: The generated prompt text
            - required_sandboxes: Set of sandbox types required by the tools
        """
        lines = [
            "note: See each Skill's `SKILL.md` for full parameters/options.",
            "",
        ]
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
        required_sandboxes: Set[str], *, enable_memory_management: bool = False
    ) -> str:
        """Generate description of sandbox structure for required sandboxes.

        Args:
            required_sandboxes: Set of sandbox type names that are actually required
            enable_memory_management: Whether long-term memory tools are enabled.

        Returns:
            Description text about sandbox structure and mount points
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
            lines.append(
                f"- **{sandbox_type}**: A containerized environment for running {sandbox_type}-specific operations"
            )

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
                "  This contains utility scripts that are available to all sandboxes but cannot be modified.",
                "",
                "- **`/bash_tools`**: Read-write directory containing bash tool scripts (Skills). ",
                "  This is where the tool paths mentioned above are located. Each tool directory contains:",
                "  - A `scripts/` subdirectory with executable scripts",
                "  - A `SKILL.md` file with documentation",
                "",
                "### Python Environment",
                "",
                "**Python is managed by `uv`**: the sandbox image creates a project-local virtual environment under `/app` using `uv` for the main sandbox",
                "",
                "Key points:",
                "- A venv is created at **`/app/.venv`** via `RUN uv venv --python 3.12`",
                "- Python deps are installed **into that venv** via `uv pip install ...`",
                "- Prefer running Python via the venv interpreter explicitly:",
                "  - `/app/.venv/bin/python -c '...'\n  - `/app/.venv/bin/pip list`",
                "- Note: command execution is non-persistent, so `source /app/.venv/bin/activate` will not carry over to the next command; prefer explicit `/app/.venv/bin/python ...`",
                "",
                "### Command Execution Model",
                "",
                "**Important**: Commands are executed as **non-persistent sessions**. Each command runs as a new independent process via `bash -c` or `sh -c`, not in a persistent interactive shell session.",
                "",
                "This means:",
                "- Each command starts with a fresh environment (environment variables, working directory, shell state are not preserved between commands)",
                "- To persist state between commands, use files in `/shared` directory or explicitly set environment variables in each command",
                "- Interactive commands that require TTY (like `vim`, `less`, `top`) may not work as expected",
                "- To change directory or set environment variables, include them in the command itself (e.g., `cd /path && command` or `VAR=value command`)",
                "",
            ]
        )

        if "neo4j" in required_sandboxes:
            idx = lines.index("### Python Environment")
            neo4j_lines = [
                "### Neo4j (Databases & Schemas)",
                "",
                "The `neo4j` sandbox provides Neo4j as structured storage.",
                "",
                "Database organization (selected by `client_type`):",
                "- **history**: Agent execution history (e.g. `AgentRun`, `Event`, `RawToolResponse`; relationships like `HAS_EVENT`, `SUMMARIZES_TOOL_RESPONSE`)",
                "- **analysis**: Static analysis / code graph data (Joern/CodeQL-related)",
                "- **memory**: Long-term memory (e.g. Q&A cache `QACache`)",
                "- Note: Some databases may not be available depending on sandbox configuration.",
                "",
            ]
            if enable_memory_management:
                neo4j_lines.extend(
                    [
                        "Query long-term memory:",
                        "- Prefer using the `memory_management_agent` tool. It exposes helpers like `cache_qa_pair`, `get_cached_answer_by_id`, `create_cache_relation`, `list_node_types`, `list_relations`, and `run_neo4j_query`.",
                        "- `run_neo4j_query` can target different DBs via `client_type` (default: `memory`).",
                        "",
                    ]
                )
            lines[idx:idx] = neo4j_lines

        return "\n".join(lines)


class AigiseAgent(LlmAgent):
    tool_combos: Optional[List[ToolCombo]] = Field(default=None)

    def __init__(
        self,
        *args,
        tools: Optional[List] = None,
        tool_combos: Optional[List[ToolCombo]] = None,
        enabled_skills: Optional[Union[List[str], str]] = None,
        enable_memory_management: bool = False,
        **kwargs,
    ):
        tools = list(tools) if tools else []

        sub_agents = kwargs.get("sub_agents", [])
        for combo in tool_combos or []:
            if combo.return_history:
                sub_agents.append(combo.sequential_agent)
            else:
                if combo.agent_tool not in tools:
                    tools.append(combo.agent_tool)

        if enable_memory_management:
            # Lazy import to avoid circular dependencies at module import time.
            from aigise.util_agents.memory_management_agent.agent import (
                create_memory_management_agent_tool,
            )

            model = kwargs.get("model", "")
            memory_management_tool = create_memory_management_agent_tool(model=model)
            if memory_management_tool not in tools:
                tools.append(memory_management_tool)

        kwargs["sub_agents"] = sub_agents
        kwargs["tools"] = tools

        # Initialize the parent class first
        super().__init__(*args, **kwargs)
        self._enable_memory_management = enable_memory_management
        # Store enabled_skills for dependency collection
        self._enabled_skills = enabled_skills
        loader = ToolLoader(
            enabled_skills=enabled_skills
        )  # No sandbox needed for metadata
        metadata = loader.load_tools()
        tool_prompt, required_sandboxes = ToolLoader.generate_system_prompt_part(
            metadata
        )

        if enable_memory_management:
            # Put this at the very front so it is followed even when the
            # instruction grows via dynamically injected tool descriptions.
            repo_first_prompt = """
Before doing anything else, you must first build comprehensive repository
documentation and persist it into Neo4j.

**CRITICAL: Start by carefully reading the README file(s) in the repository.**
The README contains essential information about the project's purpose, architecture,
and structure. Use this as the foundation for your documentation.

**Step 1: Determine Documentation Structure**

1) Analyze the repository:
   - Read and analyze the README file(s) FIRST to understand the project's purpose,
     architecture, and key components
   - Examine the repository file tree to identify major components, modules, and
     features
   - Based on README and code structure, design a logical documentation structure

2) Documentation structure should include:
   - **Overview**: Project introduction, purpose, and high-level architecture (heavily based on README)
   - **Core Components**: Major modules, features, or subsystems
   - **Architecture**: System design, data flow, component relationships
   - **API/Interfaces**: If applicable, API documentation or key interfaces
   - **Setup/Deployment**: Installation, configuration, deployment instructions
   - Additional sections as needed based on repository analysis

3) Create the structure outline:
   - List all planned documentation pages with titles and brief descriptions
   - Identify relationships between pages (which pages should link to others)

**Step 2: Write Documentation Pages**

1) Create documentation files under `/docs` directory:
   - Use descriptive filenames (e.g., `Overview.md`, `Architecture.md`, `API.md`)
   - Each page should be a standalone Markdown file

2) For each documentation page:
   - Write comprehensive, accurate content based on README analysis, code structure, and key source files
   - Include code examples where relevant
   - Use proper Markdown formatting
   - Add a `related_to` field (YAML frontmatter or comment):
     ```yaml
     ---
     title: Overview
     related_to:
       - Architecture
       - Quick Start
     ---
     ```

**Step 3: Store Documentation in Neo4j**

1) For each documentation page, use `memory_management_agent` to store it:
   - Call `cache_qa_pair(question="<page_title>", answer="<page_content>",
     answering_agent="documentation_agent", answering_model="<model_name>",
     metadata={"doc_type": "wiki_page"})`

2) After storing all pages, create relationships between related pages:
   - Use `create_cache_relation(source_match={"question": "<source_title>"},
     target_match={"question": "<target_title>"}, relation_type="RELATED_TO",
     database="memory")` for all pages listed in each page's `related_to` field

**Important Notes:**
- DO NOT mirror the repository directory structure - create a logical documentation structure
- ALWAYS start with README analysis - it's crucial for understanding the project
- Store pages and create relationships between them using the memory_management_agent tools
"""
            self.instruction = repo_first_prompt.strip() + "\n\n" + self.instruction

        if tool_prompt:
            # Preamble describing the skill structure
            description_preamble = (
                "Each tool path provided below represents a 'Skill' directory which follows a specific structure:\n"
                "- It contains a `SKILL.md` file which serves as documentation.\n"
                "- Some Skills are **toolsets/groupings** and may not include a `scripts/` directory.\n"
                "- Executable Skills include a `scripts/` directory with the runnable scripts/tools.\n"
                "You are encouraged to inspect these files (e.g., using `ls -R <path>` or `cat <path>/SKILL.md`) "
                "to better understand the tool's usage and available scripts before invocation.\n"
            )

            tool_usage_policy = (
                "Tool usage policy:\n"
                "- When planning or describing how you will accomplish a task, prefer using the provided Skills under "
                "`/bash_tools/...` (i.e., the tool scripts described below).\n"
                "- Only fall back to generic shell commands when there is no suitable `/bash_tools` Skill for the job.\n"
                "- If a workflow is repetitive, prefer writing a small wrapper script (or a new Skill) to automate it. "
                "You may compose existing `/bash_tools` Skills, and you may also adapt/extend them.\n"
                "- Do NOT edit existing `/bash_tools/...` Skills in place. If you need changes, copy/adapt into a new "
                "Skill/script under `/bash_tools/new_tools/<tool_name>/` (with a `SKILL.md`). You can use "
                "`/bash_tools/new_tool_creator` to scaffold the initial directory structure.\n"
                "You should use tools in `/bash_tools/...` to accomplish the task, do not use generic shell commands when there is a suitable tool in `/bash_tools/...`. Use tools in `/bash_tools/...` as much as possible."
                "You should use tools in `/bash_tools/...` to accomplish the task, do not use generic shell commands when there is a suitable tool in `/bash_tools/...`. Use tools in `/bash_tools/...` as much as possible."
                "You should use tools in `/bash_tools/...` to accomplish the task, do not use generic shell commands when there is a suitable tool in `/bash_tools/...`. Use tools in `/bash_tools/...` as much as possible."
            )

            # Generate sandbox structure description based on required sandboxes
            sandbox_description = ToolLoader.generate_sandbox_structure_description(
                required_sandboxes,
                enable_memory_management=self._enable_memory_management,
            )

            # logger.info(
            #     "Injecting dynamically loaded tool descriptions into agent instruction:\n\n"
            #     + tool_prompt
            # )
            self.instruction += (
                "\n\nHere are the available bash tools you can use:\n"
                f"{description_preamble}\n{tool_usage_policy}\n{tool_prompt}{sandbox_description}"
            )
        else:
            logger.info("No dynamically loaded tool descriptions found")

    def update_enabled_skills(
        self, enabled_skills: Optional[Union[List[str], str]]
    ) -> None:
        """Update enabled_skills and regenerate system prompt with new bash tools.

        This method:
        1. Updates the _enabled_skills attribute
        2. Removes the old bash tools section from instruction
        3. Generates new tool prompt based on new enabled_skills
        4. Appends the new tool prompt to instruction

        Args:
            enabled_skills: New enabled_skills value (None, "all", or List[str])
        """
        import re

        # Update enabled_skills
        self._enabled_skills = enabled_skills

        # Remove old tool prompt section from instruction
        # Pattern matches from "Here are the available bash tools" to end of string
        pattern = r"\n\nHere are the available bash tools you can use:.*"
        self.instruction = re.sub(pattern, "", self.instruction, flags=re.DOTALL)

        # Generate new tool prompt based on new enabled_skills
        loader = ToolLoader(enabled_skills=enabled_skills)
        metadata = loader.load_tools()
        tool_prompt, required_sandboxes = ToolLoader.generate_system_prompt_part(
            metadata
        )

        if tool_prompt:
            # Preamble describing the skill structure
            description_preamble = (
                "Each tool path provided below represents a 'Skill' directory which follows a specific structure:\n"
                "- It contains a `SKILL.md` file which serves as documentation.\n"
                "- Some Skills are **toolsets/groupings** and may not include a `scripts/` directory.\n"
                "- Executable Skills include a `scripts/` directory with the runnable scripts/tools.\n"
                "You are encouraged to inspect these files (e.g., using `ls -R <path>` or `cat <path>/SKILL.md`) "
                "to better understand the tool's usage and available scripts before invocation.\n"
            )

            tool_usage_policy = (
                "Tool usage policy:\n"
                "- When planning or describing how you will accomplish a task, prefer using the provided Skills under "
                "`/bash_tools/...` (i.e., the tool scripts described below).\n"
                "- Only fall back to generic shell commands when there is no suitable `/bash_tools` Skill for the job.\n"
                "- If a workflow is repetitive, prefer writing a small wrapper script (or a new Skill) to automate it. "
                "You may compose existing `/bash_tools` Skills, and you may also adapt/extend them.\n"
                "- Do NOT edit existing `/bash_tools/...` Skills in place. If you need changes, copy/adapt into a new "
                "Skill/script under `/bash_tools/new_tools/<tool_name>/` (with a `SKILL.md`). You can use "
                "`/bash_tools/new_tool_creator` to scaffold the initial directory structure.\n"
            )

            # Generate sandbox structure description based on required sandboxes
            sandbox_description = ToolLoader.generate_sandbox_structure_description(
                required_sandboxes,
                enable_memory_management=self._enable_memory_management,
            )

            # Append new tool prompt to instruction
            self.instruction += (
                "\n\nHere are the available bash tools you can use:\n"
                f"{description_preamble}\n{tool_usage_policy}\n{tool_prompt}{sandbox_description}"
            )
            logger.info(
                f"Updated enabled_skills and regenerated system prompt for agent '{self.name}'"
            )
        else:
            logger.info(
                f"Updated enabled_skills to {enabled_skills}, no bash tools found"
            )
