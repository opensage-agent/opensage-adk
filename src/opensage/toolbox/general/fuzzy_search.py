"""Fuzzy search tool — search OpenSage agent trajectory history.

Two tools:
- ``fuzzy_search``: find sessions by keyword via ``ase --agent opensage``
- ``fuzzy_search_preview``: read the content of a session (with line range)

Trajectory storage locations (both indexed by ``ase`` opensage adapter):
- Host sessions: ``~/.local/opensage/sessions/``
- Eval outputs:   ``<project>/evals/`` (configure via ase config.toml)
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)

_ASE_BIN = "ase"
_MAX_RESULTS = 20
_DEFAULT_PREVIEW_LINES = 200

# Default directories where OpenSage writes trajectories
_HOST_SESSION_DIR = str(Path.home() / ".local" / "opensage" / "sessions")


def fuzzy_search(
    query: str,
    tool_context: ToolContext,
    directory: str = _HOST_SESSION_DIR,
    max_results: int = _MAX_RESULTS,
) -> dict:
    """Search OpenSage agent trajectory history.

    Full-text fuzzy search across all indexed OpenSage evaluation
    trajectories and sessions. Use fuzzy_search_preview() to read
    the content of a result.

    Args:
        query: Search query (e.g. 'sandbox timeout', 'CodeQL init failure').
        directory: Filter by directory path (substring match). Defaults to
            ``~/.local/opensage/sessions/``. Pass empty string to search all
            indexed OpenSage sessions regardless of directory.
        max_results: Maximum number of results to return.
    Returns:
        dict with matching sessions (id, title, directory, timestamp, turns).
    """
    ase_path = shutil.which(_ASE_BIN)
    if not ase_path:
        return {
            "success": False,
            "error": (
                f"'{_ASE_BIN}' not found on PATH. Install: cargo install agents-sesame"
            ),
        }

    cmd = [ase_path, "--list", "--no-tui", "--format", "json", "--agent", "opensage"]
    if directory:
        cmd.extend(["--directory", directory])
    if query:
        cmd.append(query)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "ase search timed out after 30s"}
    except Exception as e:
        return {"success": False, "error": str(e)}

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        return {"success": False, "error": f"ase exited {proc.returncode}: {stderr}"}

    sessions = []
    for line in proc.stdout.strip().splitlines():
        if not line:
            continue
        try:
            sessions.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    total = len(sessions)
    sessions = sessions[:max_results]

    return {
        "success": True,
        "total": total,
        "returned": len(sessions),
        "sessions": sessions,
    }


def fuzzy_search_preview(
    session_id: str,
    tool_context: ToolContext,
    start_line: int = 0,
    end_line: int = 0,
) -> dict:
    """Read the content of an OpenSage trajectory session.

    With no line range, returns the first 200 lines (the default cap to
    avoid flooding the agent context). Use start_line/end_line to
    paginate through longer sessions — the response includes
    ``total_lines`` so you know how much more is available.

    Args:
        session_id: Session ID from fuzzy_search results.
        start_line: First line to return (1-based, inclusive). 0 = from start.
        end_line: Last line to return (1-based, inclusive). 0 = default cap
            (200 lines from start_line) when start_line is also 0; otherwise
            read to end of session.
    Returns:
        dict with content, total_lines, and the line range returned.
    """
    ase_path = shutil.which(_ASE_BIN)
    if not ase_path:
        return {"success": False, "error": f"'{_ASE_BIN}' not found on PATH."}

    try:
        proc = subprocess.run(
            [ase_path, "--preview", session_id],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "ase preview timed out after 30s"}
    except Exception as e:
        return {"success": False, "error": str(e)}

    if proc.returncode != 0:
        return {
            "success": False,
            "error": f"ase exited {proc.returncode}: {proc.stderr.strip()}",
        }

    lines = proc.stdout.splitlines()
    total_lines = len(lines)

    # Apply line range (1-based, inclusive). When neither bound is given,
    # cap at _DEFAULT_PREVIEW_LINES so the agent isn't flooded with the
    # full trajectory.
    s = max(0, start_line - 1) if start_line > 0 else 0
    if end_line > 0:
        e = end_line
    elif start_line == 0:
        e = min(total_lines, _DEFAULT_PREVIEW_LINES)
    else:
        e = total_lines
    selected = lines[s:e]
    truncated = e < total_lines

    return {
        "success": True,
        "session_id": session_id,
        "total_lines": total_lines,
        "start_line": s + 1,
        "end_line": min(e, total_lines),
        "truncated": truncated,
        "content": "\n".join(selected),
    }
