"""MkDocs hook: generate CLI reference pages for OpenSage.

This keeps the docs site in sync with the actual CLI help text without manual
copy/paste.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_LOG_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+\|")


def _extract_help_text(raw: str) -> str:
    """Extract the CLI help text, removing runtime log lines."""
    lines = []
    started = False
    for line in raw.splitlines():
        if not started:
            if line.startswith("Usage:"):
                started = True
            else:
                continue
        if _LOG_PREFIX_RE.match(line):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip() + "\n"


def _run_help(repo_root: Path, *args: str) -> str:
    """Run `opensage ... --help` via uv."""
    cmd = [
        "uv",
        "run",
        "opensage",
        *args,
        "--help",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        check=False,
    )
    # Some commands may emit logs to stdout/stderr; keep stdout as primary.
    raw = proc.stdout or ""
    return _extract_help_text(raw)


def _write_page(path: Path, title: str, help_text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"# {title}\n\n```text\n{help_text}```\n"
    path.write_text(content, encoding="utf-8")


def _annotate_under_development(help_text: str) -> str:
    """Patch CLI help text to reflect doc status notes.

    We intentionally keep the generated CLI pages aligned with actual `--help`
    output, but we also need a small number of documentation-only notes (e.g.
    marking k8s as under development).
    """
    help_text = re.sub(r"\bOpenSage\b", "OpenSage", help_text)
    help_text = re.sub(r"\bopensage\b", "opensage", help_text)
    help_text = help_text.replace(
        "Kubernetes sandbox backend",
        "Kubernetes sandbox backend (under development)",
    )
    return _format_dependency_check_paragraph(help_text)


def _format_dependency_check_paragraph(help_text: str) -> str:
    """Pretty-print dependency-check description bullets.

    Some CLI help output uses an inline list like:
      "Checks for manually installed dependencies: - CodeQL: ... - Docker: ..."
    which renders poorly in MkDocs. This rewrites that paragraph into
    multi-line bullets, without changing the actual CLI behavior.
    """
    lines = help_text.splitlines()
    out: list[str] = []
    i = 0

    def _is_stop_line(line: str) -> bool:
        return (
            (not line.strip())
            or line.startswith("Options:")
            or line.startswith("Commands:")
        )

    while i < len(lines):
        line = lines[i]
        if "Checks for manually installed dependencies:" not in line:
            out.append(line)
            i += 1
            continue

        # Collect this paragraph (may wrap across multiple lines).
        para_lines = [line]
        j = i + 1
        while j < len(lines) and not _is_stop_line(lines[j]):
            para_lines.append(lines[j])
            j += 1

        para = " ".join(l.strip() for l in para_lines)
        para = re.sub(r"\s+", " ", para).strip()

        prefix, _, rest = para.partition(":")
        rest = rest.strip()

        out.append(f"{prefix}:")
        if rest:
            # Split on the inline bullet delimiter.
            parts = [p.strip() for p in rest.split(" - ") if p.strip()]
            for p in parts:
                # Preserve any leading dash already present.
                p = p[2:].strip() if p.startswith("- ") else p
                out.append(f"  - {p}")

        i = j
        continue

    return "\n".join(out) + ("\n" if help_text.endswith("\n") else "")


def on_startup(command: str, dirty: bool) -> None:
    del dirty  # unused

    # This file lives under docs/hooks/, so parents[2] is the repo root.
    repo_root = Path(__file__).resolve().parents[2]
    docs_dir = repo_root / "docs" / "wiki"
    out_dir = docs_dir / "generated" / "cli"

    # Only generate for build/serve to avoid surprising behavior.
    if command not in {"build", "serve", "gh-deploy"}:
        return

    _write_page(
        out_dir / "opensage.md",
        "opensage",
        _annotate_under_development(_run_help(repo_root)),
    )
    _write_page(
        out_dir / "opensage-web.md",
        "opensage web",
        _annotate_under_development(_run_help(repo_root, "web")),
    )
    _write_page(
        out_dir / "opensage-dependency-check.md",
        "opensage dependency-check",
        _annotate_under_development(_run_help(repo_root, "dependency-check")),
    )
