"""Tests for fuzzy_search tool (OpenSage trajectory search)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from opensage.toolbox.general.fuzzy_search import (
    fuzzy_search,
    fuzzy_search_preview,
)


def _ctx():
    return MagicMock()


def _proc(stdout="", stderr="", returncode=0):
    p = MagicMock()
    p.stdout = stdout
    p.stderr = stderr
    p.returncode = returncode
    return p


_W = "opensage.toolbox.general.fuzzy_search.shutil.which"
_R = "opensage.toolbox.general.fuzzy_search.subprocess.run"


# ---------------------------------------------------------------------------
# fuzzy_search
# ---------------------------------------------------------------------------


class TestFuzzySearch:
    def test_ase_not_found(self):
        with patch(_W, return_value=None):
            r = fuzzy_search("query", _ctx())
        assert r["success"] is False
        assert "not found" in r["error"]

    def test_returns_results(self):
        out = "\n".join(
            json.dumps(s)
            for s in [
                {"id": "s1", "title": "fix bug", "turns": 5},
                {"id": "s2", "title": "sandbox test", "turns": 12},
            ]
        )
        with (
            patch(_W, return_value="/usr/bin/ase"),
            patch(_R, return_value=_proc(stdout=out)) as mock,
        ):
            r = fuzzy_search("bug", _ctx())

        assert r["success"] is True
        assert r["total"] == 2
        assert r["sessions"][0]["id"] == "s1"
        # Must always filter by opensage
        args = mock.call_args[0][0]
        idx = args.index("--agent")
        assert args[idx + 1] == "opensage"

    def test_default_directory(self):
        """Default directory should be the host session root."""
        with (
            patch(_W, return_value="/usr/bin/ase"),
            patch(_R, return_value=_proc()) as mock,
        ):
            fuzzy_search("q", _ctx())
        args = mock.call_args[0][0]
        idx = args.index("--directory")
        assert ".local/opensage/sessions" in args[idx + 1]

    def test_custom_directory(self):
        with (
            patch(_W, return_value="/usr/bin/ase"),
            patch(_R, return_value=_proc()) as mock,
        ):
            fuzzy_search("q", _ctx(), directory="/evals")
        args = mock.call_args[0][0]
        assert args[args.index("--directory") + 1] == "/evals"

    def test_empty_directory_searches_all(self):
        with (
            patch(_W, return_value="/usr/bin/ase"),
            patch(_R, return_value=_proc()) as mock,
        ):
            fuzzy_search("q", _ctx(), directory="")
        args = mock.call_args[0][0]
        assert "--directory" not in args

    def test_max_results(self):
        out = "\n".join(json.dumps({"id": f"s{i}"}) for i in range(30))
        with (
            patch(_W, return_value="/usr/bin/ase"),
            patch(_R, return_value=_proc(stdout=out)),
        ):
            r = fuzzy_search("x", _ctx(), max_results=5)
        assert r["total"] == 30
        assert r["returned"] == 5

    def test_empty(self):
        with (
            patch(_W, return_value="/usr/bin/ase"),
            patch(_R, return_value=_proc()),
        ):
            r = fuzzy_search("nope", _ctx())
        assert r["success"] is True
        assert r["total"] == 0

    def test_ase_error(self):
        with (
            patch(_W, return_value="/usr/bin/ase"),
            patch(_R, return_value=_proc(returncode=1, stderr="bad")),
        ):
            r = fuzzy_search("x", _ctx())
        assert r["success"] is False

    def test_timeout(self):
        import subprocess as sp

        with (
            patch(_W, return_value="/usr/bin/ase"),
            patch(_R, side_effect=sp.TimeoutExpired("ase", 30)),
        ):
            r = fuzzy_search("x", _ctx())
        assert "timed out" in r["error"]

    def test_malformed_json_skipped(self):
        out = '{"id":"s1"}\nnot json\n'
        with (
            patch(_W, return_value="/usr/bin/ase"),
            patch(_R, return_value=_proc(stdout=out)),
        ):
            r = fuzzy_search("x", _ctx())
        assert r["total"] == 1

    def test_empty_query_no_trailing_arg(self):
        with (
            patch(_W, return_value="/usr/bin/ase"),
            patch(_R, return_value=_proc()) as mock,
        ):
            fuzzy_search("", _ctx())
        assert mock.call_args[0][0][-1] != ""


# ---------------------------------------------------------------------------
# fuzzy_search_preview
# ---------------------------------------------------------------------------


class TestFuzzySearchPreview:
    def test_ase_not_found(self):
        with patch(_W, return_value=None):
            r = fuzzy_search_preview("s1", _ctx())
        assert r["success"] is False

    def test_full_content(self):
        content = "line1\nline2\nline3\nline4\nline5"
        with (
            patch(_W, return_value="/usr/bin/ase"),
            patch(_R, return_value=_proc(stdout=content)),
        ):
            r = fuzzy_search_preview("s1", _ctx())
        assert r["success"] is True
        assert r["total_lines"] == 5
        assert r["start_line"] == 1
        assert r["end_line"] == 5
        assert r["truncated"] is False
        assert "line1" in r["content"]
        assert "line5" in r["content"]

    def test_default_caps_at_200_lines(self):
        """Default preview without bounds caps at 200 lines."""
        content = "\n".join(f"L{i}" for i in range(1, 501))  # 500 lines
        with (
            patch(_W, return_value="/usr/bin/ase"),
            patch(_R, return_value=_proc(stdout=content)),
        ):
            r = fuzzy_search_preview("s1", _ctx())
        assert r["total_lines"] == 500
        assert r["start_line"] == 1
        assert r["end_line"] == 200
        assert r["truncated"] is True
        assert "L200" in r["content"]
        assert "L201" not in r["content"]
        # Content has 200 lines
        assert len(r["content"].splitlines()) == 200

    def test_line_range(self):
        content = "\n".join(f"line{i}" for i in range(1, 11))  # 10 lines
        with (
            patch(_W, return_value="/usr/bin/ase"),
            patch(_R, return_value=_proc(stdout=content)),
        ):
            r = fuzzy_search_preview("s1", _ctx(), start_line=3, end_line=5)
        assert r["total_lines"] == 10
        assert r["start_line"] == 3
        assert r["end_line"] == 5
        assert "line3" in r["content"]
        assert "line5" in r["content"]
        assert "line2" not in r["content"]
        assert "line6" not in r["content"]

    def test_start_only(self):
        content = "\n".join(f"L{i}" for i in range(1, 6))
        with (
            patch(_W, return_value="/usr/bin/ase"),
            patch(_R, return_value=_proc(stdout=content)),
        ):
            r = fuzzy_search_preview("s1", _ctx(), start_line=4)
        assert r["start_line"] == 4
        assert r["end_line"] == 5
        assert "L4" in r["content"]
        assert "L3" not in r["content"]

    def test_end_only(self):
        content = "\n".join(f"L{i}" for i in range(1, 6))
        with (
            patch(_W, return_value="/usr/bin/ase"),
            patch(_R, return_value=_proc(stdout=content)),
        ):
            r = fuzzy_search_preview("s1", _ctx(), end_line=2)
        assert r["start_line"] == 1
        assert r["end_line"] == 2
        assert "L1" in r["content"]
        assert "L2" in r["content"]
        assert "L3" not in r["content"]

    def test_not_found(self):
        with (
            patch(_W, return_value="/usr/bin/ase"),
            patch(_R, return_value=_proc(returncode=1, stderr="not found")),
        ):
            r = fuzzy_search_preview("nope", _ctx())
        assert r["success"] is False

    def test_timeout(self):
        import subprocess as sp

        with (
            patch(_W, return_value="/usr/bin/ase"),
            patch(_R, side_effect=sp.TimeoutExpired("ase", 30)),
        ):
            r = fuzzy_search_preview("s1", _ctx())
        assert "timed out" in r["error"]
