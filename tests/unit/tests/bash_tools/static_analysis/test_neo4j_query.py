"""Unit tests for neo4j-query bash tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from opensage.session import OpenSageSession, get_opensage_session
from opensage.toolbox.general.bash_tools_interface import run_terminal_command
from opensage.utils.project_info import PROJECT_PATH
from tests.unit.utils.utils import fix_neo4j_client

# Increase timeout for slow static analysis tests
pytestmark = pytest.mark.timeout(1200)


@pytest_asyncio.fixture(scope="module")
async def opensage_session():
    """Create opensage session for testing static analysis tools."""
    opensage_session = None
    try:
        opensage_session = get_opensage_session(
            "test-bash-tools-static-analysis-neo4j-query",
            str(PROJECT_PATH / "tests/unit/data/configs/test_cpg.toml"),
        )

        opensage_session.sandboxes.initialize_shared_volumes()
        await opensage_session.sandboxes.launch_all_sandboxes()
        await opensage_session.sandboxes.initialize_all_sandboxes()
        yield opensage_session
    finally:
        if opensage_session is not None:
            opensage_session.cleanup()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_neo4j_query_basic(opensage_session: OpenSageSession):
    """Test neo4j-query tool with basic query."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}
    fix_neo4j_client(opensage_session, "analysis")

    # Test with a simple query
    query = "MATCH (n) RETURN count(n) AS count LIMIT 1"
    result = run_terminal_command(
        command=f'python3 /bash_tools/neo4j/neo4j-query/scripts/neo4j_query.py "{query}"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    output = result["output"]
    if isinstance(output, str):
        output = json.loads(output)

    assert isinstance(output, dict)
    assert "records" in output
    assert isinstance(output["records"], list)
    assert len(output["records"]) == 1
    assert "count" in output["records"][0]


@pytest.mark.slow
@pytest.mark.asyncio
async def test_neo4j_query_with_params(opensage_session: OpenSageSession):
    """Test neo4j-query tool with query parameters."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}
    fix_neo4j_client(opensage_session, "analysis")

    # Test with query and parameters (may return no results, which is OK)
    query = "MATCH (n) RETURN count(n) AS count LIMIT 1"
    params = "{}"
    result = run_terminal_command(
        command=f"python3 /bash_tools/neo4j/neo4j-query/scripts/neo4j_query.py \"{query}\" --params '{params}'",
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    output = result["output"]
    if isinstance(output, str):
        output = json.loads(output)

    assert isinstance(output, dict)
    assert "records" in output
    assert isinstance(output["records"], list)
    assert len(output["records"]) == 1
    assert "count" in output["records"][0]


@pytest.mark.slow
@pytest.mark.asyncio
async def test_neo4j_query_invalid_query(opensage_session: OpenSageSession):
    """Test neo4j-query tool with invalid query."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}
    fix_neo4j_client(opensage_session, "analysis")

    # Test with invalid query syntax
    query = "INVALID CYPHER QUERY SYNTAX !!!"
    result = run_terminal_command(
        command=f'python3 /bash_tools/neo4j/neo4j-query/scripts/neo4j_query.py "{query}"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    output = result["output"]
    if isinstance(output, str):
        output = json.loads(output)

    assert result["success"] is False
    assert result["exit_code"] != 0
    assert isinstance(output, dict)
    assert output.get("records") == []
    assert isinstance(output.get("error", ""), str)
    assert output.get("error")
