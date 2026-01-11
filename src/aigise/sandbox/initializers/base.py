"""Base Initializer class for sandbox functionality."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
from abc import ABC, abstractmethod

from aigise.session.sandbox_state import SandboxState

logger = logging.getLogger(__name__)


class SandboxInitializer(ABC):
    """Base class for sandbox functionality initializers."""

    async def async_initialize(self) -> None:
        """Initialize sandbox initializer (async version)."""

        await self.ensure_ready()

    async def async_prepare_skill_deps(self) -> None:
        """Run enabled per-skill dependency installers for this sandbox (best-effort).

        Convention:
        - A skill directory may include:
          - `deps/<sandbox_type>/install.sh` (sandbox-specific), and/or
          - `deps/install.sh` (generic)
        - These scripts are executed inside the *execution sandbox* indicated by
          the skill's `should_run_in_sandbox` YAML field, and only when that
          sandbox is being initialized.

        This is called *before* the sandbox's ensure_ready/async_initialize step
        so that skills can assume their dependencies exist by the time the
        sandbox is marked READY.
        """
        # Lazy imports to avoid circular deps and to keep non-sandbox contexts safe.
        try:
            from aigise.sandbox.base_sandbox import (
                BaseSandbox,  # pylint: disable=g-import-not-at-top
            )
            from aigise.session import (
                get_aigise_session,  # pylint: disable=g-import-not-at-top
            )
        except Exception:  # pylint: disable=broad-except
            return

        if not isinstance(self, BaseSandbox):
            return

        session_id = getattr(self, "aigise_session_id", None)
        sandbox_type = getattr(self, "sandbox_type", None)
        if not session_id or not sandbox_type:
            return

        try:
            aigise_session = get_aigise_session(session_id)
        except Exception:  # pylint: disable=broad-except
            return

        enabled_skills = getattr(
            getattr(aigise_session, "sandboxes", None), "enabled_skills", None
        )
        if enabled_skills is None:
            # None => no skills enabled (ToolLoader convention); nothing to install.
            return

        def _marker_path(rel_skill_dir: str) -> str:
            safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", rel_skill_dir.strip("/"))
            return f"/shared/.aigise/skill_deps/{sandbox_type}/{safe}.done"

        def _parse_should_run_in_sandbox(skill_md: str) -> str | None:
            if not skill_md.startswith("---"):
                return None
            parts = skill_md.split("---", 2)
            if len(parts) < 3:
                return None
            yaml_block = parts[1]
            match = re.search(
                r"^should_run_in_sandbox:\s*(.+)$", yaml_block, re.MULTILINE
            )
            if not match:
                return None
            val = match.group(1).strip()
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                val = val[1:-1].strip()
            return val.lower() if val else None

        skill_dirs: list[str] = []
        if isinstance(enabled_skills, list):
            for entry in enabled_skills:
                if not isinstance(entry, str):
                    continue
                entry = entry.strip()
                if not entry or entry.startswith("/"):
                    continue
                normalized = f"/{entry}/"
                if entry in (".", "..") or "/../" in normalized or "/./" in normalized:
                    continue
                # Treat each enabled_skills entry as a directory prefix under /bash_tools.
                #
                # Examples:
                # - "fuzz" => include all skills under /bash_tools/fuzz/**/SKILL.md
                # - "fuzz/run-fuzzing-campaign" => include all skills under
                #   /bash_tools/fuzz/run-fuzzing-campaign/**/SKILL.md
                #
                # If the directory itself is an executable skill (has SKILL.md),
                # it will be included too.
                root = f"/bash_tools/{entry}"
                # If entry itself is a skill dir (has SKILL.md), include it.
                _, has_skill_md = self.run_command_in_container(
                    ["bash", "-lc", f"test -f {shlex.quote(root + '/SKILL.md')}"],
                    timeout=10,
                )
                if has_skill_md == 0:
                    skill_dirs.append(entry)

                # Always expand if entry is a directory: include all child skill dirs.
                out, code = self.run_command_in_container(
                    [
                        "bash",
                        "-lc",
                        " && ".join(
                            [
                                f"test -d {shlex.quote(root)}",
                                # No maxdepth: user expectation is that enabling a folder
                                # enables all nested skills.
                                f"find {shlex.quote(root)} -type f -name SKILL.md -print",
                            ]
                        ),
                    ],
                    timeout=60,
                )
                if code == 0 and isinstance(out, str) and out.strip():
                    for line in out.splitlines():
                        path = line.strip()
                        if not path.startswith("/bash_tools/") or not path.endswith(
                            "/SKILL.md"
                        ):
                            continue
                        rel_dir = os.path.dirname(path)[len("/bash_tools/") :]
                        if rel_dir:
                            skill_dirs.append(rel_dir)
        else:
            out, code = self.run_command_in_container(
                [
                    "bash",
                    "-lc",
                    # No maxdepth: scan all nested skills under /bash_tools.
                    "find /bash_tools -type f -name SKILL.md -print",
                ],
                timeout=60,
            )
            if code != 0 or not isinstance(out, str):
                return
            for line in out.splitlines():
                path = line.strip()
                if not path.startswith("/bash_tools/") or not path.endswith(
                    "/SKILL.md"
                ):
                    continue
                rel_dir = os.path.dirname(path)[len("/bash_tools/") :]
                if rel_dir:
                    skill_dirs.append(rel_dir)

        if not skill_dirs:
            return

        self.run_command_in_container(
            [
                "bash",
                "-lc",
                f"mkdir -p {shlex.quote(f'/shared/.aigise/skill_deps/{sandbox_type}')}",
            ],
            timeout=30,
        )

        for rel_skill_dir in sorted(set(skill_dirs)):
            skill_root = f"/bash_tools/{rel_skill_dir}"
            skill_md_path = f"{skill_root}/SKILL.md"

            try:
                skill_md = self.extract_file_from_container(skill_md_path)
            except Exception:  # pylint: disable=broad-except
                continue
            if not isinstance(skill_md, str) or not skill_md:
                continue

            exec_sandbox = _parse_should_run_in_sandbox(skill_md)
            if exec_sandbox != sandbox_type:
                continue

            marker = _marker_path(rel_skill_dir)
            _, already = self.run_command_in_container(
                ["bash", "-lc", f"test -f {shlex.quote(marker)}"],
                timeout=10,
            )
            if already == 0:
                continue

            installers = [
                f"{skill_root}/deps/{sandbox_type}/install.sh",
                f"{skill_root}/deps/install.sh",
            ]
            chosen = None
            for installer in installers:
                _, exists = self.run_command_in_container(
                    ["bash", "-lc", f"test -f {shlex.quote(installer)}"],
                    timeout=10,
                )
                if exists == 0:
                    chosen = installer
                    break
            if not chosen:
                continue

            logger.info(
                "Running skill deps installer for sandbox '%s': %s (skill=%s)",
                sandbox_type,
                chosen,
                rel_skill_dir,
            )
            msg, err = self.run_command_in_container(
                [
                    "bash",
                    "-lc",
                    f"chmod +x {shlex.quote(chosen)} && {shlex.quote(chosen)}",
                ],
                timeout=1800,
            )
            if err != 0:
                raise RuntimeError(
                    "Skill deps installer failed for sandbox '%s' skill '%s': %s"
                    % (sandbox_type, rel_skill_dir, msg)
                )

            self.run_command_in_container(
                ["bash", "-lc", f"touch {shlex.quote(marker)}"],
                timeout=10,
            )

    async def ensure_ready(self) -> None:
        from aigise.session import get_aigise_session
        from aigise.utils.agent_utils import get_mcp_url_from_session_id

        aigise_session = get_aigise_session(self.aigise_session_id)
        aigise_session.sandboxes._sandboxes[self.sandbox_type] = self

        async def verify_connection(url: str) -> bool:
            """Check if MCP SSE server is ready by establishing a real connection."""
            from mcp.client.sse import sse_client

            try:
                # Use real MCP client for proper connection and cleanup
                async with asyncio.timeout(10.0):
                    async with sse_client(url, timeout=5.0, sse_read_timeout=10.0) as (
                        read,
                        write,
                    ):
                        # Successfully connected and initialized
                        return True
            except Exception as e:
                logger.debug(f"MCP connection verify failed for {url}: {e}")
                return False

        try:
            url = get_mcp_url_from_session_id(self.sandbox_type, self.aigise_session_id)
            retry_num = 0
            logger.info(f"Waiting for MCP server {self.sandbox_type} at {url}...")

            while not await verify_connection(url):
                retry_num += 1
                logger.info(
                    f"Still waiting for {self.sandbox_type}... (retry {retry_num})"
                )
                await asyncio.sleep(1)

            logger.info(f"MCP server {self.sandbox_type} is ready!")
        except (RuntimeError, AttributeError):
            logger.debug(
                f"{self.sandbox_type} is not an MCP server, skipping connection check"
            )
        aigise_session.sandboxes.set_sandbox_state(
            self.sandbox_type, SandboxState.READY
        )
        logger.info(
            f"main environment successfully initialized for session {self.aigise_session_id}"
        )
