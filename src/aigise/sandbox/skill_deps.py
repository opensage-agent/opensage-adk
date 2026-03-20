"""Skill dependency preparation for bash_tools.

This module is intentionally sandbox-centric (operates on a BaseSandbox) and
does not depend on session managers to avoid import-time cycles.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
from typing import Any

from .base_sandbox import BaseSandbox

logger = logging.getLogger(__name__)


async def prepare_skill_deps(sandbox: BaseSandbox, enabled_skills: Any) -> None:
    """Run enabled per-skill dependency installers for a sandbox (best-effort).

        Convention:
        - A skill directory may include:
          - `deps/<sandbox_type>/install.sh` (sandbox-specific), and/or
          - `deps/install.sh` (generic)
        - A skill declares which sandbox should execute its deps installer via YAML
          frontmatter in `SKILL.md`:
            should_run_in_sandbox: <sandbox_type>

        Markers are written under:
          /shared/.opensage/skill_deps/<sandbox_type>/<skill>.done

        Args:
          sandbox (BaseSandbox): Sandbox instance with `/bash_tools` and `/shared` mounted.
          enabled_skills (Any): ToolLoader-style enabled_skills setting:
            - None: no skills enabled (skip)
            - list[str]: only scan those prefixes under /bash_tools
            - other: scan all skills under /bash_tools

    Raises:
      RuntimeError: Raised when this operation fails."""
    if enabled_skills is None:
        return

    sandbox_type = getattr(sandbox, "sandbox_type", None)
    if not sandbox_type:
        return

    def _marker_path(rel_skill_dir: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", rel_skill_dir.strip("/"))
        return f"/shared/.opensage/skill_deps/{sandbox_type}/{safe}.done"

    def _parse_should_run_in_sandbox(skill_md: str) -> str | None:
        if not skill_md.startswith("---"):
            return None
        parts = skill_md.split("---", 2)
        if len(parts) < 3:
            return None
        yaml_block = parts[1]
        match = re.search(r"^should_run_in_sandbox:\s*(.+)$", yaml_block, re.MULTILINE)
        if not match:
            return None
        val = match.group(1).strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1].strip()
        return val.lower() if val else None

    def _parse_priority(skill_md: str) -> float:
        default_priority = float("inf")
        try:
            match = re.search(r"^##\s+Priority\s*\n+\s*(\d+)", skill_md, re.MULTILINE)
            if match:
                return int(match.group(1))
        except Exception:  # pylint: disable=broad-except
            pass
        return default_priority

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

            root = f"/bash_tools/{entry}"

            # If entry itself is a skill dir (has SKILL.md), include it.
            _, has_skill_md = sandbox.run_command_in_container(
                ["bash", "-lc", f"test -f {shlex.quote(root + '/SKILL.md')}"],
                timeout=10,
            )
            if has_skill_md == 0:
                skill_dirs.append(entry)

            # Expand nested skills under this prefix.
            out, code = sandbox.run_command_in_container(
                [
                    "bash",
                    "-lc",
                    " && ".join(
                        [
                            f"test -d {shlex.quote(root)}",
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
        out, code = sandbox.run_command_in_container(
            ["bash", "-lc", "find /bash_tools -type f -name SKILL.md -print"],
            timeout=60,
        )
        if code != 0 or not isinstance(out, str):
            return
        for line in out.splitlines():
            path = line.strip()
            if not path.startswith("/bash_tools/") or not path.endswith("/SKILL.md"):
                continue
            rel_dir = os.path.dirname(path)[len("/bash_tools/") :]
            if rel_dir:
                skill_dirs.append(rel_dir)

    if not skill_dirs:
        return

    sandbox.run_command_in_container(
        [
            "bash",
            "-lc",
            f"mkdir -p {shlex.quote(f'/shared/.opensage/skill_deps/{sandbox_type}')}",
        ],
        timeout=30,
    )

    # Collect and parse all skills first.
    skills_to_install: list[dict[str, Any]] = []
    for rel_skill_dir in sorted(set(skill_dirs)):
        skill_root = f"/bash_tools/{rel_skill_dir}"
        skill_md_path = f"{skill_root}/SKILL.md"
        try:
            skill_md = sandbox.extract_file_from_container(skill_md_path)
        except Exception:  # pylint: disable=broad-except
            continue
        if not isinstance(skill_md, str) or not skill_md:
            continue

        exec_sandbox = _parse_should_run_in_sandbox(skill_md)
        if exec_sandbox != sandbox_type:
            continue

        skills_to_install.append(
            {
                "rel_skill_dir": rel_skill_dir,
                "skill_root": skill_root,
                "priority": _parse_priority(skill_md),
            }
        )

    skills_to_install.sort(key=lambda x: (x["priority"], x["rel_skill_dir"]))

    for skill_info in skills_to_install:
        rel_skill_dir = skill_info["rel_skill_dir"]
        skill_root = skill_info["skill_root"]
        marker = _marker_path(rel_skill_dir)

        _, already = sandbox.run_command_in_container(
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
            _, exists = sandbox.run_command_in_container(
                ["bash", "-lc", f"test -f {shlex.quote(installer)}"],
                timeout=10,
            )
            if exists == 0:
                chosen = installer
                break
        if not chosen:
            continue

        logger.info(
            "Running skill deps installer for sandbox '%s': %s (skill=%s, priority=%s)",
            sandbox_type,
            chosen,
            rel_skill_dir,
            skill_info["priority"],
        )
        msg, err = sandbox.run_command_in_container(
            ["bash", "-lc", f"chmod +x {shlex.quote(chosen)} && {shlex.quote(chosen)}"],
            timeout=1800,
        )
        if err != 0:
            raise RuntimeError(
                "Skill deps installer failed for sandbox '%s' skill '%s': %s"
                % (sandbox_type, rel_skill_dir, msg)
            )

        sandbox.run_command_in_container(
            ["bash", "-lc", f"touch {shlex.quote(marker)}"],
            timeout=10,
        )
